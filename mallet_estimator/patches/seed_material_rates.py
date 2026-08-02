import frappe

from mallet_estimator import inventory

# The shop's material catalogue (codes only — the CODES are not sensitive; the
# RATES are, so they are never stored in this repo). Rates live on the site's
# Estimation (Assumed) price list, keyed in the UI / seeded directly on the site.
MATERIAL_CATALOGUE = [
    ("EB_PVC_EX_b", "edge"),
    ("EB_PVC_IN_a", "edge"),
    ("HWD_AH_SC_0", "hardware"),
    ("HWD_DR_SC_550mm", "hardware"),
    ("HWD_Handle_150mm", "hardware"),
    ("HWD_HandleDrawer_150mm", "hardware"),
    ("HWD_Lock_20mm", "hardware"),
    ("HWD_MiniFix", "hardware"),
    ("HWD_Screw_8x32", "hardware"),
    ("HWD_ShelfSupport", "hardware"),
    ("SG_LAM_V0_12mm_a_a", "laminate"),
    ("SG_LAM_V0_16mm_a_a", "laminate"),
    ("SG_LAM_V1_16mm_a_b", "laminate"),
    ("SG_LAM_V1_16mm_b_a", "laminate"),
    ("SG_PLY_V0_a_a_12mm", "sheet"),
    ("SG_PLY_V0_a_a_16mm", "sheet"),
    ("SG_PLY_V1_a_b_16mm", "sheet"),
]


def execute():
    """Ensure the catalogue items exist in their correct groups. Assumed rates
    (###, sensitive) are NOT seeded from code — they are keyed on the site's
    Estimation (Assumed) price list (already present on mcft-stg)."""
    inventory.ensure_pricing_masters()
    for code, kind in MATERIAL_CATALOGUE:
        try:
            inventory.ensure_material_item(code, kind=kind)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"seed_material_rates {code}")
    frappe.db.commit()
