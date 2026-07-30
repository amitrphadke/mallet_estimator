import frappe

from mallet_estimator import inventory


def execute():
    """Older material/article Items were created before is_stock_item defaulted to
    1, so they can't be received against a Purchase Order or moved by a Stock
    Entry. Backfill: every material Item in the Mallet Materials groups becomes a
    purchasable stock item, and every finished Client SKU article becomes a
    sellable stock item (produced -> stocked -> delivered)."""
    # raw materials (plywood, laminate, edge banding, hardware, …)
    mats = frappe.get_all(
        "Item", filters={"item_group": ["in", inventory.ITEM_GROUPS]}, pluck="name"
    )
    for code in mats:
        frappe.db.set_value("Item", code, {"is_stock_item": 1, "is_purchase_item": 1}, update_modified=False)

    # finished client articles
    if frappe.db.exists("Item Group", inventory.CLIENT_SKU_GROUP):
        arts = frappe.get_all("Item", filters={"item_group": inventory.CLIENT_SKU_GROUP}, pluck="name")
        for code in arts:
            frappe.db.set_value("Item", code, {"is_stock_item": 1, "is_sales_item": 1}, update_modified=False)

    frappe.db.commit()
