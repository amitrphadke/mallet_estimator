import frappe
from frappe import _
from frappe.model.document import Document


class Estimate(Document):
    def validate(self):
        self.aggregate_project_skus()

    def aggregate_project_skus(self):
        """Rebuild the SKU list from every Estimate SKU linked to this Project
        (no manual add, so a SKU can't be counted twice) and roll up the totals."""
        names = frappe.get_all(
            "Estimate SKU", filters={"project": self.project}, order_by="room asc, article_name asc", pluck="name"
        ) if self.project else []
        self.set("skus", [])
        totals = dict(material=0, labor=0, overhead=0, design=0, internal=0, client=0)
        for name in names:
            s = frappe.get_doc("Estimate SKU", name)
            self.append("skus", {
                "estimate_sku": s.name,
                "item": s.item,
                "room": s.room,
                "article_name": s.article_name,
                "internal_cost": s.internal_cost,
                "client_total": s.client_total,
            })
            totals["material"] += s.material_cost or 0
            totals["labor"] += s.labor_cost or 0
            totals["overhead"] += s.overhead_cost or 0
            totals["design"] += s.design_cost or 0
            totals["internal"] += s.internal_cost or 0
            totals["client"] += s.client_total or 0
        self.total_material = totals["material"]
        self.total_labor = totals["labor"]
        self.total_overhead = totals["overhead"]
        self.total_design = totals["design"]
        self.total_internal = totals["internal"]
        self.total_client = totals["client"]

    @frappe.whitelist()
    def create_quotation(self):
        if self.quotation and frappe.db.exists("Quotation", self.quotation):
            frappe.throw(_("Quotation {0} already exists for this estimate.").format(self.quotation))
        if not self.skus:
            frappe.throw(_("Add at least one SKU (link SKUs to this Project) before creating a Quotation."))

        quo = frappe.new_doc("Quotation")
        quo.quotation_to = "Customer"
        quo.party_name = self.customer
        quo.order_type = "Sales"
        if self.project:
            quo.project = self.project
        for row in self.skus:
            s = frappe.get_doc("Estimate SKU", row.estimate_sku)
            if not s.item:
                frappe.throw(_("SKU {0} has no linked Item. Open it, tick 'Create Item' and save.").format(s.name))
            quo.append("items", {
                "item_code": s.item,
                "qty": 1,
                "rate": s.client_total,
                "description": s.description or s.article_name,
            })
        quo.insert(ignore_permissions=True)
        self.db_set("quotation", quo.name)
        return quo.name

    @frappe.whitelist()
    def build_boms(self):
        """Create a submitted BOM per SKU (materials + operations) so ERPNext can
        drive Work Orders and Job Cards. Native Sales Order -> Work Order takes it
        from here (it handles warehouses). Per-SKU errors are collected, not fatal."""
        company = _default_company()
        made, errors = [], []
        for row in self.skus:
            try:
                s = frappe.get_doc("Estimate SKU", row.estimate_sku)
                if not s.item:
                    errors.append(f"{s.name}: no linked Item")
                    continue
                made.append(_build_sku_bom(s, company))
            except Exception as exc:
                errors.append(f"{row.estimate_sku}: {exc}")
        return {"boms": made, "errors": errors}


def _default_company():
    c = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")
    if not c:
        names = frappe.get_all("Company", pluck="name", limit=1)
        c = names[0] if names else None
    if not c:
        frappe.throw(_("No Company found. Create a Company first."))
    return c


def _ensure_operation(op_row):
    name = "Miscellaneous - extra" if getattr(op_row, "is_misc", 0) else (op_row.phase or "")
    name = name.replace(" / ", " - ").replace("/", "-").strip()
    if name and not frappe.db.exists("Operation", name):
        o = frappe.new_doc("Operation")
        o.name = name
        o.workstation = op_row.workstation
        o.insert(ignore_permissions=True, set_name=name)
    return name


def _build_sku_bom(s, company):
    bom = frappe.new_doc("BOM")
    bom.item = s.item
    bom.company = company
    bom.quantity = 1
    bom.with_operations = 1
    bom.rm_cost_as_per = "Valuation Rate"
    for m in s.materials:
        if not m.item:
            continue
        bom.append("items", {"item_code": m.item, "qty": m.qty or 1, "rate": m.unit_cost or 0})
    if not bom.items:
        frappe.throw(_("SKU {0} has no priced material Items to put in a BOM.").format(s.name))
    for op in s.labor:
        if getattr(op, "is_misc", 0) and not s.include_misc:
            continue
        crew_min = (op.qty or 0) * (op.carp_min or 0)
        if crew_min <= 0:
            continue
        op_name = _ensure_operation(op)
        if op_name and op.workstation:
            bom.append("operations", {"operation": op_name, "workstation": op.workstation, "time_in_mins": crew_min})
    bom.insert(ignore_permissions=True)
    bom.submit()
    return bom.name
