import json

import frappe
from frappe import _
from frappe.model.document import Document

from mallet_estimator import opencutlist, estimate_pdf, inventory, views_pdf, decor
from mallet_estimator.estimator import (
    STEP_TEMPLATE, OPERATION_STANDARDS, OPERATION_WORKSTATION, calc_sku, sku_code,
    customer_initials, op_phase, live_workstation_rates,
    DESIGN_STEP_TEMPLATE, DESIGN_STANDARDS,
)

DEFAULT_WORKSTATION = "Assembly Station"


def default_workstation(row):
    return OPERATION_WORKSTATION.get(op_phase(row), DEFAULT_WORKSTATION)


def operation_defaults(op_name):
    """(min_per_unit, workstation) for an Operation — read from the Operation
    master (single source of truth: Total Operation Time + Default Workstation),
    falling back to the code standards when the master has none."""
    mins, ws = 0, None
    if op_name and frappe.db.exists("Operation", op_name):
        meta = frappe.get_meta("Operation")
        if meta.has_field("mallet_min_per_unit"):
            mins = frappe.db.get_value("Operation", op_name, "mallet_min_per_unit") or 0
        ws = frappe.db.get_value("Operation", op_name, "workstation")
    if not mins:
        mins = OPERATION_STANDARDS.get(op_name, {}).get("min_per_unit", 0) \
            or DESIGN_STANDARDS.get(op_name, {}).get("min_per_unit", 0)
    if not ws:
        ws = OPERATION_WORKSTATION.get(op_name)
    return mins, ws


MARGIN_FIELDS = ("material", "labor", "overhead", "design")


@frappe.whitelist()
def get_margins():
    """The four margin (markup) percentages from Estimate Settings — the text
    boxes the user tunes to decide how much to make on each total."""
    s = frappe.get_single("Estimate Settings")
    return {f: float(s.get(f"markup_{f}") or 0) for f in MARGIN_FIELDS}


@frappe.whitelist()
def set_margins(material=None, labor=None, overhead=None, design=None):
    """Write the margin percentages back to Estimate Settings (values live in
    the DB only — repo defaults stay 0, they are business-sensitive). Every
    open/save reprices from these, so the change takes effect immediately."""
    s = frappe.get_single("Estimate Settings")
    for f, v in (("material", material), ("labor", labor),
                 ("overhead", overhead), ("design", design)):
        if v is not None and v != "":
            s.set(f"markup_{f}", float(v))
    s.flags.ignore_permissions = True
    s.save()
    return get_margins()


def build_bifurcation(amounts, gst_pct=18.0):
    """The Material / Labor / Design / Overhead / Transport / Taxes line items
    (client side): [{label, amount, pct (of pre-tax total), gst, gross}] +
    pre-tax / taxes / grand-total rows. Transport stays its own line — it is
    the shared cost across SKUs, recovered at cost."""
    rows = [
        ("Material (incl. joinery consumables)", amounts.get("client_material") or 0),
        ("Labor (carpentry wages)", amounts.get("client_labor") or 0),
        ("Design", amounts.get("client_design") or 0),
        ("Factory Overhead", amounts.get("client_overhead") or 0),
        ("Transport (shared across SKUs, at cost)", amounts.get("transport") or 0),
    ]
    pre_tax = sum(a for _, a in rows)
    out = [{
        "label": label, "amount": amt,
        "pct": (amt / pre_tax * 100.0) if pre_tax else 0,
        "gst": amt * gst_pct / 100.0,
        "gross": amt * (1 + gst_pct / 100.0),
    } for label, amt in rows]
    taxes = sum(r["gst"] for r in out)
    return {
        "rows": out, "pre_tax": pre_tax, "taxes": taxes,
        "grand_total": pre_tax + taxes, "gst_pct": gst_pct,
    }


def get_default_item_group():
    return (
        frappe.db.get_single_value("Stock Settings", "item_group")
        or ("Products" if frappe.db.exists("Item Group", "Products") else None)
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )


