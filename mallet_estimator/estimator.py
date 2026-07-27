# ---------------------------------------------------------------------------
# Cost calculation engine for the SKU execution estimator.
#
# Cost buildup for one SKU (one article / Item):
#   Material  (line items)
#   + Labor   (carpenter + helper minutes per process step x rates)
#   + Machine (depreciation-based machine-hour rate x machine-op minutes)
#   + Rent    (rent-per-hour x in-factory hours the SKU occupies)
#   + Design  (design hours x design rate + flat)
#   = Internal execution cost
#   -> Client price = internal cost with per-category markup, where the client
#      view folds machine + rent overhead into the "design & execution" line.
#
# This mirrors the standalone React app's src/model.js so both stay in sync.
# ---------------------------------------------------------------------------

# 16 fixed process steps (1-16) plus one editable miscellaneous / extra step
# at line 17. `machine` links a step to a machine key in Estimate Settings.
# `in_factory` decides whether the step's hours attract factory rent.
STEP_TEMPLATE = [
    {"phase": "Sheet Lamination",     "machine": None,          "in_factory": 1},
    {"phase": "Sheet Tape Removal",   "machine": None,          "in_factory": 1},
    {"phase": "Sheet Cutting",        "machine": "panel_saw",   "in_factory": 1},
    {"phase": "Edge Banding",         "machine": "edge_bander", "in_factory": 1},
    {"phase": "Minifix Boring",       "machine": "drill_press", "in_factory": 1},
    {"phase": "Drilling",             "machine": "drill_press", "in_factory": 1},
    {"phase": "Grooving",             "machine": "panel_saw",   "in_factory": 1},
    {"phase": "Assembly",             "machine": "assembly",    "in_factory": 1},
    {"phase": "Install Hardware",     "machine": "assembly",    "in_factory": 1},
    {"phase": "Disassembly",          "machine": "assembly",    "in_factory": 1},
    {"phase": "Packing",              "machine": "packing",     "in_factory": 1},
    {"phase": "Loading",              "machine": None,          "in_factory": 0},
    {"phase": "Transport",            "machine": None,          "in_factory": 0},
    {"phase": "Unloading",            "machine": None,          "in_factory": 0},
    {"phase": "Assembly (on-site)",   "machine": None,          "in_factory": 0},
    {"phase": "Installation",         "machine": None,          "in_factory": 0},
    {"phase": "Miscellaneous / extra", "machine": None,         "in_factory": 1, "is_misc": 1},
]

