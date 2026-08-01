# V2 — Estimate vs Execution material variance, so invoicing has no surprises.
#
# For each Estimate SKU with an execution design, one row per material line:
#   Estimated (generic, ceiling)  vs  Chosen actual (client-selected item)
#   Variance = actual − estimated.  Rows where actual > estimated are flagged ⚠.
#
# Filter by Project (and optionally show only the flagged, over-estimate lines).

import frappe


def execute(filters=None):
    filters = filters or {}
    conds = []
    if filters.get("project"):
        conds.append("es.project = %(project)s")
    if filters.get("sku"):
        conds.append("es.name = %(sku)s")
    where = (" AND " + " AND ".join(conds)) if conds else ""

    rows = frappe.db.sql(
        f"""
        SELECT es.name AS sku, es.article_name, es.room,
               em.est_material, em.est_qty, em.est_rate, em.est_amount,
               em.chosen_item, em.actual_qty, em.actual_rate, em.actual_amount,
               em.variance
        FROM `tabExecution Material` em
        JOIN `tabEstimate SKU` es ON es.name = em.parent
        WHERE 1=1 {where}
        ORDER BY es.room, es.article_name, em.est_material
        """,
        filters,
        as_dict=True,
    )

    data = []
    for r in rows:
        over = (r.variance or 0) > 0.005
        if filters.get("only_over") and not over:
            continue
        r["flag"] = "⚠ over" if over else ("✓ under" if (r.variance or 0) < -0.005 else "=")
        data.append(r)

    return _columns(), data


def _columns():
    return [
        {"label": "SKU", "fieldname": "sku", "fieldtype": "Link", "options": "Estimate SKU", "width": 150},
        {"label": "Article", "fieldname": "article_name", "fieldtype": "Data", "width": 130},
        {"label": "Room", "fieldname": "room", "fieldtype": "Data", "width": 110},
        {"label": "Estimated (generic)", "fieldname": "est_material", "fieldtype": "Data", "width": 150},
        {"label": "Est Qty", "fieldname": "est_qty", "fieldtype": "Float", "width": 70},
        {"label": "Est Rate", "fieldname": "est_rate", "fieldtype": "Currency", "width": 90},
        {"label": "Est Amount", "fieldname": "est_amount", "fieldtype": "Currency", "width": 100},
        {"label": "Chosen Item", "fieldname": "chosen_item", "fieldtype": "Link", "options": "Item", "width": 160},
        {"label": "Act Qty", "fieldname": "actual_qty", "fieldtype": "Float", "width": 70},
        {"label": "Act Rate", "fieldname": "actual_rate", "fieldtype": "Currency", "width": 90},
        {"label": "Act Amount", "fieldname": "actual_amount", "fieldtype": "Currency", "width": 100},
        {"label": "Variance", "fieldname": "variance", "fieldtype": "Currency", "width": 100},
        {"label": "Flag", "fieldname": "flag", "fieldtype": "Data", "width": 80},
    ]
