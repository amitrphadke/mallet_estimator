import frappe
from frappe import _
from frappe.model.document import Document


class Estimate(Document):
    def on_submit(self):
        """Approving the estimate FREEZES every SKU's rates — later price-list
        changes never alter what was quoted (the price list keeps the history)."""
        for row in self.skus or []:
            if row.estimate_sku:
                frappe.db.set_value("Estimate SKU", row.estimate_sku, "rates_frozen", 1,
                                    update_modified=False)

    def on_cancel(self):
        for row in self.skus or []:
            if row.estimate_sku:
                frappe.db.set_value("Estimate SKU", row.estimate_sku, "rates_frozen", 0,
                                    update_modified=False)

    def validate(self):
        # Only a draft (docstatus 0) re-pulls SKUs. Once submitted (approved) the
        # SKU list and totals are frozen as the baseline; changes go via Amend.
        if self.docstatus == 0:
            self.aggregate_project_skus()
        # Provisional allowances (F6) — amounts are a simple qty x assumed rate,
        # recomputed every save so the client-print subtotal is always right.
        self.compute_allowances()
        self.compute_transport_and_tax()

    def compute_allowances(self):
        total = 0
        for a in self.allowances or []:
            a.amount = (a.qty or 0) * (a.assumed_rate or 0)
            total += a.amount
        self.total_allowance = total

    def compute_transport_and_tax(self):
        """C1 — consolidated transport as an EDITABLE table: SKUs share trips, so
        the estimate's trip rows (change qty/rate, add more) are what the client
        pays. Rows are seeded once from the Estimate Settings rates. T1 — output
        GST is charged on top of the client total (quote plus GST, always)."""
        if not self.meta.has_field("total_transport"):
            return
        from mallet_estimator.estimator import transport_rates
        settings = frappe.get_single("Estimate Settings")
        rates = transport_rates(settings)
        if self.meta.has_field("transport_items"):
            if not self.get("transport_items"):
                for label, desc, rate in (
                    ("Big Tempo (inward)", "Ply + internal laminate + joinery hardware", rates["tempo"]),
                    ("External Laminate (inward)", "External laminate sheets", rates["ext_lam"]),
                    ("Client Hardware (inward)", "Hinges, rails, handles, lifts", rates["client_hw"]),
                    ("Outward Delivery", "Finished goods to site", rates["outward"]),
                ):
                    self.append("transport_items", {
                        "trip_type": label, "description": desc, "qty": 1, "rate": rate,
                    })
            total = 0
            for t in self.transport_items:
                t.amount = (t.qty or 0) * (t.rate or 0)
                total += t.amount
            self.total_transport = total
        else:
            self.total_transport = 0
        # aggregate_project_skus left the totals transport-free; add the shared
        # trips here, then output GST on the full client amount.
        if self.docstatus == 0:
            self.total_internal = (self.total_internal or 0) + self.total_transport
            self.total_client = (self.total_client or 0) + self.total_transport
        base = (self.total_client or 0)
        gst_pct = self.gst_pct if self.gst_pct is not None else 18
        self.total_gst = base * (gst_pct or 0) / 100.0
        self.total_with_gst = base + self.total_gst

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
            "Estimate SKU",
            filters={"project": self.project, "exclude_from_estimate": ["!=", 1]},
            order_by="room asc, article_name asc", pluck="name",
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
            # Per-SKU transport is a STANDALONE view (client_total already excludes
            # it) — strip it from internal too; the estimate's consolidated trips
            # are added in compute_transport_and_tax.
            totals["internal"] += (s.internal_cost or 0) - (s.get("transport_cost") or 0)
            totals["client"] += s.client_total or 0
        self.total_material = totals["material"]
        self.total_labor = totals["labor"]
        self.total_overhead = totals["overhead"]
        self.total_design = totals["design"]
        # Transport-free at this point; compute_transport_and_tax (which runs
        # right after in validate) adds the consolidated trips + GST on top.
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
        # Native output-GST template on the quotation when seeded.
        from mallet_estimator.install import GST_SALES_TEMPLATE_TITLE
        st = frappe.db.get_value("Sales Taxes and Charges Template",
                                 {"title": GST_SALES_TEMPLATE_TITLE}, "name")
        if st:
            quo.taxes_and_charges = st
            quo.run_method("set_taxes")
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
    # V3 — once an execution design exists, build the BOM from the CHOSEN actual
    # items (so Work Orders consume the real materials and Project margin reflects
    # actual cost). Before that, fall back to the estimate's generic materials.
    exec_rows = [r for r in (s.get("execution_materials") or []) if r.chosen_item]
    if exec_rows:
        for r in exec_rows:
            bom.append("items", {"item_code": r.chosen_item, "qty": r.actual_qty or 1, "rate": r.actual_rate or 0})
    else:
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
