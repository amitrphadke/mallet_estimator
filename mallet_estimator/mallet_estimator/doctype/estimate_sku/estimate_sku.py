import json

import frappe
from frappe import _
from frappe.model.document import Document

from mallet_estimator import opencutlist, estimate_pdf
from mallet_estimator.estimator import (
    STEP_TEMPLATE, OPERATION_STANDARDS, OPERATION_WORKSTATION, calc_sku, sku_code,
    customer_initials, op_phase,
)

DEFAULT_WORKSTATION = "Assembly Station"


def default_workstation(row):
    return OPERATION_WORKSTATION.get(op_phase(row), DEFAULT_WORKSTATION)


def workstation_rate_map():
    """Live rates from the ERPNext Workstation masters (so in-app edits apply)."""
    m = {}
    for w in frappe.get_all(
        "Workstation",
        fields=["name", "hour_rate_rent", "hour_rate_consumable", "hour_rate_labour", "hour_rate"],
    ):
        total = w.hour_rate or ((w.hour_rate_rent or 0) + (w.hour_rate_consumable or 0) + (w.hour_rate_labour or 0))
        m[w.name] = {
            "rent_hr": w.hour_rate_rent or 0,
            "dep_hr": w.hour_rate_consumable or 0,
            "labour_hr": w.hour_rate_labour or 0,
            "total_hr": total,
        }
    return m


def get_default_item_group():
    return (
        frappe.db.get_single_value("Stock Settings", "item_group")
        or ("Products" if frappe.db.exists("Item Group", "Products") else None)
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )


def ensure_material_item(code, uom="Nos"):
    """Return the best available inventory/rate-card price for a material Item,
    creating it (rate 0) if new. Prefers valuation rate, then last purchase, then
    the standard buying rate."""
    if frappe.db.exists("Item", code):
        v = frappe.db.get_value(
            "Item", code, ["valuation_rate", "last_purchase_rate", "standard_rate"], as_dict=True
        ) or {}
        return v.get("valuation_rate") or v.get("last_purchase_rate") or v.get("standard_rate") or 0
    item = frappe.new_doc("Item")
    item.item_code = code
    item.item_name = code[:140]
    item.item_group = get_default_item_group()
    item.stock_uom = uom or "Nos"
    item.is_stock_item = 0
    item.standard_rate = 0
    item.insert(ignore_permissions=True)
    return 0