class EstimateSKU(Document):
    ATTACH_FIELDS = ("estimate_pdf", "partlist_pdf", "views_pdf", "parts_csv", "article_image")

    def before_validate(self):
        """A file removed from the Attachments sidebar leaves the attach field
        pointing at nothing, and core validation then blocks EVERY save with
        'Uploaded file not found'. Silently drop dangling references instead."""
        for f in self.ATTACH_FIELDS:
            url = self.get(f)
            if url and not frappe.db.exists("File", {"file_url": url}):
                self.set(f, None)

    def validate(self):
        self.wipe_on_cleared_files()
        self.ensure_steps()
        self.ensure_design_steps()
        self.ensure_step_remarks()
        self.maybe_import()
        self.apply_decor_map()
        self.refresh_material_rates()
        self.compute_code()
        self.enforce_locked_qty()
        self.derive_joinery()
        self.compute_costs()
        self.build_cost_breakup()

    def wipe_on_cleared_files(self):
        """Clearing a source PDF wipes what was derived from it, so the SKU never
        carries stale imported data."""
        if not self.is_new() and self.has_value_changed("estimate_pdf") and not self.estimate_pdf:
            self.set("materials", [])
            self.set("joinery_items", [])
            self.set("parts", [])
            self.unpriced_materials = ""
            # no design (estimate PDF) → no billable design work
            for row in self.get("design_labor") or []:
                row.qty = 0
            self.import_drivers = ""
        if not self.is_new() and self.has_value_changed("views_pdf") and not self.views_pdf:
            self.article_image = None
            if self.meta.has_field("views_images"):
                self.views_images = ""

    def on_update(self):
        if self.create_item:
            self.sync_item()
        self.refresh_project_estimates()
        self._gc_orphan_files()

    def _gc_orphan_files(self):
        """B8 — removing a file from an attach field also removes it from the
        Attachments sidebar: delete File docs attached to this SKU whose URL no
        attach field references any more (repeat ISO extracts included)."""
        keep = {self.get(f) for f in self.ATTACH_FIELDS if self.get(f)}
        try:
            keep |= set((json.loads(self.get("views_images") or "{}")).values())
        except Exception:
            pass
        for fd in frappe.get_all(
            "File", filters={"attached_to_doctype": self.doctype, "attached_to_name": self.name},
            fields=["name", "file_url"],
        ):
            if fd.file_url not in keep:
                try:
                    frappe.delete_doc("File", fd.name, force=True, ignore_permissions=True)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), f"gc file {fd.name}")

    @frappe.whitelist()
    def reset_files(self):
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        """Start over: remove EVERY attached file (and its File docs) plus all
        data derived from them — materials, joinery, parts, execution design,
        drivers. Identity, labor/design step templates and settings stay."""
        for f in self.ATTACH_FIELDS:
            self.set(f, None)
        # Labor + design steps wipe too — ensure_steps re-seeds the fresh
        # 17-step / 7-step templates during the save below.
        for table in ("materials", "joinery_items", "parts", "execution_materials",
                      "labor", "design_labor", "sku_decors", "sku_decor_edges"):
            if self.meta.has_field(table):
                self.set(table, [])
        self.unpriced_materials = ""
        self.import_drivers = ""
        self.save(ignore_permissions=True)  # on_update GC deletes the File docs
        return {"ok": True}

    def on_trash(self):
        """Delink from DRAFT estimates so the SKU can actually be deleted (a
        submitted estimate still blocks — amend it first)."""
        for name in frappe.get_all(
            "Execution Estimate SKU", filters={"estimate_sku": self.name}, pluck="parent", distinct=True
        ):
            if frappe.db.get_value("Estimate", name, "docstatus") == 0:
                est = frappe.get_doc("Estimate", name)
                est.set("skus", [r for r in est.skus if r.estimate_sku != self.name])
                est.save(ignore_permissions=True)

    def ensure_step_remarks(self):
        """Seed each step's Remarks with what it is supposed to take care of
        (glue both sides on lamination, screws in Drilling, what to assemble /
        dismantle, …). Only fills EMPTY remarks — user edits always stick."""
        from mallet_estimator.estimator import STEP_REMARKS
        for row in self.labor or []:
            if not (row.remark or "").strip():
                row.remark = STEP_REMARKS.get(op_phase(row), "")

    def _frozen(self):
        return bool(self.get("rates_frozen"))

    def maybe_import(self):
        """When an estimation input PDF is attached (or changed), import the
        material quantities + operation quantities automatically on save — no
        button. The Parts CSV, if attached, gives the edge-banding part count and
        the QR part list."""
        self.maybe_extract_iso()
        if self._frozen():
            return  # quoted — rates are locked, no re-imports
        if not self.estimate_pdf:
            return
        if self.materials and not self.has_value_changed("estimate_pdf") \
                and not self.has_value_changed("partlist_pdf") and not self.has_value_changed("parts_csv"):
            return
        self.do_import()

    def maybe_extract_iso(self):
        """When the 7 Views PDF is attached (or changed), pull the render off its
        'IsoView' page into Article Image — the isometric shown to the client."""
        if not self.views_pdf or self.is_new():
            return
        if self.article_image and not self.has_value_changed("views_pdf"):
            return
        try:
            content = _file_content(self.views_pdf)
            # Outer W/D/H from the views' dimension callouts — fills empty fields
            # only (a keyed-in value always wins) and gets stamped onto the image.
            dims = {}
            try:
                dims = views_pdf.extract_outer_dims(content)
                for field, key in (("outer_w", "w"), ("outer_d", "d"), ("outer_h", "h")):
                    if dims.get(key) and not (self.get(field) or 0):
                        self.set(field, dims[key])
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"dims extract {self.name}")
            # Plain render only — no annotation on the image (dims live in fields).
            url = views_pdf.attach_iso_image(self, content)
            if url:
                self.article_image = url
            else:
                frappe.msgprint(_("No 'IsoView' page found in the 7 Views PDF — attach an Article Image manually."),
                                indicator="orange")
            if not all((self.outer_w, self.outer_d, self.outer_h)) \
                    and not all(dims.get(k) for k in ("w", "d", "h")):
                frappe.msgprint(_("Outer dimensions could not be read from the 7 Views PDF — key in Outer Width/Depth/Height."),
                                indicator="orange")
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"iso extract {self.name}")
            frappe.msgprint(_("Could not extract the IsoView render from the 7 Views PDF."), indicator="orange")

    def do_import(self):
        settings = frappe.get_single("Estimate Settings")
        materials = estimate_pdf.parse_estimate_pdf(estimate_pdf.read_pdf_text(_file_content(self.estimate_pdf)))
        if not materials:
            frappe.throw(_("No materials found in the Estimate PDF. Is it an OpenCutList Estimate export?"))

        # ESTIMATION inputs: the Material Estimate PDF gives sheets/laminate/edge
        # quantities; the PART LIST PDF identifies hardware CORRECTLY — the real
        # stock item designation (HWD_AH_SC_0), where the estimate PDF only knows
        # the group (HWD_Hinge). The Parts CSV stays an EXECUTION input: it only
        # fills the parts table + part count driving operation quantities.
        part_count, parts = 0, []
        if self.parts_csv:
            content = _file_content(self.parts_csv)
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            rows = opencutlist.parse_opencutlist_csv(content)
            parts = opencutlist.parts_list(rows)
            part_count = len(parts)

        hardware, pl_edges = [], []
        if self.partlist_pdf:
            pl_content = _file_content(self.partlist_pdf)
            try:
                hardware = views_pdf.parse_partlist_hardware(pl_content)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"partlist parse {self.name}")
                frappe.msgprint(_("Could not parse hardware from the Part List PDF — using the estimate PDF's generic hardware."),
                                indicator="orange")
            try:
                pl_edges = views_pdf.parse_partlist_edges(pl_content)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"partlist edges {self.name}")

        # Manually added rows (extra hardware like a bed hydraulic lift) survive
        # every re-import — only imported rows are rebuilt.
        manual_rows = [m.as_dict() for m in (self.materials or []) if m.get("is_manual")]
        # S9v2 — REAL laminates straight on the lines: parse the material
        # Descriptions (est + part list) into an SKU-wide slot→décor map; the
        # placeholder's trailing letters get replaced by the first letter's décor
        # short code (SG_LAM_V1_16mm_b_a + b=Virgo Mica 6534 → SG_LAM_V1_16mm_VM6534).
        # The DÉCOR MAP TABLE is the single source of truth for what each slot
        # letter means (control it in the ERP — no need to model every real
        # laminate in SketchUp). OCL description blocks are only used to PREFILL
        # table rows that don't exist yet; substitution always reads the table.
        try:
            pl_text = views_pdf._pdf_text(pl_content) if self.partlist_pdf else ""
            est_text = estimate_pdf.read_pdf_text(_file_content(self.estimate_pdf))
            brands = frappe.get_all("Manufacturer", pluck="name")
            existing = {("Laminate" if (r.domain or "Laminate") != "Edge Band" else "Edge Band",
                         (r.slot or "").strip().lower())
                        for r in (self.get("sku_decors") or [])}
            existing |= {("Edge Band", (r.slot or "").strip().lower())
                         for r in (self.get("sku_decor_edges") or [])}
            # edge-band physicals follow the ply: <=18 mm ply → 22 x 0.8 mm band,
            # thicker ply → 50 x 1 mm
            ply_max = max([float(m.get("thickness") or 0) for m in materials
                           if m.get("kind") == "sheet"] or [16])
            eb_thick, eb_wide = (1.0, 50.0) if ply_max > 18 else (0.8, 22.0)
            for d in decor.extract_slot_map(est_text + "\n" + pl_text, brands):
                ph = str(d["placeholder"])
                dom = "Edge Band" if ph.startswith("EB_") else "Laminate"
                # A description block that describes the placeholder's DECIDING
                # letter maps to its slot INSTANCE (b on ..._b_a1 → row b1 —
                # SketchUp's paste-rename suffix namespaces the whole material);
                # other blocks (the non-deciding side) fill their plain letter.
                first = (decor.trailing_slots(ph) or [""])[0][:1]
                slot = decor.slot_key(ph) if d["slot"] == first else d["slot"]
                key = (dom, slot)
                if not slot or key in existing or not (d.get("brand") or d.get("catalogue")):
                    continue
                if dom == "Edge Band":
                    self.append("sku_decor_edges", {
                        "slot": slot, "brand": d.get("brand"), "code": d.get("catalogue"),
                        "decor_name": d.get("name"), "year": d.get("year"), "short": d.get("short"),
                        "thickness": eb_thick, "width": eb_wide,
                    })
                else:
                    self.append("sku_decors", {
                        "slot": slot, "domain": dom, "brand": d.get("brand"),
                        "code": d.get("catalogue"), "decor_name": d.get("name"),
                        "year": d.get("year"), "short": d.get("short"),
                    })
                existing.add(key)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"decor parse {self.name}")
        lam_decors, edge_decors = self._decor_maps_from_table()
        lam_shorts = {k: decor.short_code(v) for k, v in lam_decors.items() if decor.short_code(v)}
        edge_shorts = {k: decor.short_code(v) for k, v in edge_decors.items() if decor.short_code(v)}

        self.set("materials", [])
        unpriced = []
        deferred_hw = []
        # Sheets, laminate and edge banding come from the estimate PDF (its nesting
        # is authoritative); hardware comes from the part list designations when
        # attached (falling back to the PDF's generic groups).
        for m in materials:
            if m.get("kind") == "laminate":
                real, letter = decor.substitute_real_code(m["name"], lam_shorts)
                meta = lam_decors.get(letter) if letter else None
                self._add_material_line(
                    m["name"], "laminate", m.get("thickness") or 0, m["qty"] or 0,
                    (f"{real} — real laminate for {m['name']}" if letter else _pdf_desc(m)),
                    unpriced, decor_meta=meta, real_code=real if letter else None,
                )
                continue
            if m.get("kind") == "hardware" and hardware:
                # Preferred source is the part list; but NEVER silently drop an
                # estimate-PDF hardware the part-list parse didn't cover — decide
                # after the designation lines are in (fixes: new hardware in the
                # estimate PDF missing from the SKU).
                deferred_hw.append(m)
                continue
            qty = m["qty"] or 0
            desc = _pdf_desc(m)
            if m.get("kind") == "edge":
                if pl_edges:
                    continue  # authoritative rows come from the part list below
                # Fallback (no part list): the estimate PDF's roll count. Bought
                # AND charged in whole ROLLS (50 m each) at per-meter rate x 50.
                real_eb, eb_ltr = decor.substitute_real_code(m["name"], edge_shorts)
                desc = f"{desc} — whole roll(s) of {inventory.EDGE_ROLL_METERS:g} m"[:140]
                self._add_material_line(
                    m["name"], "edge", m.get("thickness") or 0, qty, desc, unpriced,
                    uom="Roll", rate_factor=inventory.EDGE_ROLL_METERS,
                    decor_meta=edge_decors.get(eb_ltr) if eb_ltr else None,
                    real_code=real_eb if eb_ltr else None,
                )
                continue
            self._add_material_line(
                m["name"], m.get("kind"), m.get("thickness") or 0, qty,
                desc, unpriced,
            )
        # Edge banding from the PART LIST Summary (authoritative: real banding
        # meters; the estimate PDF's edge section can be wrong/missing). Purchase
        # + client charge = whole 50 m rolls covering those meters. Some exports
        # omit the length column — then fall back to the estimate PDF's roll count
        # for that code, else 1 roll flagged for verification.
        import math
        pdf_edge_rolls = {m["name"]: (m["qty"] or 0) for m in materials if m.get("kind") == "edge"}
        for e in pl_edges:
            orig = e["code"]
            real_e, _ltr = decor.substitute_real_code(orig, edge_shorts)
            shown = real_e if _ltr else orig
            if e.get("meters"):
                rolls = max(1, math.ceil(e["meters"] / inventory.EDGE_ROLL_METERS))
                desc = (f"{shown} — {e['meters']:g} m banding on {e['parts']} part edge(s) "
                        f"→ {rolls} whole roll(s) of {inventory.EDGE_ROLL_METERS:g} m")
            elif pdf_edge_rolls.get(orig):
                rolls = int(pdf_edge_rolls[orig])
                desc = (f"{shown} — {e['parts']} part edge(s); rolls from estimate PDF "
                        f"({rolls} × {inventory.EDGE_ROLL_METERS:g} m)")
            else:
                rolls = 1
                desc = f"{shown} — {e['parts']} part edge(s); length unknown — VERIFY roll count"
            self._add_material_line(
                orig, "edge", 0, rolls, desc[:140], unpriced,
                uom="Roll", rate_factor=inventory.EDGE_ROLL_METERS,
                decor_meta=edge_decors.get(_ltr) if _ltr else None,
                real_code=real_e if _ltr else None,
            )

        for h in hardware:
            cat = f" · {h['category']}" if h.get("category") and h["category"] != h["code"] else ""
            self._add_material_line(
                h["code"], "hardware", 0, h["qty"],
                f"{h['code']} — {h['qty']} nos{cat}", unpriced,
                dims={"category": h.get("category")},
            )
        # Estimate-PDF hardware the part list didn't cover (a new fitting whose
        # designation the parser missed, or a category with no part rows) is added
        # as a fallback line instead of vanishing.
        covered = {(h.get("category") or "").strip() for h in hardware} \
            | {h["code"] for h in hardware}
        for m in deferred_hw:
            if m["name"] in covered:
                continue
            self._add_material_line(
                m["name"], "hardware", 0, m["qty"] or 0,
                f"{_pdf_desc(m)} — from estimate PDF (not matched in part list)", unpriced,
            )

        # Re-append the preserved manual rows (re-priced at the current landed rate
        # only if no rate was keyed).
        for r in manual_rows:
            self.append("materials", {
                "item": r.get("item"), "material": r.get("material"),
                "description": r.get("description"), "qty": r.get("qty") or 0,
                "uom": r.get("uom"), "unit_cost": r.get("unit_cost") or 0,
                "line_cost": (r.get("qty") or 0) * (r.get("unit_cost") or 0),
                "customer_supplied": r.get("customer_supplied") or 0,
                "is_manual": 1,
            })

        self.unpriced_materials = ", ".join(unpriced)
        if unpriced:
            frappe.msgprint(
                _("UNPRICED lines entered at ₹0 — this estimate is NOT quotable "
                  "yet. Key each rate on the <b>Estimation (Assumed)</b> price "
                  "list (the only rate authority) and Re-import:<br><b>{0}</b>")
                .format(", ".join(unpriced)),
                title=_("Materials need a price"), indicator="red",
            )

        # Edge Banding operation qty: banded-edge count from the part list when
        # the CSV part count is absent.
        if not part_count and pl_edges:
            part_count = sum(e["parts"] or 0 for e in pl_edges)
        opq = estimate_pdf.operation_quantities(materials, part_count)
        for row in self.labor:
            op = op_phase(row)
            if op in opq:
                row.qty = opq[op]
            std = OPERATION_STANDARDS.get(op)
            if std and not float(row.carp_min or 0):
                # carp_min = minutes the workstation is occupied per unit (the
                # single time driver; the crew wage lives in the workstation rate).
                row.carp_min = std["min_per_unit"]
        # Every slot instance present on the lines gets a map row — WITHOUT
        # descriptions in the PDF the row comes in BLANK, so the user simply
        # selects the laminate / edge band (creating the stock item if new).
        have_rows = {("Laminate" if (r.domain or "Laminate") != "Edge Band" else "Edge Band",
                      (r.slot or "").strip().lower())
                     for r in (self.get("sku_decors") or [])}
        have_rows |= {("Edge Band", (r.slot or "").strip().lower())
                      for r in (self.get("sku_decor_edges") or [])}
        for m_row in self.materials or []:
            base = str(m_row.material or "")
            up = base.upper()
            if not (up.startswith("SG_LAM") or up.startswith("EB_")):
                continue
            key = decor.slot_key(base)
            if not key:
                continue
            dom = "Edge Band" if up.startswith("EB_") else "Laminate"
            if (dom, key) not in have_rows:
                if dom == "Edge Band":
                    self.append("sku_decor_edges", {"slot": key, "thickness": eb_thick, "width": eb_wide})
                else:
                    self.append("sku_decors", {"slot": key, "domain": dom})
                have_rows.add((dom, key))

        self.import_drivers = json.dumps(opq)

        # A design exists the moment an estimate PDF does — design steps whose
        # qty is still 0 become billable at 1 (user-set quantities stick).
        for row in self.get("design_labor") or []:
            if not float(row.qty or 0):
                row.qty = 1

        if parts:
            self.set("parts", [])
            for p in parts:
                self.append("parts", {
                    "part_no": p["part_no"], "designation": p["designation"], "material": p["material"],
                    "tag": p["tag"], "length": p["length"], "width": p["width"], "thickness": p["thickness"],
                    "cut": p.get("cut", 1), "edge_banded": p.get("edge_banded", 0),
                    "laminated": p.get("laminated", 0),
                })

    def _add_material_line(self, name, kind, thickness, qty, desc, unpriced, dims=None,
                           uom=None, rate_factor=1, decor_meta=None, real_code=None):
        """Append a costed material row. A NEW code auto-creates its Item
        STRUCTURE (group, UOM, dims — zero manual setup), but the rate is NEVER
        invented: the unit cost is the STOCK PRICE LIST rate EXACTLY (no
        gross-up; GST at document level). An unpriced item enters at 0 and is
        flagged LOUDLY — key its rate on the Estimation (Assumed) list once and
        Re-import. `uom`/`rate_factor` adapt the unit (edge banding: Rolls at
        per-meter rate x 50). `real_code` = the décor-substituted item; `name`
        stays the ORIGINAL OCL code on the line, so the décor map can re-point
        the item any time."""
        code, rate, source = inventory.ensure_material_item(real_code or name, kind=kind,
                                                           thickness=thickness, dims=dims)
        if decor_meta:
            inventory.enrich_decor_item(code, decor_meta)
        rate = (rate or 0) * (rate_factor or 1)
        line_uom = uom or inventory.stock_uom_for(kind)
        self.append("materials", {
            "item": code, "material": name, "description": (desc or name)[:140],
            "qty": qty, "uom": line_uom if frappe.db.exists("UOM", line_uom) else None,
            "unit_cost": rate, "line_cost": qty * rate,
        })
        if source == "unset":
            unpriced.append(code)

    def _decor_maps_from_table(self):
        """(laminate_map, edge_map) — slot letter → parsed décor from the SKU's
        Décor Slots table, THE single source of truth for what a/b/c mean."""
        lam, edge = {}, {}

        def parse_row(row, domain):
            return {
                "brand": (row.brand or "").strip() or None,
                "catalogue": (row.code or "").strip() or None,
                "name": (row.decor_name or "").strip(),
                "year": (row.year or "").strip(),
                "short": (row.get("short") or "").strip() or None,
                "thickness": float(row.get("thickness") or 0),
                "width": float(row.get("width") or 0),
                "domain": domain,
                "title": None,
                "raw": " ".join(x for x in ((row.brand or "").strip(), (row.code or "").strip(),
                                            (row.decor_name or "").strip()) if x),
            }

        for row in self.get("sku_decors") or []:
            slot = (row.slot or "").strip().lower()
            if not decor.SLOT_TOKEN_RE.match(slot):
                continue
            # legacy: pre-split rows could carry domain 'Edge Band' in this table
            if (row.get("domain") or "Laminate") == "Edge Band":
                edge.setdefault(slot, parse_row(row, "Edge Band"))
            else:
                lam.setdefault(slot, parse_row(row, "Laminate"))
        for row in self.get("sku_decor_edges") or []:
            slot = (row.slot or "").strip().lower()
            if decor.SLOT_TOKEN_RE.match(slot):
                edge.setdefault(slot, parse_row(row, "Edge Band"))
        return lam, edge

    def apply_decor_map(self):
        """The map table DECIDES which real laminate / edge band each material
        line carries — every save re-points lines whose ORIGINAL code (kept in
        the `material` column) has slot letters. Editing the table therefore
        changes the lines without any re-import; slots without a map row keep
        the generic code and the user is warned to fill the map."""
        if self._frozen() or not self.materials:
            return
        lam, edge = self._decor_maps_from_table()
        lam_shorts = {k: decor.short_code(v) for k, v in lam.items() if decor.short_code(v)}
        edge_shorts = {k: decor.short_code(v) for k, v in edge.items() if decor.short_code(v)}
        missing = set()
        for m in self.materials:
            base = str(m.material or "")
            up = base.upper()
            if up.startswith("SG_PLY") or not decor.trailing_slots(base):
                continue
            is_edge = up.startswith("EB_")
            if not (is_edge or up.startswith("SG_LAM")):
                continue
            real, letter = decor.substitute_real_code(base, edge_shorts if is_edge else lam_shorts)
            if letter is None:
                missing.add(f"{decor.slot_key(base)} ({'Edge Band' if is_edge else 'Laminate'})")
                real = base
            # the cross-check column: which slot produced this line
            if m.meta.has_field("remarks"):
                key = decor.slot_key(base)
                kind_lbl = "edge" if is_edge else "lam"
                m.remarks = (f"{kind_lbl} {key} → {real.rsplit('_', 1)[-1]}" if letter
                             else f"{kind_lbl} {key}: NOT MAPPED")[:140]
            if (m.item or "") == real:
                continue
            meta = (edge if is_edge else lam).get(letter) if letter else None
            kind = "edge" if is_edge else "laminate"
            code, _rate, _src = inventory.ensure_material_item(
                real, kind=kind, thickness=(meta or {}).get("thickness") or 0)
            if meta:
                inventory.enrich_decor_item(code, meta)
            old = m.item
            if m.description and old:
                m.description = m.description.replace(str(old), code)[:140]
            m.item = code
        if missing:
            frappe.msgprint(
                _("Décor map incomplete — slot(s) {0} have no row in the Décor Slots "
                  "table, so those lines keep their GENERIC codes. Fill the map to "
                  "point them at real laminates/edge bands.").format(", ".join(sorted(missing))),
                title=_("Fill the Décor Slots map"), indicator="orange",
            )

    @frappe.whitelist()
    def prefill_decor_from_project(self):
        """COMBINED-MODEL helper: one SketchUp file holding every SKU reuses slot
        letters PROJECT-WIDE — one letter per DISTINCT décor (not per article).
        Pull every distinct décor from this project's other SKUs into this SKU's
        map: a letter is kept when it doesn't collide, otherwise the next free
        letter (per domain) is assigned. Returns the register — the exact
        generic material letters to use while building the combined model."""
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        if not self.project:
            frappe.throw(_("Set the Project first."))
        letters = "abcdefghijklmnopqrstuvwxyz"
        used = {"Laminate": set(), "Edge Band": set()}
        have = set()
        for r in list(self.get("sku_decors") or []) + list(self.get("sku_decor_edges") or []):
            dom = "Edge Band" if r.parentfield == "sku_decor_edges" else (r.get("domain") or "Laminate")
            used[dom].add((r.slot or "").strip().lower()[:1])
            have.add((dom, (r.brand or "").strip().lower(), (r.code or "").strip(),
                      (r.decor_name or "").strip().lower()))
        register, added = [], 0
        for name in frappe.get_all("Estimate SKU", filters={"project": self.project},
                                   order_by="room asc, article_name asc", pluck="name"):
            if name == self.name:
                continue
            src = frappe.get_doc("Estimate SKU", name)
            for r in list(src.get("sku_decors") or []) + list(src.get("sku_decor_edges") or []):
                dom = "Edge Band" if r.parentfield == "sku_decor_edges" else (r.get("domain") or "Laminate")
                ident = (dom, (r.brand or "").strip().lower(), (r.code or "").strip(),
                         (r.decor_name or "").strip().lower())
                if ident in have or not (r.brand or r.code):
                    continue
                pref = (r.slot or "").strip().lower()[:1]
                slot = pref if pref and pref not in used[dom] else \
                    next((l for l in letters if l not in used[dom]), None)
                if not slot:
                    continue
                used[dom].add(slot)
                have.add(ident)
                target = "sku_decor_edges" if dom == "Edge Band" else "sku_decors"
                payload = {
                    "slot": slot, "brand": r.brand, "code": r.code,
                    "decor_name": r.decor_name, "year": r.get("year"), "short": r.get("short"),
                    "thickness": r.get("thickness"), "width": r.get("width"),
                }
                if target == "sku_decors":
                    payload["domain"] = dom
                    payload.pop("width", None)
                self.append(target, payload)
                register.append({"slot": slot, "domain": dom, "brand": r.brand,
                                 "code": r.code, "name": r.decor_name,
                                 "from": src.sku_code or name,
                                 "relettered": bool(pref and pref != slot)})
                added += 1
        if added:
            self.save(ignore_permissions=True)
        return {"added": added,
                "rows": [{"slot": r.slot,
                          "domain": "Edge Band" if r.parentfield == "sku_decor_edges"
                                    else (r.get("domain") or "Laminate"),
                          "brand": r.brand, "code": r.code, "name": r.decor_name}
                         for r in list(self.get("sku_decors") or [])
                         + list(self.get("sku_decor_edges") or [])],
                "register": register}

    @frappe.whitelist()
    def map_slot(self, domain, slot, brand=None, code=None, decor_name=None,
                 thickness=None, width=None, short=None):
        """Map a décor slot STRAIGHT FROM THE MATERIAL LINES: pick brand / code /
        name (+ thickness, and width for edge bands) in one dialog — no table
        hopping, no code-order memorising. Upserts the row in the right map
        table and saves, which re-points the lines AND creates the stock Item
        right away."""
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        slot = (slot or "").strip().lower()
        if not decor.SLOT_TOKEN_RE.match(slot):
            frappe.throw(_("Slot must be a letter with optional digits (a, b, b1 …)."))
        table = "sku_decor_edges" if domain == "Edge Band" else "sku_decors"
        row = next((r for r in (self.get(table) or [])
                    if (r.slot or "").strip().lower() == slot), None)
        if row is None:
            row = self.append(table, {"slot": slot})
            if table == "sku_decors":
                row.domain = "Laminate"
        row.brand = brand
        row.code = code
        row.decor_name = decor_name
        # exact suffix override — set when the user picked an EXISTING item, so
        # substitution reuses that item family instead of creating a duplicate
        if short not in (None, ""):
            row.short = short
        if thickness not in (None, ""):
            row.thickness = float(thickness)
        if width not in (None, "") and table == "sku_decor_edges":
            row.width = float(width)
        self.save(ignore_permissions=True)
        prefix = ("edge " if domain == "Edge Band" else "lam ") + slot + " →"
        mapped = [m.item for m in self.materials or [] if str(m.get("remarks") or "").startswith(prefix)]
        return {"mapped_lines": len(mapped), "items": mapped[:6]}

    @frappe.whitelist()
    def decor_from_item(self, item_code):
        """Prefill for 'Use existing item' in the mapping dialog: the décor
        identity of an EXISTING laminate/edge Item — suffix (short), brand,
        catalogue code, name, physicals — so mapping reuses it instead of
        creating a near-duplicate in stock."""
        if not frappe.db.exists("Item", item_code):
            frappe.throw(_("Item {0} not found.").format(item_code))
        it = frappe.db.get_value(
            "Item", item_code,
            ["item_name", "default_item_manufacturer", "default_manufacturer_part_no",
             "mallet_thickness_mm", "mallet_sheet_width_mm"], as_dict=True) or {}
        suffix = str(item_code).rsplit("_", 1)[-1]
        return {
            "short": suffix,
            "brand": it.get("default_item_manufacturer"),
            "code": it.get("default_manufacturer_part_no"),
            "decor_name": (it.get("item_name") or "")[:100],
            "thickness": it.get("mallet_thickness_mm"),
            "width": it.get("mallet_sheet_width_mm"),
        }

    def refresh_material_rates(self):
        """The price list is the only rate authority — until the Estimate is
        quoted (rates_frozen), every save re-reads each material line's rate,
        so a price keyed AFTER import (the red 'not quotable' flow) takes
        effect without a re-import. Edge banding lines (Roll UOM) stay at the
        per-metre rate x roll length. Quantities, descriptions and manual rows
        are untouched; the unpriced flag clears itself as items get priced."""
        if self._frozen() or not self.materials:
            return
        unpriced = []
        for row in self.materials:
            if not row.item:
                continue
            rate, source = inventory.material_rate(row.item)
            factor = inventory.EDGE_ROLL_METERS if (row.uom or "") == "Roll" else 1
            row.unit_cost = (rate or 0) * factor
            row.line_cost = (row.qty or 0) * row.unit_cost
            if source == "unset" and row.item not in unpriced:
                unpriced.append(row.item)
        self.unpriced_materials = ", ".join(unpriced)

    def _hw_line_counts(self):
        """Hardware quantities summed from the CURRENT material lines (imported
        AND manual) by designation substring — HWD_MiniFix, HWD_Screw…"""
        tot = {"minifix": 0, "screw": 0, "hinge": 0, "rail": 0, "handle": 0, "shelf": 0}
        for m in self.materials or []:
            code = str(m.item or m.material or "")
            if not code.upper().startswith("HWD"):
                continue
            name = f"{m.item or ''} {m.material or ''}".lower()
            for k in tot:
                if k in name:
                    tot[k] += m.qty or 0
        return tot

    def enforce_locked_qty(self):
        """Locked operations always carry their COMPUTED qty — hand edits never
        stick. Sheet ops + Edge Banding come from the last import's drivers;
        Minifix Boring / Drilling / Install Hardware are live-derived from the
        HWD_* material lines, so a manually added fitting (reviewable as a row)
        moves its operation too."""
        if not self.import_drivers:
            return
        try:
            q = json.loads(self.import_drivers)
        except Exception:
            q = {}
        hw = self._hw_line_counts()
        hw_qty = {
            "Minifix Boring": hw["minifix"],
            "Drilling": hw["screw"],
            # Install Hardware covers ONLY hinges / drawer rails / handles / shelf supports.
            "Install Hardware": hw["hinge"] + hw["rail"] + hw["handle"] + hw["shelf"],
        }
        for row in self.labor:
            op = op_phase(row)
            if op in hw_qty:
                row.qty = hw_qty[op]
            elif op in estimate_pdf.LOCKED_OPERATIONS and op in q:
                row.qty = q[op]

    def refresh_project_estimates(self):
        """Refresh the totals of any DRAFT Estimate that CARRIES this SKU, so an
        edit here shows there without reopening. SKU selection belongs to the
        Estimate (rows are picked by hand) — nothing is ever added or removed
        from an estimate automatically; submitted estimates are never touched."""
        for name in frappe.get_all(
            "Execution Estimate SKU", filters={"estimate_sku": self.name},
            pluck="parent", distinct=True,
        ):
            if frappe.db.get_value("Estimate", name, "docstatus") != 0:
                continue
            try:
                est = frappe.get_doc("Estimate", name)
                est.save(ignore_permissions=True)  # validate refreshes row data + totals
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"mallet_estimator refresh estimate {name}")

    # --- steps -------------------------------------------------------------
    def ensure_steps(self):
        if self.labor:
            # Backfill operation (from legacy phase) + workstation on older rows.
            for row in self.labor:
                if not getattr(row, "operation", None):
                    row.operation = op_phase(row)
                if not row.workstation:
                    row.workstation = default_workstation(row)
            return
        for t in STEP_TEMPLATE:
            op_name = t["phase"]  # STEP_TEMPLATE phase == the Operation name
            mins, ws = operation_defaults(op_name)
            self.append("labor", {
                "operation": op_name,
                "phase": op_name,  # keep the legacy field in sync during transition
                "workstation": ws or DEFAULT_WORKSTATION,
                "carp_min": mins,
                "in_factory": t.get("in_factory", 0),
                "is_misc": t.get("is_misc", 0),
                # No files attached -> no default quantities (they fill on import).
                "qty": 1 if self.estimate_pdf else 0,
            })

    def ensure_design_steps(self):
        """D1 — seed the designer's 7-step pipeline (priced at the Design Desk
        workstation). Same Operation-master model as the execution steps."""
        if not self.meta.has_field("design_labor"):
            return
        if self.design_labor:
            for row in self.design_labor:
                if not getattr(row, "operation", None):
                    row.operation = op_phase(row)
                if not row.workstation:
                    row.workstation = "Design Desk"
            return
        for t in DESIGN_STEP_TEMPLATE:
            op_name = t["phase"]
            mins, ws = operation_defaults(op_name)
            if not mins:
                mins = DESIGN_STANDARDS.get(op_name, {}).get("min_per_unit", 0)
            self.append("design_labor", {
                "operation": op_name,
                "phase": op_name,
                "workstation": ws or "Design Desk",
                "carp_min": mins,
                "in_factory": t.get("in_factory", 1),
                "qty": 1 if self.estimate_pdf else 0,
            })

    # --- derived consumables + transport (J1 / C1) -------------------------
    def derive_joinery(self):
        """J1 — Fevicol + Abrotape derived from the Sheet Lamination step qty:
        3 packets + 11 m tape per laminated sheet, as stocked Joinery Hardware
        items at their landed (post-tax) rate."""
        if not self.meta.has_field("joinery_items"):
            return
        sheets = 0.0
        for row in self.labor or []:
            if op_phase(row) == "Sheet Lamination":
                sheets = float(row.qty or 0)
                break
        self.set("joinery_items", [])
        if not sheets:
            return
        for code, qty, uom, note in (
            ("JH_Fevicol", 3 * sheets, "Nos", f"3 packets × {sheets:g} laminated sheet(s)"),
            ("JH_Abrotape", 11 * sheets, "Meter", f"11 m × {sheets:g} laminated sheet(s) — 20 m rolls"),
        ):
            code, rate, _src = inventory.ensure_material_item(code, kind="joinery")
            self.append("joinery_items", {
                "item": code, "description": note, "qty": qty,
                "uom": uom if frappe.db.exists("UOM", uom) else None,
                "unit_cost": rate, "amount": qty * (rate or 0),
                "derived_from": "Sheet Lamination",
            })

    def build_cost_breakup(self):
        """C1 — the cost grid: material buckets grouped into Sheet Goods /
        Laminate / Edge Banding / Hardware totals, then Design, Wages, Factory
        Overhead. Transport is billed on the Estimate (shared trips)."""
        if not self.meta.has_field("cost_breakup"):
            return
        r = getattr(self, "_calc", None) or {}
        mat = {}
        for m in self.materials or []:
            b = inventory.material_bucket(m.item, m.material)
            mat[b] = mat.get(b, 0) + float(m.line_cost or 0)
        joinery = float(self.get("joinery_cost") or 0)

        def bucket(*names):
            return sum(mat.get(n, 0) for n in names)

        groups = [
            ["Sheet Goods", [
                ["Ply V0 (structure grade)", mat.get("Ply V0 (structure grade)", 0)],
                ["Ply V1 (visible grade)", mat.get("Ply V1 (visible grade)", 0)],
            ]],
            ["Laminate", [
                ["Internal", mat.get("Laminate Internal", 0)],
                ["External", mat.get("Laminate External", 0)],
            ]],
            ["Edge Banding", [
                ["Internal", mat.get("Edge Banding Internal", 0)],
                ["External", mat.get("Edge Banding External", 0)],
            ]],
            ["Hardware", [
                ["Client Hardware (hinges/rails/handles…)", mat.get("Client Hardware", 0)],
                ["Joinery Hardware (screws/minifix…)", mat.get("Joinery Hardware", 0)],
                ["Joinery Consumables (Fevicol/Abrotape)", joinery],
            ]],
            ["Labor & Overhead", [
                ["Design Labor (Design Desk)", float(self.design_cost or 0)],
                ["Carpentry Wages", float(r.get("labor_cost", self.labor_cost or 0))],
                ["Factory Overhead (rent + electricity + consumables + depreciation)",
                 float(self.overhead_cost or 0)],
            ]],
        ]
        other = bucket("Other Material")
        if other:
            groups[0][1].append(["Other Material", other])
        # Transport sits INSIDE internal cost (it is money spent making/delivering
        # this SKU) but is billed on the Estimate — show it so Internal vs Client
        # always reconciles on screen.
        transport = float(r.get("transport_cost") or 0)
        if transport:
            groups.append(["Transport", [
                ["Trips for this SKU (recovered on the Estimate, at cost)", transport],
            ]])

        # Output GST shown per SKU too (the DOCUMENT charge stays on the
        # Estimate/Quotation — this is the same 18% made visible per article).
        gst_pct = 18.0
        client_total = float(self.client_total or 0)
        internal = float(self.internal_cost or 0)
        gst_amount = client_total * gst_pct / 100.0
        # Profit: what the client pays (SKU total + transport recovered at cost on
        # the Estimate) minus every rupee it cost to make.
        profit = client_total + transport - internal
        # The clear Material / Labor / Design / Overhead / Transport / Taxes
        # bifurcation (client side, transport kept separate as the shared cost):
        # each line carries its % of the pre-tax total and its own GST.
        bif = build_bifurcation({
            "client_material": float(self.client_material or 0),
            "client_labor": float(r.get("client_labor") or 0),
            "client_design": float(r.get("client_design") or 0),
            "client_overhead": float(r.get("client_overhead") or 0),
            "transport": transport,
        }, gst_pct)
        self.cost_breakup = json.dumps({
            "groups": groups,
            "internal": internal,
            "client_material": float(self.client_material or 0),
            "client_design_exec": float(self.client_design_exec or 0),
            "client_total": client_total,
            "markup_pct": r.get("markup_pct") or {},
            "transport": transport,
            "profit": profit,
            "margin_pct": (profit / client_total * 100.0) if client_total else 0,
            "gst_pct": gst_pct,
            "gst_amount": gst_amount,
            "client_total_with_gst": client_total + gst_amount,
            "bifurcation": bif,
            "sqft": self.facial_sqft_block(),
            "note": "Transport is billed on the Estimate (trips shared across SKUs).",
        })

    def facial_sqft_block(self):
        """Facial area per the interior-design convention: the product of the two
        GREATEST outer dimensions (mm) → sq ft, with the client per-sqft rates
        (all pre-tax). None when outer dims aren't keyed yet."""
        dims = sorted([float(self.get(f) or 0) for f in ("outer_w", "outer_d", "outer_h")], reverse=True)
        if not (dims[0] and dims[1]):
            return None
        sqft = dims[0] * dims[1] / 92903.04  # mm² per sq ft
        if not sqft:
            return None
        return {
            "sqft": sqft,
            "material_per_sqft": float(self.client_material or 0) / sqft,
            "labor_per_sqft": float(self.client_design_exec or 0) / sqft,
            "total_per_sqft": float(self.client_total or 0) / sqft,
        }

    # --- naming ------------------------------------------------------------
    def customer_display_name(self):
        if not self.customer:
            return ""
        return frappe.db.get_value("Customer", self.customer, "customer_name") or self.customer

    def compute_code(self):
        # Every article is built for a specific customer, so the code always
        # carries the customer initials as a prefix.
        ci = customer_initials(self.customer_display_name())
        if self.auto_name:
            room_token = "All Rooms" if self.get("multi_room") else self.room
            self.sku_code = sku_code(self.customer_display_name(), room_token, self.article_name)
        elif self.sku_code and ci and not self.sku_code.upper().startswith(ci):
            self.sku_code = f"{ci}_{self.sku_code}"
        if not self.sku_code:
            self.sku_code = "_".join(x for x in [ci, self.article_name] if x) or self.name

    # --- costs -------------------------------------------------------------
    def compute_costs(self):
        settings = frappe.get_single("Estimate Settings")
        for m in self.materials:
            # Customer-supplied material (client buys & ships it to us) is tracked
            # but never billed back — it carries no cost in the estimate.
            if getattr(m, "customer_supplied", 0):
                m.line_cost = 0
            else:
                m.line_cost = (m.qty or 0) * (m.unit_cost or 0)
        # Show each step's master Std Time (min/unit) next to its actual Min/Unit,
        # so an override (Min/Unit != Std) is obvious at a glance.
        for row in list(self.labor or []) + list(self.get("design_labor") or []):
            row.std_min = operation_defaults(op_phase(row))[0]
        # Each phase is priced at its Workstation's live Net Hour Rate from the
        # ERPNext Manufacturing master (Rent + per-role Wages + Depreciation +
        # Electricity + Consumables). Wages are folded in per workstation crew.
        ws_rates = live_workstation_rates(settings)
        r = calc_sku(self, settings, ws_rates=ws_rates)
        self._calc = r  # kept for the cost-breakup table
        for k in (
            "material_cost", "labor_cost", "machine_cost", "rent_cost", "overhead_cost",
            "design_cost", "internal_cost", "client_material", "client_design_exec",
            "client_total", "carp_min_total", "helper_min_total",
            "joinery_cost", "transport_cost",
        ):
            if self.meta.has_field(k):
                self.set(k, r.get(k, 0))
        # I-days: a human's productive day = 360 min (6 of 8 hrs). The SKU takes
        # as long as its BUSIEST trade — max(carpenter, helper minutes) ÷ 360.
        if self.meta.has_field("est_days"):
            # round at SOURCE to the field's 1-decimal precision — an unrounded
            # value vs the stored rounded one made recompute() report 'changed'
            # on every form open (reload loop)
            self.est_days = round(max(float(r.get("carp_min_total") or 0),
                                      float(r.get("helper_min_total") or 0)) / 360.0, 1)
        self.compute_execution()

    def compute_execution(self):
        """V1/V2 — cost the chosen actual materials and the variance vs the estimate.
        Actual amount = actual_qty × actual_rate; variance = actual − estimated.
        Falls back to the chosen item's ceiling rate when a rate isn't keyed yet."""
        exec_total = 0
        for row in self.execution_materials or []:
            if row.chosen_item and not (row.actual_rate or 0):
                row.actual_rate = inventory.material_rate(row.chosen_item)[0]
            row.actual_amount = (row.actual_qty or 0) * (row.actual_rate or 0)
            row.variance = row.actual_amount - (row.est_amount or 0)
            exec_total += row.actual_amount
        self.execution_material_cost = exec_total
        # Only meaningful once a design exists; else 0 (no variance).
        self.execution_variance = (exec_total - (self.material_cost or 0)) if self.execution_materials else 0

    @frappe.whitelist()
    def build_execution_design(self):
        """V1 — seed the execution material table from the estimate's generic lines,
        one row each, defaulting the chosen item to the generic. The designer then
        swaps in the real client-selected item + actual rate/vendor; variance is
        tracked automatically. Re-running reseeds from the current estimate."""
        # Lines already carry the REAL items (laminates included) — execution
        # starts as the estimate; swap items only where the client changes a pick.
        self.set("execution_materials", [])
        for m in self.materials or []:
            if getattr(m, "customer_supplied", 0):
                continue  # client-supplied — not costed either side
            est_amt = (m.qty or 0) * (m.unit_cost or 0)
            self.append("execution_materials", {
                "est_material": m.material or m.item,
                "est_qty": m.qty, "est_rate": m.unit_cost, "est_amount": est_amt,
                "chosen_item": m.item, "actual_qty": m.qty, "actual_rate": m.unit_cost,
                "actual_amount": est_amt, "variance": 0,
            })
        self.save(ignore_permissions=True)
        return {"rows": len(self.execution_materials)}

    @frappe.whitelist()
    def reset_step_times(self):
        """Pull every step's Min/Unit + Workstation from its Operation master
        (Std Time + Default Workstation), overwriting any per-SKU overrides, then
        re-price. Use after changing an Operation's Std Time on the master."""
        n = 0
        for row in self.labor or []:
            mins, ws = operation_defaults(op_phase(row))
            row.carp_min = mins
            if ws:
                row.workstation = ws
            n += 1
        self.save(ignore_permissions=True)
        return {"steps": n}

    @frappe.whitelist()
    def get_landed_rate(self, item_code):
        """Stock price-list rate + UOM for a manually added material row — the
        rate is NEVER hand-altered; it comes from the price list (version history
        lives there)."""
        rate, _src = inventory.material_rate(item_code)
        return {"rate": rate, "uom": frappe.db.get_value("Item", item_code, "stock_uom")}

    @frappe.whitelist()
    def workstation_net_rates(self):
        """{workstation_name: Net Hour Rate} (+ __default__ and __markups__) so the
        form can price Phase Cost AND the SKU totals live as you edit — no save
        needed (I1/I3). The save remains the authoritative computation."""
        settings = frappe.get_single("Estimate Settings")
        rates = live_workstation_rates(settings)
        out = {name: (r.get("net_hr") or 0) for name, r in rates.items()}
        # live totals must price at THIS SKU's effective margins (override wins)
        if self.get("use_custom_margins"):
            out["__markups__"] = {
                "material": float(self.get("margin_material") or 0),
                "labor": float(self.get("margin_labor") or 0),
                "overhead": float(self.get("margin_overhead") or 0),
                "design": float(self.get("margin_design") or 0),
            }
        else:
            out["__markups__"] = {
                "material": float(settings.markup_material or 0),
                "labor": float(settings.markup_labor or 0),
                "overhead": float(settings.markup_overhead or 0),
                "design": float(settings.markup_design or 0),
            }
        out["__default__"] = out.get(DEFAULT_WORKSTATION, 0)
        return out

    @frappe.whitelist()
    def apply_target_price(self, target=None, per_sqft=None):
        """Price backwards from REVENUE: give the pre-tax price you want for
        this SKU (final rupees, or rupees per facial sq ft) and the margins are
        back-solved onto the SKU as custom margins. Material margin keeps its
        current effective value (the client can supply material); the whole
        remaining uplift is carried by labor / overhead / design — one factor
        across the three, which is where conversion profit belongs."""
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        target = float(target or 0)
        if not target and per_sqft:
            blk = self.facial_sqft_block()
            if not blk:
                frappe.throw(_("Per-sqft target needs the outer W/D/H dims — key them first."))
            target = float(per_sqft) * blk["sqft"]
        if target <= 0:
            frappe.throw(_("Give a target price (₹ or ₹/sq ft)."))
        r = getattr(self, "_calc", None) or {}
        if not r:
            self.compute_costs()
            r = getattr(self, "_calc", None) or {}
        mat_cost = float(r.get("material_cost") or 0) + float(r.get("joinery_cost") or 0)
        conv_cost = float(r.get("labor_cost") or 0) + float(r.get("overhead_cost") or 0) \
            + float(r.get("design_cost") or 0)
        if not conv_cost:
            frappe.throw(_("No labor/overhead/design cost to carry the margin — import the SKU first."))
        mat_margin = float((r.get("markup_pct") or {}).get("material") or 0)
        client_material = mat_cost * (1 + mat_margin / 100.0)
        k = (target - client_material) / conv_cost - 1.0
        self.use_custom_margins = 1
        self.margin_material = mat_margin
        self.margin_labor = self.margin_overhead = self.margin_design = round(k * 100.0, 2)
        self.save(ignore_permissions=True)
        internal = float(self.internal_cost or 0)
        return {
            "target": target,
            "client_total": float(self.client_total or 0),
            "conversion_margin_pct": round(k * 100.0, 2),
            "blended_margin_pct": round((target / internal - 1) * 100.0, 2) if internal else 0,
            "profit": float(self.client_total or 0) + float(r.get("transport_cost") or 0) - internal,
            "below_cost": target < internal,
        }

    @frappe.whitelist()
    def reimport(self):
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        """Force a re-import from the attached OpenCutList PDF + Parts CSV,
        bypassing the change-detection guard — rebuilds the material lines at the
        CURRENT import logic (e.g. designation-level hardware). Returns a summary."""
        if not self.estimate_pdf:
            frappe.throw(_("Attach an OpenCutList Estimate PDF first."))
        self.do_import()
        self.save(ignore_permissions=True)
        return {
            "materials": len(self.materials or []),
            "parts": len(self.parts or []),
        }

    @frappe.whitelist()
    def recompute(self):
        if self.get("rates_frozen"):
            return {"changed": False}  # quoted — never silently re-price
        """Re-price every step at the CURRENT Workstation Net Hour Rates (and
        refresh each step's master Std Time) and save only if something actually
        moved. Called on form load so Phase Cost / Std (master) never show a value
        that pre-dates a workstation-rate or Operation-time change."""
        before = self.client_total or 0
        before_days = float(self.get("est_days") or 0)
        before_std = [row.std_min for row in (self.labor or [])]
        before_rates = [row.unit_cost for row in (self.materials or [])]
        self.refresh_material_rates()
        self.compute_costs()
        after_std = [row.std_min for row in (self.labor or [])]
        after_rates = [row.unit_cost for row in (self.materials or [])]
        if abs((self.client_total or 0) - before) > 0.005 or before_std != after_std \
                or before_rates != after_rates \
                or abs(float(self.get("est_days") or 0) - before_days) > 0.05:
            self.save(ignore_permissions=True)
            return {"changed": True, "client_total": self.client_total}
        return {"changed": False, "client_total": self.client_total}

    @frappe.whitelist()
    def refresh_rates(self):
        """Materials > Refresh rates: pull the CURRENT price-list rate onto
        every material line (imported AND manual) without re-parsing the PDFs.
        The everyday flow after pricing red-flagged items on the Estimation
        (Assumed) list. save() runs the full validate chain, which does the
        actual refresh + recosting."""
        if self._frozen():
            frappe.throw(_("Rates are frozen (quoted) — amend/cancel the Estimate first."))
        before = [(row.item, row.unit_cost) for row in (self.materials or [])]
        self.save(ignore_permissions=True)
        after = [(row.item, row.unit_cost) for row in (self.materials or [])]
        changed = sum(1 for b, a in zip(before, after)
                      if abs((b[1] or 0) - (a[1] or 0)) > 0.005)
        return {"changed": changed, "unpriced": self.unpriced_materials or ""}

    # --- ERPNext Item ------------------------------------------------------
    def sync_item(self):
        code = self.sku_code or self.name
        if not code:
            return
        if self.item and frappe.db.exists("Item", self.item):
            frappe.db.set_value("Item", self.item, {
                "item_name": (self.article_name or code)[:140],
                "standard_rate": self.client_total,
                "description": self.description or self.article_name,
            })
            return
        if frappe.db.exists("Item", code):
            target = code
        else:
            item = frappe.new_doc("Item")
            item.item_code = code
            item.item_name = (self.article_name or code)[:140]
            # Finished client articles get their own group so they never mix with
            # regular products and can be archived when the project closes.
            item.item_group = (
                inventory.CLIENT_SKU_GROUP if frappe.db.exists("Item Group", inventory.CLIENT_SKU_GROUP)
                else get_default_item_group()
            )
            item.stock_uom = "Nos"
            item.is_stock_item = 1   # finished good: produced -> stocked -> delivered
            item.is_sales_item = 1   # sold on the Quotation
            # Each finished article is a unique, high-value one-off — serialize it
            # for per-unit warranty / repair traceability (this piece -> its Work
            # Order -> BOM -> the exact materials used).
            if item.meta.has_field("has_serial_no"):
                item.has_serial_no = 1
            if item.meta.has_field("serial_no_series"):
                item.serial_no_series = f"{code}-.###"
            item.description = self.description or self.article_name
            item.standard_rate = self.client_total
            item.insert(ignore_permissions=True)
            target = item.name
        # persist the link without re-triggering validate/on_update
        self.db_set("item", target, update_modified=False)


def _file_content(file_url):
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_("Uploaded file not found: {0}").format(file_url))
    return frappe.get_doc("File", name).get_content()


def _pdf_desc(m):
    if m["kind"] == "sheet":
        return f"{m['name']} {m['thickness']:g}mm — {m['qty']} sheet(s)"
    if m["kind"] == "laminate":
        return f"{m['name']} laminate — {m['qty']} sheet(s)"
    if m["kind"] == "edge":
        return f"{m['name']} edge banding — {m['qty']} roll(s)"
    return f"{m['name']} — {m['qty']} nos"
