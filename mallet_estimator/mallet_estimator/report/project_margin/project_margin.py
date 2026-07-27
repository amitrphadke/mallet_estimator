# Estimate-vs-Actual margin per Project.
#
#   Estimated  = from our Estimate SKUs (planned cost + client price + margin)
#   Actual     = ERPNext Project's native rollups (timesheet labour + consumed
#                material + purchase cost) and billed amount
#   Variance   = actual margin - estimated margin  (negative = margin eroded)
#
# This is the end-of-project reality check for custom jobs.

import frappe


def execute(filters=None):
    filters = filters or {}
    cond = " AND es.project = %(project)s" if filters.get("project") else ""

    estimates = frappe.db.sql(
        f"""
        SELECT es.project AS project,
               SUM(es.internal_cost) AS est_cost,
               SUM(es.client_total)  AS est_client
        FROM `tabEstimate SKU` es
        WHERE IFNULL(es.project, '') != '' {cond}
        GROUP BY es.project
        """,
        filters,
        as_dict=True,
    )

    data = []
    for e in estimates:
        p = frappe.db.get_value(
            "Project", e.project,
            ["customer", "total_costing_amount", "total_consumed_material_cost",
             "total_purchase_cost", "total_billed_amount"],
            as_dict=True,
        ) or {}
        act_labour = p.get("total_costing_amount") or 0
        act_material = (p.get("total_consumed_material_cost") or 0) + (p.get("total_purchase_cost") or 0)
        billed = p.get("total_billed_amount") or 0
        est_cost = e.est_cost or 0
        est_client = e.est_client or 0
        est_margin = est_client - est_cost
        act_cost = act_labour + act_material
        act_margin = billed - act_cost
        data.append({
            "project": e.project,
            "customer": p.get("customer"),
            "est_cost": est_cost,
            "est_client": est_client,
            "est_margin": est_margin,
            "act_labour": act_labour,
            "act_material": act_material,
            "billed": billed,
            "act_margin": act_margin,
            "variance": act_margin - est_margin,
        })

    return get_columns(), data


def get_columns():
    return [
        {"label": "Project", "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 150},
        {"label": "Customer", "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 140},
        {"label": "Est. Cost", "fieldname": "est_cost", "fieldtype": "Currency", "width": 110},
        {"label": "Est. To Client", "fieldname": "est_client", "fieldtype": "Currency", "width": 110},
        {"label": "Est. Margin", "fieldname": "est_margin", "fieldtype": "Currency", "width": 110},
        {"label": "Actual Labour", "fieldname": "act_labour", "fieldtype": "Currency", "width": 110},
        {"label": "Actual Material", "fieldname": "act_material", "fieldtype": "Currency", "width": 120},
        {"label": "Billed", "fieldname": "billed", "fieldtype": "Currency", "width": 110},
        {"label": "Actual Margin", "fieldname": "act_margin", "fieldtype": "Currency", "width": 110},
        {"label": "Margin Variance", "fieldname": "variance", "fieldtype": "Currency", "width": 120},
    ]
