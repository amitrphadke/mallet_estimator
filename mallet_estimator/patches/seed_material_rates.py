import frappe

from mallet_estimator import inventory

# Snapshot of the user's "Mallet Materials.numbers" rate card (2026-08-01) — the
# shop's OWN codes and assumed estimation rates. Hardware rows are the real stock
# item designations (HWD_AH_SC_0), whose GROUP (HWD_Hinge) appears in the part
# list; the estimate must stock/cost the designation, not the group.
#   (item_code, kind, rate ₹ per stock UOM)
MATERIAL_RATES = [
    ("EB_PVC_EX_b", "edge", 30),
    ("EB_PVC_IN_a", "edge", 20),
    ("HWD_AH_SC_0", "hardware", 300),
    ("HWD_DR_SC_550mm", "hardware", ###),
    ("HWD_Handle_150mm", "hardware", 300),
    ("HWD_HandleDrawer_150mm", "hardware", 300),
    ("HWD_Lock_20mm", "hardware", 200),
    ("HWD_MiniFix", "hardware", 50),
    ("HWD_Screw_8x32", "hardware", 10),
    ("HWD_ShelfSupport", "hardware", 20),
    ("SG_LAM_V0_12mm_a_a", "laminate", 700),
    ("SG_LAM_V0_16mm_a_a", "laminate", 700),
    ("SG_LAM_V1_16mm_a_b", "laminate", 700),
    ("SG_LAM_V1_16mm_b_a", "laminate", ###),
    ("SG_PLY_V0_a_a_12mm", "sheet", ###),
    ("SG_PLY_V0_a_a_16mm", "sheet", ###),
    ("SG_PLY_V1_a_b_16mm", "sheet", ###),
]


def execute():
    """Seed the shop's material rate card: ensure each material exists as a stock
    Item in its correct group and carries its assumed estimation rate on the
    Estimation (Assumed) price list. Idempotent — an existing assumed rate is
    updated to the card's value (the card is the source of truth for planning)."""
    inventory.ensure_pricing_masters()
    for code, kind, rate in MATERIAL_RATES:
        try:
            inventory.ensure_material_item(code, kind=kind)
            inventory.set_assumed_rate(code, rate)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"seed_material_rates {code}")
    frappe.db.commit()
