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

    def work_kind(self):
        from mallet_estimator import estimator as E
        return self.get("work_type") or E.NEW_WORK

    def is_repair(self):
        from mallet_estimator import estimator as E
        return self.work_kind() == E.REPAIR

    def is_site_work(self):
        """Repair and Supply & Install both happen at the client's home and
        share one labour model. What separates them is where the material
        comes from — barely any, versus a finished article bought in."""
        from mallet_estimator import estimator as E
        return self.work_kind() in E.SITE_WORK

    def validate(self):
        # Repair is a different UNIT of estimation, not a variant of new work:
        # activities on things that already exist, no parts, no nesting, no
        # décor map, no factory. It gets its own short pipeline rather than
        # threading a dozen `if repair` branches through the article one.
        if self.is_site_work():
            self.validate_site_work()
            return
        self.wipe_on_cleared_files()
        self.ensure_steps()
        self.ensure_design_steps()
        self.ensure_step_remarks()
        self.ensure_custom_operations()
        self.maybe_import()
        self.pull_decor_masters()
        self.pull_line_decors()
        self.apply_decor_map()
        self.refresh_material_rates()
        self.price_material_lines()
        self.compute_code()
        self.enforce_locked_qty()
        self.derive_joinery()
        self.compute_costs()
        self.build_cost_breakup()

    # --- Repair (R1) --------------------------------------------------------

    def validate_site_work(self):
        """The on-site pipeline, shared by Repair and Supply & Install. Short
        by design: no parts, no nesting, no décor map, no factory.

        The one branch is where material comes from. A REPAIR's material is
        incidental and named on the activity rows, so it is derived from them.
        A SUPPLY & INSTALL's material IS the job — a finished article at the
        vendor's quoted rate, typed on its own line — so the lines are left
        exactly as entered."""
        self.import_repair_csv()
        self.compute_repair_rows()
        if self.is_repair():
            self.derive_repair_materials()
        self.refresh_material_rates()
        self.price_material_lines()
        self.compute_code()
        self.compute_site_costs()
        self.build_cost_breakup()

    def import_repair_csv(self):
        """Read the shop's repair estimation sheet into the activity table.
        Same contract as the OCL import: on save, when the file is new."""
        from mallet_estimator import repair_csv as R
        if not self.get("repair_csv") or self._frozen():
            return
        if self.get("repair_activities") and not self.has_value_changed("repair_csv"):
            return
        content = _file_content(self.repair_csv)
        if isinstance(content, bytes):
            content = content.decode("utf-8", "ignore")
        activities, warnings = R.parse_repair_csv(content or "")
        if not activities:
            frappe.throw(_("No activities found in the CSV.<br>{0}").format("<br>".join(warnings)))
        self.set("repair_activities", [])
        for a in activities:
            item = a.get("material_item")
            self.append("repair_activities", {
                "room": a["room"], "target": a["target"], "activity": a["activity"],
                "description": a["description"], "status": a["status"],
                "qty": a["qty"], "uom": _uom_or_none(a.get("material_uom")),
                "carpenters": a["carpenters"], "carp_min": a["carp_min"],
                "helpers": a["helpers"], "helper_min": a["helper_min"],
                "material_note": a["material_note"],
                "material_item": item if item and frappe.db.exists("Item", item) else None,
                "material_qty": a["qty"], "material_uom": _uom_or_none(a.get("material_uom")),
                "workstation": a["workstation"], "remarks": a["remarks"],
            })
        if warnings:
            frappe.msgprint(
                "<br>".join(frappe.utils.escape_html(w) for w in warnings),
                title=_("Repair sheet — read this"), indicator="orange")

    def compute_repair_rows(self):
        """Row minutes are COMPUTED (qty × crew × min/unit), never typed — a
        hand-maintained total is the thing that goes stale first."""
        from mallet_estimator.estimator import repair_row_minutes
        for row in self.get("repair_activities") or []:
            row.carp_total, row.helper_total = repair_row_minutes(row)

    def derive_repair_materials(self):
        """Activity material → ordinary material lines, so repair inherits the
        whole pricing chain: price-list rate, per-line discount, tax policy vs
        applied, landed amount. Rows the user added by hand are kept."""
        manual = [m.as_dict() for m in (self.get("materials") or []) if m.get("is_manual")]
        self.set("materials", [])
        for row in self.get("repair_activities") or []:
            note = (row.get("material_note") or "").strip()
            if not note and not row.get("material_item"):
                continue
            qty = float(row.get("material_qty") or 0) or float(row.get("qty") or 0) or 1
            if row.get("material_item"):
                rate = inventory.material_rate(row.material_item)[0]
                self.append("materials", {
                    "item": row.material_item, "material": row.material_item,
                    "description": f"{row.activity} — {note or row.material_item}"[:140],
                    "qty": qty, "uom": row.get("material_uom"),
                    "unit_cost": rate, "line_cost": qty * rate,
                })
            else:
                # No Item yet: the estimator types what it will cost. Flagged as
                # manual so nothing pretends this came off the price list.
                amount = float(row.get("material_amount") or 0)
                self.append("materials", {
                    "material": note,
                    "description": f"{row.activity} — {note}"[:140],
                    "qty": qty, "uom": row.get("material_uom"),
                    "unit_cost": (amount / qty) if qty else 0,
                    "line_cost": amount, "is_manual": 1,
                })
        for m in manual:
            self.append("materials", {
                "item": m.get("item"), "material": m.get("material"),
                "description": m.get("description"), "qty": m.get("qty") or 0,
                "uom": m.get("uom"), "unit_cost": m.get("unit_cost") or 0,
                "line_cost": (m.get("qty") or 0) * (m.get("unit_cost") or 0),
                "customer_supplied": m.get("customer_supplied") or 0, "is_manual": 1,
            })

    def compute_site_costs(self):
        """On-site cost = material + labour, where labour is billed at the
        GREATER of wages-plus-margin and the site visit floor. No factory
        overhead — nothing here happens in the factory.

        The material margin is the whole difference between the two kinds:
        repair material rides the ordinary material markup, while a bought-in
        finished article gets its own thinner policy, because a client can
        look up what a door costs and cannot look up what ply costs."""
        from mallet_estimator import estimator as E
        from mallet_estimator.estimator import bought_out_value, calc_repair
        settings = frappe.get_single("Estimate Settings")
        markup = (float(self.get("margin_labor") or 0)
                  if self.get("use_custom_margins") else None)
        r = calc_repair(self.get("repair_activities"), settings, markup_pct=markup,
                        visits=self.get("repair_visits"))
        self._repair = r
        material_cost = sum(float(m.line_cost or 0) for m in (self.materials or [])
                            if not m.get("customer_supplied"))
        if self.work_kind() == E.SUPPLY_INSTALL:
            client_material, mat_markup = bought_out_value(material_cost, settings)
        else:
            mat_markup = (float(self.get("margin_material") or 0)
                          if self.get("use_custom_margins")
                          else float(settings.get("markup_material") or 0))
            client_material = material_cost * (1 + mat_markup / 100.0)
        values = {
            "material_cost": material_cost,
            "labor_cost": r["labor_cost"],
            "machine_cost": 0, "rent_cost": 0, "overhead_cost": 0,
            "design_cost": 0, "joinery_cost": 0, "transport_cost": 0,
            "carp_min_total": r["carp_min"], "helper_min_total": r["helper_min"],
            "repair_labor_cost": r["labor_cost"],
            "repair_visit_amount": r["visit_amount"],
            "repair_to_inspect": r["to_inspect"],
            "client_repair": r["client_repair"],
            "client_material": client_material,
            "client_design_exec": r["client_repair"],
            "internal_cost": material_cost + r["labor_cost"],
            "client_total": client_material + r["client_repair"],
            "est_days": round(r["est_days"], 1),
        }
        for k, v in values.items():
            if self.meta.has_field(k):
                self.set(k, v)
        if not self.get("repair_visits"):
            if self.meta.has_field("repair_visits"):
                self.repair_visits = r["visits"]

    @frappe.whitelist()
    def reset_decor_map(self):
        """Rebuild the décor slot tables from the CURRENT material lines.

        Slots accumulate: every import adds the letters it needs and nothing
        ever took the old ones away, so a SKU re-imported from a changed CSV
        ends up carrying letters no material line refers to — five laminate
        slots for the two the job actually uses. Pruning happens on import,
        but only on import, which is no help to an SKU imported before that
        existed.

        So this is the 'start over' button. Slots the lines still use KEEP
        their brand / code / name — that is the user's work and losing it
        would be worse than the clutter. Slots nothing refers to are dropped."""
        if self._frozen():
            frappe.throw(_("This SKU is quoted (frozen) — cancel and amend the estimate first."))
        live_lam, live_eb = set(), set()
        for m in self.materials or []:
            up = str(m.material or "").upper()
            key = decor.slot_key(str(m.material or ""))
            if not key:
                continue
            if up.startswith("EB_"):
                live_eb.add(key)
            elif up.startswith("SG_LAM") or up.startswith("SG_PLY"):
                live_lam.add(key)

        dropped = []

        def prune(table, live):
            keep = []
            for row in self.get(table) or []:
                if (row.slot or "").strip().lower() in live:
                    keep.append(row)
                else:
                    dropped.append(f"{row.slot}")
            self.set(table, keep)
            return {(r.slot or "").strip().lower() for r in keep}

        have_lam = prune("sku_decors", live_lam)
        have_eb = prune("sku_decor_edges", live_eb)
        # Any slot a line needs but the map lacks gets a blank row to fill in.
        added = []
        for key in sorted(live_lam - have_lam):
            self.append("sku_decors", {"slot": key, "domain": "Laminate"})
            added.append(key)
        for key in sorted(live_eb - have_eb):
            self.append("sku_decor_edges", {"slot": key, "thickness": 0.8, "width": 22})
            added.append(key)
        self.save()
        frappe.msgprint(
            _("Décor map rebuilt from the material lines.<br>Kept: <b>{0}</b><br>"
              "Dropped (no line uses them): <b>{1}</b><br>Added blank: <b>{2}</b>").format(
                len(self.get("sku_decors") or []) + len(self.get("sku_decor_edges") or []) - len(added),
                ", ".join(dropped) or "—", ", ".join(added) or "—"),
            title=_("Décor slots"), indicator="blue")
        return {"dropped": dropped, "added": added}

    def pull_decor_masters(self):
        """A Décor Slots row can simply POINT at a Mallet Decor master (search
        it, or create one inline from the same field). Its brand/code/name/
        physicals fill the row on save, so every downstream path — short_code,
        substitute_real_code, ensure_material_item — works unchanged."""
        if self._frozen() or not frappe.db.exists("DocType", "Mallet Decor"):
            return
        for table in ("sku_decors", "sku_decor_edges"):
            for row in self.get(table) or []:
                if not row.meta.has_field("decor") or not row.get("decor"):
                    continue
                d = frappe.db.get_value(
                    "Mallet Decor", row.decor,
                    ["brand", "code", "decor_name", "thickness", "width", "year", "short"],
                    as_dict=True)
                if not d:
                    continue
                row.brand, row.code, row.decor_name = d.brand, d.code, d.decor_name
                if d.year:
                    row.year = d.year
                if d.short:
                    row.short = d.short
                # physicals only when the master carries them (edge widths etc.)
                if d.thickness and row.meta.has_field("thickness"):
                    row.thickness = d.thickness
                if d.width and row.meta.has_field("width"):
                    row.width = d.width

    def ensure_custom_operations(self):
        """Extra work means extra ROWS — and an ad-hoc row (e.g. 'Cut glass
        square in wardrobe door') becomes a first-class master on first save:
        its Operation (+ Workstation, if named) is created when missing, with
        the row's minutes stored as Operation.mallet_min_per_unit, so BOMs and
        every later SKU see it like any standard step. Template steps and the
        misc row are untouched; existing masters are never overwritten."""
        if self._frozen():
            return
        template_ops = {t["phase"] for t in STEP_TEMPLATE} | \
                       {t["phase"] for t in DESIGN_STEP_TEMPLATE}
        for row in list(self.labor or []) + list(self.get("design_labor") or []):
            if row.get("is_misc"):
                continue
            op = (row.get("operation") or "").strip()
            if not op or op in template_ops:
                continue
            if row.meta.has_field("is_custom"):
                row.is_custom = 1
            ws = (row.get("workstation") or "").strip()
            try:
                if ws and not frappe.db.exists("Workstation", ws):
                    w = frappe.new_doc("Workstation")
                    w.workstation_name = ws
                    w.insert(ignore_permissions=True)
                    frappe.msgprint(
                        _("Created Workstation <b>{0}</b> — key its operating "
                          "costs so the operation prices correctly.").format(ws),
                        indicator="orange")
                if not frappe.db.exists("Operation", op):
                    o = frappe.new_doc("Operation")
                    o.name = op
                    if ws and frappe.db.exists("Workstation", ws):
                        o.workstation = ws
                    if o.meta.has_field("mallet_min_per_unit") and float(row.get("carp_min") or 0):
                        o.mallet_min_per_unit = float(row.carp_min)
                    o.insert(ignore_permissions=True, set_name=op)
                    frappe.msgprint(
                        _("Created Operation <b>{0}</b> ({1} min/unit) — available "
                          "on every SKU from now on.").format(op, row.get("carp_min") or 0),
                        indicator="blue")
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"ensure_custom_operations {op}")

    def wipe_on_cleared_files(self):
        """Clearing a source PDF wipes what was derived from it, so the SKU never
        carries stale imported data."""
        if not self.is_new() and (self.get("estimation_mode") or "") == "CSV-Nest" \
                and self.has_value_changed("parts_csv") and not self.parts_csv:
            self.set("materials", [])
            self.set("joinery_items", [])
            self.set("parts", [])
            self.unpriced_materials = ""
            for row in self.get("design_labor") or []:
                row.qty = 0
            self.import_drivers = ""
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
        if (self.get("estimation_mode") or "") == "CSV-Nest":
            if not self.parts_csv:
                return
            if self.materials and not self.has_value_changed("parts_csv") \
                    and not self.has_value_changed("estimation_mode"):
                return
            self.do_import()
            return
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
        if (self.get("estimation_mode") or "") == "CSV-Nest":
            from mallet_estimator import nest_import
            nest_import.run(self)
            return
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
                # slot maps to its slot INSTANCE (b on ..._b_a1 → row b1 —
                # SketchUp's paste-rename suffix namespaces the whole material);
                # other blocks (the non-deciding side) fill their own token.
                # Description slots may themselves carry suffixes (b1 = …).
                first_tok = (decor.trailing_slots(ph) or [""])[0]
                inst = decor.slot_key(ph)
                slot = inst if d["slot"] in (first_tok[:1], first_tok, inst) else d["slot"]
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

    def pull_line_decors(self):
        """Assign décor straight from a MATERIAL LINE: picking a Mallet Decor
        on an SG_/EB_ line writes it into the SKU's décor map for that line's
        slot, so apply_decor_map (next in the pipeline) re-points the item.
        Ply carries two faces — `decor` maps the deciding slot, `decor_ext`
        the second one. No table hopping, and the map stays the single source
        of truth."""
        if self._frozen():
            return
        for m in self.materials or []:
            if not (m.get("decor") or m.get("decor_ext")):
                continue
            code = str(m.material or "")
            toks = decor.trailing_slots(code)
            if not toks:
                continue
            up = code.upper()
            domain = "Edge Band" if up.startswith("EB_") else "Laminate"
            pairs = []
            if m.get("decor"):
                pairs.append((decor.slot_key(code), m.decor, domain))
            if m.get("decor_ext") and len(toks) > 1:
                # the second face's own slot instance
                pairs.append((toks[-1], m.decor_ext, "Laminate"))
            for slot, decor_name, dom in pairs:
                if slot and decor_name:
                    self._upsert_decor_slot(slot, decor_name, dom)

    def _upsert_decor_slot(self, slot, decor_name, domain):
        """Point one slot at a Mallet Decor, creating the row when missing."""
        d = frappe.db.get_value(
            "Mallet Decor", decor_name,
            ["brand", "code", "decor_name", "thickness", "width", "year", "short"],
            as_dict=True)
        if not d:
            return
        table = "sku_decor_edges" if domain == "Edge Band" else "sku_decors"
        slot = (slot or "").strip().lower()
        for row in self.get(table) or []:
            if (row.slot or "").strip().lower() == slot:
                target = row
                break
        else:
            target = self.append(table, {"slot": slot})
            if table == "sku_decors":
                target.domain = domain
        target.decor = decor_name
        target.brand, target.code, target.decor_name = d.brand, d.code, d.decor_name
        if d.get("short"):
            target.short = d.short
        if d.get("year"):
            target.year = d.year
        if d.get("thickness"):
            target.thickness = d.thickness
        if table == "sku_decor_edges" and d.get("width"):
            target.width = d.width

    def price_material_lines(self):
        """Per-line discount and tax on top of the price-list rate.

        Stock prices are kept PRE-tax, so tax is what is added on top. The
        policy rate is the item's own mallet_gst_pct when set, else the house
        GST%; a line may override it, and both are shown so the difference is
        visible. Discount applies to THIS line only — the price list keeps its
        rate, which stays the single rate authority."""
        house_gst = 18.0
        try:
            settings = frappe.get_single("Estimate Settings")
            if settings.meta.has_field("gst_pct") and settings.get("gst_pct") not in (None, ""):
                house_gst = float(settings.gst_pct)
        except Exception:
            pass
        disc_total = tax_total = net_total = tax_saved_total = client_value = 0.0
        for m in self.materials or []:
            qty = float(m.qty or 0)
            rate = float(m.unit_cost or 0)
            disc = max(0.0, min(100.0, float(m.get("discount_pct") or 0)))
            m.net_rate = rate * (1 - disc / 100.0)
            m.discount_amount = qty * rate * disc / 100.0
            m.line_cost = qty * m.net_rate
            policy = None
            if m.item and frappe.db.has_column("Item", "mallet_gst_pct"):
                policy = frappe.db.get_value("Item", m.item, "mallet_gst_pct")
            # A field nobody has keyed reads back as 0, which is
            # indistinguishable from "this item is genuinely zero-rated" —
            # and reading it as 0% quietly took GST off every line. We are a
            # GST business: an unkeyed item falls back to the house rate, and
            # a truly exempt item is expressed by overriding Applied Tax %.
            m.tax_rate_policy = float(policy) if policy else house_gst
            applied = m.get("tax_rate")
            applied_pct = float(applied) if applied not in (None, "") else float(m.tax_rate_policy)
            m.tax_discount_pct = float(m.tax_rate_policy) - applied_pct
            m.tax_amount = float(m.line_cost or 0) * applied_pct / 100.0
            m.tax_saved = float(m.line_cost or 0) * float(m.tax_discount_pct) / 100.0
            m.amount_with_tax = float(m.line_cost or 0) + float(m.tax_amount or 0)
            # A client-supplied line is not bought by US, so none of it enters
            # our cost — but it is still part of what the job is WORTH, and
            # an estimate that hides those numbers cannot be read as a whole
            # picture. So the line keeps its full pricing and is simply left
            # out of the rollups, with its own total beside them.
            if m.get("customer_supplied"):
                client_value += float(m.amount_with_tax or 0)
                continue
            disc_total += float(m.discount_amount or 0)
            tax_saved_total += float(m.tax_saved or 0)
            tax_total += float(m.tax_amount or 0)
            net_total += float(m.line_cost or 0)
        if self.meta.has_field("material_discount_total"):
            self.material_discount_total = disc_total
        if self.meta.has_field("material_tax_total"):
            self.material_tax_total = tax_total
        if self.meta.has_field("material_total_with_tax"):
            self.material_total_with_tax = net_total + tax_total
        if self.meta.has_field("material_tax_saved_total"):
            self.material_tax_saved_total = tax_saved_total
        if self.meta.has_field("client_supplied_value"):
            self.client_supplied_value = client_value

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
            # line_cost is set by price_material_lines (discount-aware), which
            # runs immediately after this in validate().
            row.line_cost = (row.qty or 0) * row.unit_cost
            if source == "unset" and row.item not in unpriced:
                unpriced.append(row.item)
        self.unpriced_materials = ", ".join(unpriced)

    # classify_hardware's buckets -> the driver keys used by the locked ops
    _HW_BUCKETS = {"minifix": "minifix", "screws": "screw", "hinges": "hinge",
                   "rails": "rail", "handles": "handle", "shelf_supports": "shelf"}

    def _hw_line_counts(self):
        """Hardware quantities summed from the CURRENT material lines (imported
        AND manual).

        Lines carry the REAL designation (HWD_AH_SC_0 = Auto Hinge Soft Close),
        which contains none of the words the driver buckets look for — so the
        line's DESCRIPTION (which carries the OCL category, '… · HWD_Hinge')
        is part of the haystack. Without it, designation-level hardware scored
        zero hinges/rails and Install Hardware, Minifix Boring and Drilling
        silently lost their quantities."""
        tot = {"minifix": 0, "screw": 0, "hinge": 0, "rail": 0, "handle": 0, "shelf": 0}
        for m in self.materials or []:
            code = str(m.item or m.material or "")
            if not code.upper().startswith("HWD"):
                continue
            haystack = f"{m.item or ''} {m.material or ''} {m.description or ''}"
            key = self._HW_BUCKETS.get(opencutlist.classify_hardware(haystack))
            if key:
                tot[key] += m.qty or 0
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
        (all pre-tax). A MULTI-ROOM (combined) SKU has no meaningful outer dims —
        its area is the SUM of the project's individual SKUs' facial areas. The
        Facial Area Override field beats both. None when nothing resolves."""
        sqft = float(self.get("facial_sqft_override") or 0)
        if not sqft and self.get("multi_room") and self.project:
            for name in frappe.get_all(
                "Estimate SKU",
                filters={"project": self.project, "name": ["!=", self.name], "multi_room": ["!=", 1]},
                pluck="name",
            ):
                other = frappe.get_doc("Estimate SKU", name)
                blk = other.facial_sqft_block() or {}
                sqft += float(blk.get("sqft") or 0)
        if not sqft:
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
        self.sku_code = self._unique_code(self.sku_code)

    def _unique_code(self, code):
        """Two wardrobes for one customer in one room compute the SAME code —
        which used to matter silently: sync_item saw the code already existed
        and pointed the second SKU at the FIRST one's ERPNext Item, so both
        wrote to one Item and its price became whichever saved last.

        A clash now takes a numeric suffix (YS_MB_WAR_2) and says so. Sharing
        an Item between SKUs is legitimate — the same article quoted on two
        estimates — but it should happen because someone chose it, never
        because two names abbreviated the same way."""
        if not code or self.is_new() is False and self.get("rates_frozen"):
            return code
        taken = set(frappe.get_all(
            "Estimate SKU",
            filters={"sku_code": ["like", f"{code}%"], "name": ["!=", self.name or ""]},
            pluck="sku_code") or [])
        if code not in taken:
            return code
        n = 2
        while f"{code}_{n}" in taken:
            n += 1
        unique = f"{code}_{n}"
        frappe.msgprint(
            _("<b>{0}</b> is already used by another SKU, so this one is <b>{1}</b>. "
              "Rename the article if you meant them to be different things — the "
              "code is built from customer, room and article name.").format(code, unique),
            title=_("Duplicate SKU code"), indicator="orange")
        return unique

    # --- costs -------------------------------------------------------------
    def compute_costs(self):
        settings = frappe.get_single("Estimate Settings")
        # line_cost is NOT recomputed here. price_material_lines already set it
        # from the DISCOUNTED rate earlier in the pipeline; recomputing it as
        # qty x unit_cost threw every per-line discount away one step after it
        # was applied. That function is the single authority for line money.
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
        if (self.get("estimation_mode") or "") == "CSV-Nest":
            if not self.parts_csv:
                frappe.throw(_("Attach the OpenCutList Part List CSV first (CSV-Nest mode)."))
        elif not self.estimate_pdf:
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
        # NEVER writes. Opening a form must not modify the database — and this
        # is where the reload loop came from: recompute ran refresh_material_rates
        # + compute_costs and compared the result against the stored values, but
        # a SAVE runs the FULL validate pipeline (price_material_lines included),
        # so the two computations could not agree. Every open therefore saw a
        # "change", saved, bumped `modified`, and the reload that followed did it
        # all again. Comparing a partial recomputation against the product of a
        # complete one can never converge.
        #
        # So it reports instead: the caller shows a "rates have moved, save to
        # re-price" hint and the user decides. A rate change is worth knowing
        # about; it is not worth a silent write behind their back.
        before = float(self.client_total or 0)
        before_days = float(self.get("est_days") or 0)
        before_std = [row.std_min for row in (self.labor or [])]
        before_rates = [row.unit_cost for row in (self.materials or [])]
        self.refresh_material_rates()
        self.price_material_lines()
        self.compute_costs()
        moved = (
            abs(float(self.client_total or 0) - before) > 0.005
            or before_std != [row.std_min for row in (self.labor or [])]
            or before_rates != [row.unit_cost for row in (self.materials or [])]
            or abs(float(self.get("est_days") or 0) - before_days) > 0.05
        )
        # the in-memory changes are discarded with this document object
        return {"changed": False, "stale": bool(moved),
                "client_total": float(self.client_total or 0)}

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
            # A renamed article changes the SKU code, and the Item must follow
            # it. Leaving the Item on its old code strands the name a person
            # searches by — you fix "MB_WAR_CSV" to "Wardrobe" and the stock
            # ledger still says YS_MB_MB_. frappe.rename_doc re-points every
            # link (BOMs, prices, stock entries) as part of the rename.
            if self.item != code and not frappe.db.exists("Item", code):
                try:
                    frappe.rename_doc("Item", self.item, code, force=True,
                                      show_alert=False, ignore_permissions=True)
                    self.item = code
                    frappe.msgprint(
                        _("ERPNext Item renamed to <b>{0}</b> to match the SKU code.").format(code),
                        indicator="blue", alert=True)
                except Exception:
                    # A rename can be refused (a submitted document may hold
                    # it). Say so rather than leaving the mismatch unexplained.
                    frappe.log_error(frappe.get_traceback(), f"rename item {self.item} -> {code}")
                    frappe.msgprint(
                        _("The SKU code is now <b>{0}</b> but its ERPNext Item is still "
                          "<b>{1}</b> — the rename was refused, most likely because a "
                          "submitted document uses it. Rename it by hand if you need them to match.")
                        .format(code, self.item), indicator="orange")
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


def _uom_or_none(name):
    """A UOM typed on the sheet ('Nos', 'Cubic Feet') may not exist as a master
    — never fail an import over a unit label."""
    name = (name or "").strip()
    return name if name and frappe.db.exists("UOM", name) else None


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
