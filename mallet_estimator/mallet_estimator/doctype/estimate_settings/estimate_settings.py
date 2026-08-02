import frappe
from frappe.model.document import Document

from mallet_estimator.estimator import (
    WORKSTATIONS, working_hours_per_month, working_days_per_month,
    productive_hours_per_day, workstation_rates, staff_rates, transport_rates,
)


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
    factory_area = (s.factory_length_ft or 0) * (s.factory_width_ft or 0)
    rows = workstation_rates(s)
    total_month = sum(r["rent_hr"] * whm for r in rows)
    roles = staff_rates(s)
    return {
        "rows": rows,
        "working_days_per_month": working_days_per_month(s),
        "productive_hours_per_day": productive_hours_per_day(s),
        "working_hours_per_month": whm,
        "monthly_rent": s.monthly_rent,
        "billable_area": billable_area,
        "factory_area": factory_area,
        "free_area": max(factory_area - billable_area, 0),
        "rent_per_sqft_month": (s.monthly_rent / billable_area) if billable_area else 0,
        "staff_rates": roles,
        "crew_rate": roles.get("carpenter", 0) + roles.get("helper", 0),
        "transport_rates": transport_rates(s),
        "rent_recovered_month": round(total_month),
    }
