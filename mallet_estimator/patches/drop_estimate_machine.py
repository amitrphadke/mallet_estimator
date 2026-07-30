import frappe


def execute():
    """The 'Estimate Machine' doctype became dead code once machine depreciation
    was folded into the workstation 'Consumables' operating component. It was
    never referenced by any other doctype and holds no data, so drop it (and its
    empty table) from sites that already installed it."""
    if frappe.db.exists("DocType", "Estimate Machine"):
        frappe.delete_doc("DocType", "Estimate Machine", force=True, ignore_missing=True)
        frappe.db.commit()
