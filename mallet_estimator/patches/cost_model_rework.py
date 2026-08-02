import frappe

from mallet_estimator import inventory, install


# .numbers card MRPs INCLUDE tax — store ex-tax so landed (= ex-tax x 1.18)
# reproduces the MRP exactly.
FEVICOL_MRP_INCL = ###   # per packet
ABROTAPE_MRP_INCL = ###   # per 20 m roll
GST = 18.0


def execute():
    """T1/L1/OPS3/D1/J1/C1 — the cost-model rework migrate:
      • Item GST% field (+ any new mallet Item fields);
      • Client Hardware + Joinery Hardware groups; re-home HWD_ items;
      • JH_Fevicol + JH_Abrotape items (Abrotape stocked per meter, 20 m rolls);
      • rebuild every workstation's operating components to the modular set
        (Rent / Depreciation / per-role Wages / Electricity / Consumables);
      • seed the Design Desk workstation + the 7 design operations.
    Idempotent; each step guarded."""
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields(inventory.CUSTOM_FIELDS, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "cost_model_rework custom fields")

    # 1. Groups + re-home hardware into Client/Joinery.
    try:
        parent = inventory.PARENT_GROUP if frappe.db.exists("Item Group", inventory.PARENT_GROUP) else \
            frappe.db.get_value("Item Group", {"is_group": 1}, "name")
        res = {"item_groups": 0, "errors": []}
        for g in (inventory.CLIENT_HW_GROUP, inventory.JOINERY_GROUP):
            if parent and not frappe.db.exists("Item Group", g):
                inventory._ensure_group(g, parent, is_group=0, result=res)
        if frappe.db.exists("Item Group", "Hardware"):
            for code in frappe.get_all("Item", filters={"item_group": "Hardware"}, pluck="name"):
                try:
                    target = inventory.hardware_group(
                        frappe.db.get_value("Item", code, "mallet_oc_code") or code)
                    if frappe.db.exists("Item Group", target):
                        frappe.db.set_value("Item", code, "item_group", target, update_modified=False)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), f"cost_model_rework rehome {code}")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "cost_model_rework groups")

    # 2. Joinery consumable items (J1). Abrotape is bespoke: stocked per Meter,
    #    bought in 20 m rolls — ensure_material_item's joinery spec is Nos-based.
    try:
        inventory.ensure_material_item("JH_Fevicol", kind="joinery")
        inventory.set_assumed_rate("JH_Fevicol", round(FEVICOL_MRP_INCL / (1 + GST / 100), 2))
        _ensure_abrotape()
        inventory.set_assumed_rate("JH_Abrotape", round(ABROTAPE_MRP_INCL / (1 + GST / 100) / 20.0, 4))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "cost_model_rework joinery items")

    # 3. Rebuild workstation operating components to the modular model (OPS3/L1)
    #    and seed Design Desk + design operations (D1).
    try:
        _rebuild_ws_components()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "cost_model_rework ws components")
    try:
        install.ensure_manufacturing_masters()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "cost_model_rework masters")

    frappe.db.commit()


def _ensure_abrotape():
    code = "JH_Abrotape"
    if frappe.db.exists("Item", code):
        return
    item = frappe.new_doc("Item")
    item.item_code = code
    item.item_name = "Abrotape (laminate holding tape)"
    item.item_group = inventory.JOINERY_GROUP if frappe.db.exists("Item Group", inventory.JOINERY_GROUP) \
        else inventory._fallback_group()
    item.stock_uom = "Meter" if frappe.db.exists("UOM", "Meter") else "Nos"
    item.is_stock_item = 1
    item.is_purchase_item = 1
    if item.meta.has_field("purchase_uom") and frappe.db.exists("UOM", "Roll"):
        item.purchase_uom = "Roll"
    item.append("uoms", {"uom": item.stock_uom, "conversion_factor": 1})
    if frappe.db.exists("UOM", "Roll"):
        item.append("uoms", {"uom": "Roll", "conversion_factor": 20})  # 20 m per roll
    item.description = "Abrotape — holds laminate while Fevicol dries. Stocked per metre; bought in 20 m rolls."
    if item.meta.has_field("mallet_oc_code"):
        item.mallet_oc_code = code
    item.insert(ignore_permissions=True)
    inventory.attach_scope_suppliers(code, "joinery")


def _rebuild_ws_components(overwrite=True):
    """Replace each seeded workstation's known operating rows (legacy folded set
    AND current modular set) with the freshly computed modular components. Any
    custom component the user added with another name is preserved."""
    from mallet_estimator.estimator import workstation_rates
    settings = frappe.get_single("Estimate Settings")
    rates = {w["name"]: w for w in workstation_rates(settings)}
    install._ensure_operating_components()
    known = set(install.WS_COMPONENTS) | set(install.LEGACY_WS_COMPONENTS)
    for name, r in rates.items():
        if not frappe.db.exists("Workstation", name):
            continue
        try:
            ws = frappe.get_doc("Workstation", name)
            if not ws.meta.has_field("workstation_costs"):
                return
            keep = [row for row in (ws.get("workstation_costs") or [])
                    if row.operating_component not in known]
            ws.set("workstation_costs", keep)
            for label, val in r["components"]:
                if val:
                    ws.append("workstation_costs", {
                        "operating_component": label, "operating_cost": round(val, 2),
                    })
            ws.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"cost_model_rework ws {name}")
