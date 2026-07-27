import frappe
from frappe import _
from frappe.model.document import Document

from mallet_estimator import opencutlist
from mallet_estimator.estimator import STEP_TEMPLATE, calc_sku, sku_code


def get_default_item_group():
    return (
        frappe.db.get_single_value("Stock Settings", "item_group")
        or ("Products" if frappe.db.exists("Item Group", "Products") else None)
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )


def ensure_material_item(code, uom="Nos"):
    """Return the rate-card price for a material Item, creating it (rate 0) if new."""
    if frappe.db.exists("Item", code):
        return frappe.db.get_value("Item", code, "standard_rate") or 0
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
        self.compute_costs()

    def on_update(self):
        if self.create_item:
            self.sync_item()

    # --- steps -------------------------------------------------------------
    def ensure_steps(self):
        if self.labor:
            return
        for t in STEP_TEMPLATE:
            self.append("labor", {
                "phase": t["phase"],
                "machine_key": t.get("machine") or "",
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
        if self.auto_name:
            self.sku_code = sku_code(self.customer_display_name(), self.room, self.article_name)
        if not self.sku_code:
            self.sku_code = self.article_name or self.name

    # --- costs -------------------------------------------------------------
    def compute_costs(self):
        settings = frappe.get_single("Estimate Settings")
        for m in self.materials:
            if not m.line_cost and m.unit_cost:
                m.line_cost = (m.qty or 1) * m.unit_cost
        r = calc_sku(self, settings)
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

    lines = opencutlist.aggregate(
        rows,
        sheet_length_mm=settings.sheet_length_mm or 2440,
        sheet_width_mm=settings.sheet_width_mm or 1220,
        wastage_pct=settings.wastage_pct if settings.wastage_pct not in (None, "") else 12,
    )
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
    doc.save()
    return {
        "parts": len(rows),
        "materials": len(lines),
        "priced": priced,
        "unpriced": len(lines) - priced,
        "material_cost": doc.material_cost,
    }
