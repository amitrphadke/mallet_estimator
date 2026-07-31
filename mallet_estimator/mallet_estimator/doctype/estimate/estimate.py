import frappe
from frappe import _
from frappe.model.document import Document


class Estimate(Document):
    def validate(self):
        # Only a draft (docstatus 0) re-pulls SKUs. Once submitted (approved) the
        # SKU list and totals are frozen as the baseline; changes go via Amend.
        if self.docstatus == 0:
            self.aggregate_project_skus()

    @frappe.whitelist()
    def refresh_skus(self):
        """Re-pull every Estimate SKU of this Project into a draft estimate.
        Used by the 'Refresh SKUs' button after adding a SKU post-creation."""
        if self.docstatus != 0:
            frappe.throw(_("This estimate is approved (submitted). Amend it to change the SKUs."))
        self.aggregate_project_skus()
        self.save(ignore_permissions=True)
        return {"count": len(self.skus), "client": self.total_client}

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

    @frappe.whitelist()
    def create_work_orders(self):
        """Create a draft native Work Order per SKU from its BOM, linked to this
        Project (so material + labour actuals roll up to the Project Margin report).
        Submitting each Work Order — native ERPNext — generates the Job Cards, one
        per phase at its workstation. Per-SKU errors are collected, not fatal."""
        company = _default_company()
        abbr = frappe.db.get_value("Company", company, "abbr")

        def leaf_wh(name):
            full = f"{name} - {abbr}"
            return full if frappe.db.exists("Warehouse", full) else None

        wip = leaf_wh("Assembly Area")          # in-process stock
        fg = leaf_wh("Packed / Dispatch")       # finished good
        # Sales Order created from our Quotation (native), if any — links the WO to it.
        so = frappe.db.get_value("Sales Order Item", {"prevdoc_docname": self.quotation}, "parent") \
            if self.quotation else None

        made, errors = [], []
        for row in self.skus:
            try:
                s = frappe.get_doc("Estimate SKU", row.estimate_sku)
                if not s.item:
                    errors.append(f"{s.name}: no linked Item")
                    continue
                bom = frappe.db.get_value("BOM", {"item": s.item, "is_active": 1, "is_default": 1}, "name") \
                    or frappe.db.get_value("BOM", {"item": s.item, "is_active": 1}, "name")
                if not bom:
                    errors.append(f"{s.name}: no active BOM — click Build BOMs first")
                    continue
                wo = frappe.new_doc("Work Order")
                wo.production_item = s.item
                wo.bom_no = bom
                wo.qty = 1
                wo.company = company
                if self.project:
                    wo.project = self.project      # <- carries actuals to Project Margin
                if so:
                    wo.sales_order = so
                if wip:
                    wo.wip_warehouse = wip
                if fg:
                    wo.fg_warehouse = fg
                wo.insert(ignore_permissions=True)  # draft — user reviews + submits
                made.append(wo.name)
            except Exception as exc:
                errors.append(f"{row.estimate_sku}: {exc}")
        return {"work_orders": made, "errors": errors}


def _default_company():
    c = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")
    if not c:
        names = frappe.get_all("Company", pluck="name", limit=1)
        c = names[0] if names else None
    if not c:
        frappe.throw(_("No Company found. Create a Company first."))
    return c


def _ensure_operation(op_row):
    name = "Miscellaneous - extra" if getattr(op_row, "is_misc", 0) else (
        getattr(op_row, "operation", None) or getattr(op_row, "phase", None) or "")
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
    # make it the article's default BOM so native Work-Order creation finds it
    frappe.db.set_value("Item", s.item, "default_bom", bom.name, update_modified=False)
    return bom.name
