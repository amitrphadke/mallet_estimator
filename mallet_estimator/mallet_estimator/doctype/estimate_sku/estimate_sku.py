import json

import frappe
from frappe import _
from frappe.model.document import Document

from mallet_estimator import opencutlist, estimate_pdf, inventory
from mallet_estimator.estimator import (
    STEP_TEMPLATE, OPERATION_STANDARDS, OPERATION_WORKSTATION, calc_sku, sku_code,
    customer_initials, op_phase, live_workstation_rates,
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
        mins = OPERATION_STANDARDS.get(op_name, {}).get("min_per_unit", 0)
    if not ws:
        ws = OPERATION_WORKSTATION.get(op_name)
    return mins, ws


def get_default_item_group():
    return (
        frappe.db.get_single_value("Stock Settings", "item_group")
        or ("Products" if frappe.db.exists("Item Group", "Products") else None)
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )


class EstimateSKU(Document):
    def validate(self):
        self.ensure_steps()
        self.maybe_import()
        self.compute_code()
        self.enforce_locked_qty()
        self.compute_costs()

    def maybe_import(self):
        """When an OpenCutList Estimate PDF is attached (or changed), import the
        material quantities + operation quantities automatically on save — no
        button. The Parts CSV, if attached, gives the edge-banding part count and
        the QR part list."""
        if not self.estimate_pdf:
            return
        if self.materials and not self.has_value_changed("estimate_pdf") and not self.has_value_changed("parts_csv"):
            return
        self.do_import()

    def do_import(self):
        settings = frappe.get_single("Estimate Settings")
        materials = estimate_pdf.parse_estimate_pdf(estimate_pdf.read_pdf_text(_file_content(self.estimate_pdf)))
        if not materials:
            frappe.throw(_("No materials found in the Estimate PDF. Is it an OpenCutList Estimate export?"))

        part_count, parts, agg, hardware = 0, [], None, []
        if self.parts_csv:
            content = _file_content(self.parts_csv)
            if isinstance(content, bytes):
                content = content.decode("utf-8", "ignore")
            rows = opencutlist.parse_opencutlist_csv(content)
            parts = opencutlist.parts_list(rows)
            part_count = len(parts)
            hardware = opencutlist.hardware_list(rows)
            # The CSV gives ACCURATE edge-banding running metres (the PDF only
            # gives whole rolls). Use it so edge banding is stocked/costed per metre.
            agg = opencutlist.aggregate(
                rows,
                sheet_length_mm=float(getattr(settings, "sheet_length", 0) or 2440),
                sheet_width_mm=float(getattr(settings, "sheet_width", 0) or 1220),
                wastage_pct=float(getattr(settings, "wastage_pct", 0) or 12),
            )

        self.set("materials", [])
        unpriced = []
        # Sheet goods, laminate, solid wood and hardware come from the PDF (its
        # nesting is authoritative); edge banding comes from the CSV in metres.
        for m in materials:
            if m.get("kind") == "edge" and agg:
                continue  # replaced by the CSV metre lines below
            if m.get("kind") == "hardware" and hardware:
                continue  # replaced by designation-level CSV hardware below
            self._add_material_line(
                m["name"], m.get("kind"), m.get("thickness") or 0, m["qty"] or 0,
                _pdf_desc(m), unpriced,
            )
        if agg:
            for ln in agg["lines"]:
                if ln["kind"] == "edge":
                    self._add_material_line(ln["material"], "edge", 0, ln["qty"], ln["desc"], unpriced)
        # Hardware from the CSV Designation — the real SKU (HWD_AH_SC_0 = Auto
        # Hinge Soft Close 0°) with the part's physical dimensions. The PDF only
        # knows the coarse Material name (HWD_Hinge), so the CSV wins when present.
        for h in hardware:
            cat = f" · {h['category']}" if h.get("category") and h["category"] != h["code"] else ""
            self._add_material_line(
                h["code"], "hardware", h.get("thickness") or 0, h["qty"],
                f"{h['code']} — {h['qty']} nos{cat}", unpriced, dims=h,
            )

        self.unpriced_materials = ", ".join(unpriced)
        if unpriced:
            frappe.msgprint(
                _("These materials have no price in ERPNext yet — set a Purchase/valuation "
                  "or standard rate on the Item so the estimate can cost them:<br><b>{0}</b>")
                .format(", ".join(unpriced)),
                title=_("Materials need a price"), indicator="orange",
            )

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
        self.import_drivers = json.dumps(opq)

        if parts:
            self.set("parts", [])
            for p in parts:
                self.append("parts", {
                    "part_no": p["part_no"], "designation": p["designation"], "material": p["material"],
                    "tag": p["tag"], "length": p["length"], "width": p["width"], "thickness": p["thickness"],
                    "cut": p.get("cut", 1), "edge_banded": p.get("edge_banded", 0),
                    "laminated": p.get("laminated", 0),
                })

    def _add_material_line(self, name, kind, thickness, qty, desc, unpriced, dims=None):
        """Create/link the ERPNext stock Item and append a costed material row.
        Length/width/thickness are fetched from the Item (fetch_from) — single
        source of truth, not stored redundantly on the line."""
        code, rate, source = inventory.ensure_material_item(name, kind=kind, thickness=thickness, dims=dims)
        self.append("materials", {
            "item": code, "material": name, "description": (desc or name)[:140],
            "qty": qty, "uom": inventory.stock_uom_for(kind),
            "unit_cost": rate, "line_cost": qty * (rate or 0),
        })
        if source == "unset":
            unpriced.append(code)

    def enforce_locked_qty(self):
        """Locked operations (sheet lamination/tape/cutting, edge banding) always
        take their computed qty from the last import — they can't be hand-edited."""
        if not self.import_drivers:
            return
        try:
            q = json.loads(self.import_drivers)
        except Exception:
            return
        for row in self.labor:
            op = op_phase(row)
            if op in estimate_pdf.LOCKED_OPERATIONS and op in q:
                row.qty = q[op]

    def on_update(self):
        if self.create_item:
            self.sync_item()
        self.refresh_project_estimates()

    def refresh_project_estimates(self):
        """Keep any DRAFT Estimate of this SKU's Project in sync, so a SKU added
        (or edited) after the Estimate was created is pulled in automatically.
        Submitted (approved) estimates are frozen and never touched."""
        if not self.project:
            return
        for name in frappe.get_all(
            "Estimate", filters={"project": self.project, "docstatus": 0}, pluck="name"
        ):
            try:
                est = frappe.get_doc("Estimate", name)
                est.aggregate_project_skus()
                est.save(ignore_permissions=True)
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
                "qty": 1,
            })

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
            self.sku_code = sku_code(self.customer_display_name(), self.room, self.article_name)
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
        for row in self.labor or []:
            row.std_min = operation_defaults(op_phase(row))[0]
        # Each phase is priced at its Workstation's live Net Hour Rate from the
        # ERPNext Manufacturing master (Rent + Wages + Machinery + Electricity +
        # Consumables). Wages are folded in — no per-row carpenter/helper charge.
        ws_rates = live_workstation_rates(settings)
        r = calc_sku(self, settings, ws_rates=ws_rates)
        for k in (
            "material_cost", "labor_cost", "machine_cost", "rent_cost", "overhead_cost",
            "design_cost", "internal_cost", "client_material", "client_design_exec",
            "client_total", "carp_min_total", "helper_min_total",
        ):
            self.set(k, r[k])

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
    def workstation_net_rates(self):
        """{workstation_name: Net Hour Rate} (+ __default__) so the form can price
        Phase Cost live as you edit Qty / Min / Operation — no save needed (I1)."""
        settings = frappe.get_single("Estimate Settings")
        rates = live_workstation_rates(settings)
        out = {name: (r.get("net_hr") or 0) for name, r in rates.items()}
        out["__default__"] = out.get(DEFAULT_WORKSTATION, 0)
        return out

    @frappe.whitelist()
    def reimport(self):
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
        """Re-price every step at the CURRENT Workstation Net Hour Rates and save
        only if the total actually moved. Called on form load so Phase Cost never
        shows a value that pre-dates a workstation-rate change."""
        before = self.client_total or 0
        self.compute_costs()
        if abs((self.client_total or 0) - before) > 0.005:
            self.save(ignore_permissions=True)
            return {"changed": True, "client_total": self.client_total}
        return {"changed": False, "client_total": self.client_total}

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