class EstimateSKU(Document):
    def validate(self):
        self.ensure_steps()
        self.compute_code()
        self.enforce_locked_qty()
        self.compute_costs()

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
            if row.phase in estimate_pdf.LOCKED_OPERATIONS and row.phase in q:
                row.qty = q[row.phase]

    def on_update(self):
        if self.create_item:
            self.sync_item()

    # --- steps -------------------------------------------------------------
    def ensure_steps(self):
        if self.labor:
            # Backfill workstation on any row that is missing it.
            for row in self.labor:
                if not row.workstation:
                    row.workstation = default_workstation(row)
            return
        for t in STEP_TEMPLATE:
            self.append("labor", {
                "phase": t["phase"],
                "workstation": OPERATION_WORKSTATION.get(t["phase"], DEFAULT_WORKSTATION),
                "in_factory": t.get("in_factory", 0),
                "is_misc": t.get("is_misc", 0),
                "qty": 1,
                "carp_no": 1,
                "helper_no": 1,
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
            if not m.line_cost and m.unit_cost:
                m.line_cost = (m.qty or 1) * m.unit_cost
        ws_rates = workstation_rate_map() or None  # live Workstation master rates
        r = calc_sku(self, settings, ws_rates)
        for k in (
            "material_cost", "labor_cost", "machine_cost", "rent_cost", "overhead_cost",
            "design_cost", "internal_cost", "client_material", "client_design_exec",
            "client_total", "carp_min_total", "helper_min_total",
        ):
            self.set(k, r[k])

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
            item.item_group = get_default_item_group()
            item.stock_uom = "Nos"
            item.is_stock_item = 0
            item.description = self.description or self.article_name
            item.standard_rate = self.client_total
            item.insert(ignore_permissions=True)
            target = item.name
        # persist the link without re-triggering validate/on_update
        self.db_set("item", target, update_modified=False)


@frappe.whitelist()
def import_opencutlist(estimate_sku, csv_text):
    """Aggregate a native OpenCutList parts CSV into the SKU's material lines,
    pricing each from the ERPNext Item rate card (Items auto-created at rate 0).

    Module-level whitelisted function (called from the form by full dotted path).
    """
    doc = frappe.get_doc("Estimate SKU", estimate_sku)
    if not doc.has_permission("write"):
        frappe.throw(_("Not permitted to edit {0}").format(estimate_sku), frappe.PermissionError)

    settings = frappe.get_single("Estimate Settings")
    rows = opencutlist.parse_opencutlist_csv(csv_text or "")
    if not rows:
        frappe.throw(_("No parts found in the CSV. Is it a native OpenCutList export?"))

    agg = opencutlist.aggregate(
        rows,
        sheet_length_mm=settings.sheet_length_mm or 2440,
        sheet_width_mm=settings.sheet_width_mm or 1220,
        wastage_pct=settings.wastage_pct if settings.wastage_pct not in (None, "") else 12,
    )
    lines, drivers = agg["lines"], agg["drivers"]

    # Material lines, priced from the Item rate card.
    doc.set("materials", [])
    priced = 0
    for l in lines:
        code = opencutlist.item_code_for(l)
        rate = ensure_material_item(code, l.get("uom"))
        if rate:
            priced += 1
        qty = l.get("qty") or 0
        doc.append("materials", {
            "item": code,
            "material": l["material"],
            "description": (l["desc"] or "")[:140],
            "qty": qty,
            "unit_cost": rate,
            "line_cost": qty * (rate or 0),
        })

    # Auto-fill each operation's Qty from the material drivers, and default the
    # crew minutes/unit from the operation standard (without clobbering edits).
    for row in doc.labor:
        std = OPERATION_STANDARDS.get(op_phase(row))
        if not std:
            continue
        if std["qty_source"] != "manual":
            row.qty = drivers.get(std["qty_source"], 0) or 0
        if not float(row.carp_min or 0):
            row.carp_min = std["min_per_unit"]
        if not float(row.helper_min or 0):
            row.helper_min = std["min_per_unit"]

    doc.save()
    return {
        "parts": len(rows),
        "materials": len(lines),
        "priced": priced,
        "unpriced": len(lines) - priced,
        "drivers": drivers,
        "material_cost": doc.material_cost,
    }


def _file_content(file_url):
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_("Uploaded file not found: {0}").format(file_url))
    return frappe.get_doc("File", name).get_content()


def _pdf_item_code(m):
    if m["kind"] == "sheet" and m.get("thickness"):
        return f"{m['name']}_{m['thickness']:g}mm"
    return m["name"]


def _pdf_desc(m):
    if m["kind"] == "sheet":
        return f"{m['name']} {m['thickness']:g}mm — {m['qty']} sheet(s)"
    if m["kind"] == "laminate":
        return f"{m['name']} laminate — {m['qty']} sheet(s)"
    if m["kind"] == "edge":
        return f"{m['name']} edge banding — {m['qty']} roll(s)"
    return f"{m['name']} — {m['qty']} nos"


@frappe.whitelist()
def import_estimate(estimate_sku, pdf_file_url, csv_file_url=None):
    """Import accurate material quantities from the OpenCutList Estimate PDF and
    the part count from the parts CSV. Material lines are priced from ERPNext Item
    inventory rates; operation quantities follow the fixed mapping (locked ops
    1-4 computed and enforced, the rest editable defaults)."""
    doc = frappe.get_doc("Estimate SKU", estimate_sku)
    if not doc.has_permission("write"):
        frappe.throw(_("Not permitted to edit {0}").format(estimate_sku), frappe.PermissionError)

    materials = estimate_pdf.parse_estimate_pdf(estimate_pdf.read_pdf_text(_file_content(pdf_file_url)))
    if not materials:
        frappe.throw(_("No materials found in the PDF. Is it an OpenCutList Estimate export?"))

    part_count = 0
    parts = []
    if csv_file_url:
        content = _file_content(csv_file_url)
        if isinstance(content, bytes):
            content = content.decode("utf-8", "ignore")
        rows = opencutlist.parse_opencutlist_csv(content)
        parts = opencutlist.parts_list(rows)
        part_count = len(parts)

    # Material lines from PDF quantities, priced from inventory Items.
    doc.set("materials", [])
    priced = 0
    for m in materials:
        code = _pdf_item_code(m)
        rate = ensure_material_item(code, "Nos")
        if rate:
            priced += 1
        qty = m["qty"] or 0
        doc.append("materials", {
            "item": code,
            "material": m["name"],
            "description": _pdf_desc(m)[:140],
            "qty": qty,
            "unit_cost": rate,
            "line_cost": qty * (rate or 0),
        })

    # Operation quantities per the fixed mapping; store for locked-cell enforcement.
    opq = estimate_pdf.operation_quantities(materials, part_count)
    for row in doc.labor:
        op = op_phase(row)
        if op in opq:
            row.qty = opq[op]
        std = OPERATION_STANDARDS.get(op)
        if std and not float(row.carp_min or 0):
            row.carp_min = std["min_per_unit"]
            row.helper_min = std["min_per_unit"]
    doc.import_drivers = json.dumps(opq)
    doc.estimate_pdf = pdf_file_url
    if csv_file_url:
        doc.parts_csv = csv_file_url

    # Store the part list (with QR part numbers) for job-card tracking.
    if parts:
        doc.set("parts", [])
        for p in parts:
            doc.append("parts", {
                "part_no": p["part_no"],
                "designation": p["designation"],
                "material": p["material"],
                "tag": p["tag"],
                "length": p["length"],
                "width": p["width"],
                "thickness": p["thickness"],
            })

    doc.save()
    return {
        "materials": len(materials),
        "priced": priced,
        "unpriced": len(materials) - priced,
        "part_count": part_count,
        "operations": opq,
        "material_cost": doc.material_cost,
    }
