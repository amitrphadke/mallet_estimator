# Mallet Materials — every material Item (under the Mallet Materials group tree)
# with its current cost and where that cost comes from, so prices are maintained
# in one place. "Priced?" flags anything the estimate can't cost yet.

import frappe

from mallet_estimator.inventory import ITEM_GROUPS, PARENT_GROUP, material_rate


def execute(filters=None):
    columns = [
        {"label": "Item Code", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 220},
        {"label": "Material", "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": "Group", "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
        {"label": "Thickness", "fieldname": "thickness", "fieldtype": "Float", "width": 80},
        {"label": "UOM", "fieldname": "uom", "fieldtype": "Data", "width": 70},
        {"label": "Rate", "fieldname": "rate", "fieldtype": "Currency", "width": 100},
        {"label": "Cost Source", "fieldname": "source", "fieldtype": "Data", "width": 110},
        {"label": "Priced?", "fieldname": "priced", "fieldtype": "Data", "width": 80},
        {"label": "Stock Qty", "fieldname": "stock_qty", "fieldtype": "Float", "width": 90},
    ]

    groups = list(ITEM_GROUPS) + [PARENT_GROUP]
    meta = frappe.get_meta("Item")
    th_field = "mallet_thickness_mm" if meta.has_field("mallet_thickness_mm") else None
    fields = ["name as item_code", "item_name", "item_group", "stock_uom as uom"]
    if th_field:
        fields.append(f"{th_field} as thickness")

    items = frappe.get_all(
        "Item", filters={"item_group": ["in", groups], "disabled": 0},
        fields=fields, order_by="item_group asc, item_name asc",
    )

    rows = []
    for it in items:
        rate, source = material_rate(it["item_code"])
        stock = frappe.db.get_value("Bin", {"item_code": it["item_code"]}, "sum(actual_qty)") or 0
        rows.append({
            "item_code": it["item_code"],
            "item_name": it.get("item_name"),
            "item_group": it.get("item_group"),
            "thickness": it.get("thickness") or 0,
            "uom": it.get("uom"),
            "rate": rate,
            "source": source,
            "priced": "Yes" if source != "unset" else "NO — set price",
            "stock_qty": stock,
        })
    return columns, rows