DEFAULT_MACHINES = [
    {"machine_key": "panel_saw",   "machine_name": "Panel saw + dust collector", "capital_cost": ###, "life_years": 10},
    {"machine_key": "edge_bander", "machine_name": "Edge bander",                "capital_cost": ###, "life_years": 10},
    {"machine_key": "drill_press", "machine_name": "Drill press",                "capital_cost": ###,  "life_years": 10},
    {"machine_key": "assembly",    "machine_name": "Assembly station",           "capital_cost": ###,  "life_years": 10},
    {"machine_key": "packing",     "machine_name": "Packing area",               "capital_cost": 15000,  "life_years": 10},
]

# --- ERPNext Manufacturing masters -----------------------------------------
# Workstations with their factory footprint (sq ft) and machine capital for
# depreciation. Space-based rent is recovered in full across the billable
# footprints (inventory rack + aisles absorbed) over the working hours per
# month. Every operation is worked by a 2-person crew (1 carpenter + 1 helper),
# so labour per hour = carpenter_rate + helper_rate from Estimate Settings.
# On-Site has no footprint (off-site work, no rent).
WORKSTATIONS = [
    {"name": "Panel Saw",        "area_sqft": 26 * 15, "capital": ###, "life_years": 10},
    {"name": "Edge Bander",      "area_sqft": 16 * 4,  "capital": ###, "life_years": 10},
    {"name": "Drill Press",      "area_sqft": 16 * 3,  "capital": ###,  "life_years": 10},
    {"name": "Pasting Station",  "area_sqft": 12 * 8,  "capital": 0,      "life_years": 10},
    {"name": "Assembly Station", "area_sqft": 14 * 15, "capital": ###,  "life_years": 10},
    {"name": "Project Room",     "area_sqft": 14 * 15, "capital": 0,      "life_years": 10},
    {"name": "On-Site",          "area_sqft": 0,       "capital": 0,      "life_years": 10},
]

# Which workstation each of the 17 operations runs on (matches STEP_TEMPLATE).
OPERATION_WORKSTATION = {
    "Sheet Lamination": "Pasting Station",
    "Sheet Tape Removal": "Pasting Station",
    "Sheet Cutting": "Panel Saw",
    "Edge Banding": "Edge Bander",
    "Minifix Boring": "Drill Press",
    "Drilling": "Drill Press",
    "Grooving": "Panel Saw",
    "Assembly": "Assembly Station",
    "Install Hardware": "Assembly Station",
    "Disassembly": "Assembly Station",
    "Packing": "Pasting Station",
    "Loading": "On-Site",
    "Transport": "On-Site",
    "Unloading": "On-Site",
    "Assembly (on-site)": "On-Site",
    "Installation": "On-Site",
    "Miscellaneous / extra": "Assembly Station",
}

ROUTING_NAME = "Mallet Standard Build"


def workstation_rates(settings):
    """Compute per-workstation hourly rates from footprint + machine + wage.

    Rent recovers the FULL monthly rent across billable footprints over the
    working hours/month. Returns each WORKSTATIONS entry plus rent_hr, dep_hr,
    labour_hr and total_hr.
    """
    whm = working_hours_per_month(settings)
    monthly_rent = _num(settings.monthly_rent)
    # Every operation is a 2-person crew (1 carpenter + 1 helper).
    labour_hr = _num(settings.carpenter_rate) + _num(settings.helper_rate)
    billable_area = sum(w["area_sqft"] for w in WORKSTATIONS if w["area_sqft"] > 0)
    rent_per_sqft = (monthly_rent / billable_area) if billable_area else 0.0
    out = []
    for w in WORKSTATIONS:
        rent_hr = (w["area_sqft"] * rent_per_sqft / whm) if whm else 0.0
        dep_hr = (w["capital"] / (w["life_years"] * whm * 12)) if (w["life_years"] and whm) else 0.0
        out.append({**w, "rent_hr": rent_hr, "dep_hr": dep_hr, "labour_hr": labour_hr,
                    "total_hr": rent_hr + dep_hr + labour_hr})
    return out


def _num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def working_hours_per_month(settings):
    return _num(settings.working_days_per_month) * _num(settings.working_hours_per_day)


def machine_hour_rate(capital_cost, life_years, settings):
    hours_per_year = working_hours_per_month(settings) * 12
    if hours_per_year <= 0 or _num(life_years) <= 0:
        return 0.0
    return _num(capital_cost) / (_num(life_years) * hours_per_year)


def rent_per_hour(settings):
    h = working_hours_per_month(settings)
    return _num(settings.monthly_rent) / h if h > 0 else 0.0


def initials(text):
    return "".join(w[0] for w in str(text or "").split()).upper()


def abbr(text):
    s = str(text or "").strip()
    if not s:
        return ""
    words = s.split()
    return "".join(w[0] for w in words).upper() if len(words) > 1 else s[:3].upper()


def sku_code(customer_name, room, article_name):
    parts = str(customer_name or "").split()
    cf = parts[0][0] if parts else ""
    cl = parts[-1][0] if len(parts) > 1 else ""
    client = (cf + cl).upper()
    return "_".join(x for x in [client, initials(room), abbr(article_name)] if x)


def calc_sku(sku, settings):
    """Compute all cost figures for one Estimate SKU document.

    `sku` exposes: materials (list with .line_cost), labor (list of step rows),
    include_misc, design_hours, design_flat.
    `settings` exposes rates, rent, machines. Returns a dict of figures and also
    sets carp_total / helper_total on each labor row.
    """
    machines = {m.machine_key: m for m in (settings.machines or [])}
    markup = {
        "material": _num(settings.markup_material),
        "labor": _num(settings.markup_labor),
        "overhead": _num(settings.markup_overhead),
        "design": _num(settings.markup_design),
    }

    carp_min_total = 0.0
    helper_min_total = 0.0
    machine_cost = 0.0
    rent_hours = 0.0

    for s in sku.labor or []:
        if getattr(s, "is_misc", 0) and not sku.include_misc:
            s.carp_total = 0
            s.helper_total = 0
            continue
        c_min = _num(s.qty) * _num(s.carp_no) * _num(s.carp_min)
        h_min = _num(s.qty) * _num(s.helper_no) * _num(s.helper_min)
        s.carp_total = c_min
        s.helper_total = h_min
        carp_min_total += c_min
        helper_min_total += h_min
        if s.machine_key and s.machine_key in machines:
            m = machines[s.machine_key]
            machine_cost += machine_hour_rate(m.capital_cost, m.life_years, settings) * (c_min / 60.0)
        if s.in_factory:
            rent_hours += c_min / 60.0

    carpenter_cost = (carp_min_total / 60.0) * _num(settings.carpenter_rate)
    helper_cost = (helper_min_total / 60.0) * _num(settings.helper_rate)
    labor_cost = carpenter_cost + helper_cost
    rent_cost = rent_per_hour(settings) * rent_hours
    material_cost = sum(_num(m.line_cost) for m in (sku.materials or []))
    design_cost = _num(sku.design_hours) * _num(settings.design_rate) + _num(sku.design_flat)
    overhead_cost = machine_cost + rent_cost
    internal_cost = material_cost + labor_cost + overhead_cost + design_cost

    client_material = material_cost * (1 + markup["material"] / 100.0)
    client_labor = labor_cost * (1 + markup["labor"] / 100.0)
    client_overhead = overhead_cost * (1 + markup["overhead"] / 100.0)
    client_design = design_cost * (1 + markup["design"] / 100.0)
    # Client view folds labour + overhead + design into one "design & execution" line.
    client_design_exec = client_labor + client_overhead + client_design
    client_total = client_material + client_design_exec

    return {
        "carp_min_total": carp_min_total,
        "helper_min_total": helper_min_total,
        "carpenter_cost": carpenter_cost,
        "helper_cost": helper_cost,
        "labor_cost": labor_cost,
        "machine_cost": machine_cost,
        "rent_cost": rent_cost,
        "rent_hours": rent_hours,
        "overhead_cost": overhead_cost,
        "material_cost": material_cost,
        "design_cost": design_cost,
        "internal_cost": internal_cost,
        "client_material": client_material,
        "client_design_exec": client_design_exec,
        "client_total": client_total,
    }
