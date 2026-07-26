import frappe
from frappe import _
from frappe.model.document import Document


class ExecutionEstimate(Document):
    def validate(self):
        self.rollup()

    def rollup(self):
        totals = dict(material=0, labor=0, overhead=0, design=0, internal=0, client=0)
        for row in self.skus:
            if not row.estimate_sku:
                continue
            s = frappe.get_doc("Estimate SKU", row.estimate_sku)
            row.item = s.item
            row.room = s.room
            row.article_name = s.article_name
            row.internal_cost = s.internal_cost
            row.client_total = s.client_total
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
            frappe.throw(_("Add at least one SKU before creating a Quotation."))

        quo = frappe.new_doc("Quotation")
        quo.quotation_to = "Customer"
        quo.party_name = self.customer
        quo.order_type = "Sales"
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
