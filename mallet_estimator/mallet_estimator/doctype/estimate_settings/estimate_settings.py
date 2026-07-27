import frappe
from frappe.model.document import Document

from mallet_estimator.estimator import WORKSTATIONS, working_hours_per_month, workstation_rates


class EstimateSettings(Document):
    pass


@frappe.whitelist()
def cost_calculator():
    """Reference breakdown of each workstation's hourly charge from the current
    settings — direct labour (carpenter+helper crew), indirect machine
    depreciation, and prorated factory space cost. Use it to decide/verify the
    Workstation master rates. Nothing here is stored; the live rates live on the
    ERPNext Workstation records."""
    s = frappe.get_single("Estimate Settings")
    whm = working_hours_per_month(s)
    billable_area = sum(w["area_sqft"] for w in WORKSTATIONS if w["area_sqft"] > 0)
    rows = workstation_rates(s)
    total_month = sum(r["rent_hr"] * whm for r in rows)
    return {
        "rows": rows,
        "working_hours_per_month": whm,
        "working_hours_per_year": whm * 12,
        "monthly_rent": s.monthly_rent,
        "billable_area": billable_area,
        "rent_per_sqft_month": (s.monthly_rent / billable_area) if billable_area else 0,
        "crew_rate": (s.carpenter_rate or 0) + (s.helper_rate or 0),
        "rent_recovered_month": round(total_month),
    }
