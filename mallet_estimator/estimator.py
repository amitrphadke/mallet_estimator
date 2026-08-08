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
import math

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
    {"phase": "Miscellaneous - extra", "machine": None,         "in_factory": 1, "is_misc": 1},
]

# --- ERPNext Manufacturing masters -----------------------------------------
# Workstations with their factory footprint (sq ft) and machine capital for
# depreciation. Space-based rent is recovered in full across the billable
# footprints (inventory rack + aisles absorbed) over the working hours per
# month. Every operation is worked by a 2-person crew (1 carpenter + 1 helper),
# so labour per hour = carpenter_rate + helper_rate from Estimate Settings.
# On-Site has no footprint (off-site work, no rent).
#
# Each workstation's hourly cost is broken into the SAME native ERPNext
# "Operating Components Cost" the Workstation master uses: Rent + Wages +
# Machinery (depreciation) + Electricity + Consumables. `elec_hr`/`consumable_hr`
# are per-workstation defaults (₹/hr) you can override directly on the Workstation.
# COST DATA IS SENSITIVE (###): capital / electricity / consumables seed values
# are NOT stored in this repo — key them on each ERPNext Workstation (the live
# site already carries them). Zeros here mean "no code-side seed".
# `dims` = (length, width) ft — shown in the settings footprint table. `crew` =
# which staff work the station (their salary-derived rates become the Wage
# components); default is the 2-person carpenter+helper crew.
WORKSTATIONS = [
    {"name": "Panel Saw",        "dims": (26, 15), "area_sqft": 26 * 15, "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    {"name": "Edge Bander",      "dims": (16, 4),  "area_sqft": 16 * 4,  "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    {"name": "Drill Press",      "dims": (16, 3),  "area_sqft": 16 * 3,  "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    {"name": "Pasting Station",  "dims": (12, 8),  "area_sqft": 12 * 8,  "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    {"name": "Assembly Station", "dims": (14, 15), "area_sqft": 14 * 15, "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    {"name": "Project Room",     "dims": (14, 15), "area_sqft": 14 * 15, "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
    # D1 — the designer's desk: 6x4 ft, designer crew (capital ###, keyed on site).
    {"name": "Design Desk",      "dims": (6, 4),   "area_sqft": 6 * 4,   "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0, "crew": ("designer",)},
    {"name": "On-Site",          "dims": (0, 0),   "area_sqft": 0,       "capital": 0, "life_years": 10, "elec_hr": 0, "consumable_hr": 0},
]

# Canonical order of the operating-cost components on every Workstation (OPS3 —
# modular): Rent = pure space rent; Depreciation = machine capital straight-line
# (its own component, no longer folded into Consumables); one Wage component per
# crew member (salary-derived, L1). Zero-value components are skipped per station.
WS_COMPONENTS = ["Rent", "Depreciation", "Carpenter Wage", "Helper Wage", "Designer Wage",
                 "Electricity", "Consumables"]
DEFAULT_CREW = ("carpenter", "helper")
WAGE_COMPONENT = {"carpenter": "Carpenter Wage", "helper": "Helper Wage", "designer": "Designer Wage"}

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
    "Miscellaneous - extra": "Assembly Station",
}

# D1 — the design pipeline as first-class labor: 7 steps, all worked by the
# designer at the Design Desk (site measurement happens on-site but is still the
# designer's time; in_factory=0 marks it off-site). Std minutes live on the
# Operation master (mallet_min_per_unit) like every other operation — these are
# the seed defaults, tune them on the Operation.
DESIGN_STEP_TEMPLATE = [
    {"phase": "Site Measurement (ImageMeter + Laser)", "in_factory": 0},
    {"phase": "Live 3D Floor Plan",                    "in_factory": 1},
    {"phase": "Export 3D Plan to SKP",                 "in_factory": 1},
    {"phase": "SKU in SketchUp (OCL)",                 "in_factory": 1},
    {"phase": "7 Views PDF (Layout)",                  "in_factory": 1},
    {"phase": "Estimate PDF (OCL)",                    "in_factory": 1},
    {"phase": "Part List PDF (OCL)",                   "in_factory": 1},
]
DESIGN_STANDARDS = {
    "Site Measurement (ImageMeter + Laser)": {"qty_source": "manual", "min_per_unit": 120},
    "Live 3D Floor Plan":                    {"qty_source": "manual", "min_per_unit": 240},
    "Export 3D Plan to SKP":                 {"qty_source": "manual", "min_per_unit": 30},
    "SKU in SketchUp (OCL)":                 {"qty_source": "manual", "min_per_unit": 180},
    "7 Views PDF (Layout)":                  {"qty_source": "manual", "min_per_unit": 60},
    "Estimate PDF (OCL)":                    {"qty_source": "manual", "min_per_unit": 15},
    "Part List PDF (OCL)":                   {"qty_source": "manual", "min_per_unit": 15},
}
OPERATION_WORKSTATION.update({t["phase"]: "Design Desk" for t in DESIGN_STEP_TEMPLATE})
OPERATION_STANDARDS_DESIGN = DESIGN_STANDARDS  # alias for controllers

# C1 — inward material trips (₹/trip, defaults; live values come from settings):
#   tempo  = ply + internal laminate + joinery hardware (one big tempo)
#   ext    = external laminate (separate careful trip)
#   client = client hardware (hinges/rails/handles/lifts)
#   outward = finished goods to site
# Trip rates are SENSITIVE (###) — live values are keyed in Estimate Settings.
TRANSPORT_DEFAULTS = {"tempo": 0, "ext_lam": 0, "client_hw": 0, "outward": 0}


def transport_rates(settings):
    return {
        "tempo": _get(settings, "trip_rate_tempo", TRANSPORT_DEFAULTS["tempo"]) or TRANSPORT_DEFAULTS["tempo"],
        "ext_lam": _get(settings, "trip_rate_ext_lam", TRANSPORT_DEFAULTS["ext_lam"]) or TRANSPORT_DEFAULTS["ext_lam"],
        "client_hw": _get(settings, "trip_rate_client_hw", TRANSPORT_DEFAULTS["client_hw"]) or TRANSPORT_DEFAULTS["client_hw"],
        "outward": _get(settings, "trip_rate_outward", TRANSPORT_DEFAULTS["outward"]) or TRANSPORT_DEFAULTS["outward"],
    }

# What each process step is supposed to take care of — seeded into the step's
# Remarks (editable per SKU; Assembly/Packing are meant to be refined by the user).
STEP_REMARKS = {
    "Sheet Lamination": "Apply glue on BOTH sides of the ply and paste laminate according to the material code.",
    "Sheet Tape Removal": "Remove the holding tape from the laminated sheets.",
    "Sheet Cutting": "Cut sheets per the cutting diagram (CD).",
    "Edge Banding": "Band exposed edges per part list (internal/external tape per code).",
    "Minifix Boring": "Bore minifix housings (qty = minifix count).",
    "Drilling": "Pilot holes + screws — qty is the SCREW count.",
    "Assembly": "Specify what gets assembled here: typically carcass and/or drawer boxes.",
    "Install Hardware": "Hinges, drawer rails, handles and shelf supports only (qty = their total).",
    "Disassembly": "Dismantle what must travel flat.",
    "Packing": "Dismantle where required before packing — a wardrobe carcass is dismantled, drawer boxes are not.",
    "Loading": "Load packed parts + hardware boxes.",
    "Transport": "Factory → site trip.",
    "Unloading": "Unload and stage at site.",
    "Assembly (on-site)": "Re-assemble carcass, hang doors/drawers.",
    "Installation": "Fix to wall/level, final alignment and handover.",
}

ROUTING_NAME = "Mallet Standard Build"


def workstation_rates(settings):
    """Compute per-workstation hourly rates broken into the native ERPNext
    operating components: Rent + Wages + Machinery + Electricity + Consumables.

    Rent recovers the FULL monthly rent across billable footprints over the
    working hours/month. Wages is the 2-person crew (carpenter + helper).
    Machinery is straight-line depreciation. Returns each WORKSTATIONS entry plus:
      - rent_hr, wages_hr, machine_hr, elec_hr, consumable_hr
      - components: [(label, ₹/hr), ...] in WS_COMPONENTS order
      - net_hr: the Net Hour Rate (sum of components)
      - legacy aliases labour_hr/dep_hr/total_hr so older callers still work.
    """
    whm = working_hours_per_month(settings)
    monthly_rent = _num(settings.monthly_rent)
    roles = staff_rates(settings)
    billable_area = sum(w["area_sqft"] for w in WORKSTATIONS if w["area_sqft"] > 0)
    rent_per_sqft = (monthly_rent / billable_area) if billable_area else 0.0
    out = []
    for w in WORKSTATIONS:
        rent_hr = (w["area_sqft"] * rent_per_sqft / whm) if whm else 0.0
        # Depreciation is its OWN component now (OPS3) — Rent stays pure space rent
        # and Consumables stays true consumables.
        dep_hr = (w["capital"] / (w["life_years"] * whm * 12)) if (w["life_years"] and whm) else 0.0
        elec_hr = _num(w.get("elec_hr"))
        consumable_hr = _num(w.get("consumable_hr"))
        crew = w.get("crew", DEFAULT_CREW)
        wage_vals = {WAGE_COMPONENT[role]: roles.get(role, 0.0) for role in crew}
        wages_hr = sum(wage_vals.values())
        comp_vals = {
            "Rent": rent_hr, "Depreciation": dep_hr,
            "Electricity": elec_hr, "Consumables": consumable_hr, **wage_vals,
        }
        # Canonical order; a component a station doesn't have (zero) is skipped.
        components = [(c, comp_vals[c]) for c in WS_COMPONENTS if comp_vals.get(c)]
        net_hr = sum(comp_vals.get(c, 0.0) for c in WS_COMPONENTS)
        out.append({
            **w,
            "rent_hr": rent_hr, "wages_hr": wages_hr, "machine_hr": dep_hr,
            "elec_hr": elec_hr, "consumable_hr": consumable_hr,
            "components": components, "net_hr": net_hr, "crew": crew,
            # legacy aliases
            "labour_hr": wages_hr, "dep_hr": dep_hr, "total_hr": net_hr,
        })
    return out


def live_workstation_rates(settings):
    """Return {workstation_name: rate_dict} using the ERPNext Workstation master
    as the source of truth: each row of the native `workstation_costs` child table
    (Rent/Wages/Machinery/Electricity/Consumables) plus the computed `hour_rate`
    (Net Hour Rate). Falls back to the computed rate for any workstation that has
    no operating-cost rows yet, so nothing prices at zero. Import-safe: only used
    inside the ERPNext controller.
    """
    import frappe  # local import keeps this module unit-testable without frappe

    computed = {w["name"]: w for w in workstation_rates(settings)}
    rates = {}
    for name in frappe.get_all("Workstation", pluck="name"):
        # Read fresh (not cached) so an edit to the workstation's operating costs
        # is picked up immediately.
        doc = frappe.get_doc("Workstation", name)
        rows = getattr(doc, "workstation_costs", None) or []
        if not rows:
            if name in computed:
                rates[name] = computed[name]
            continue
        comp = {r.operating_component: _num(r.operating_cost) for r in rows}
        rent_hr = comp.get("Rent", 0)
        # Wages = every per-role Wage component (Carpenter/Helper/Designer) plus
        # the legacy folded "Wages"; Depreciation plus the legacy "Machinery".
        wages_hr = comp.get("Wages", 0) + sum(v for c, v in comp.items() if c.endswith("Wage"))
        machine_hr = comp.get("Machinery", 0) + comp.get("Depreciation", 0)
        elec_hr = comp.get("Electricity", 0)
        consumable_hr = comp.get("Consumables", 0)
        # Net Hour Rate = the sum of ALL component rows (never trust a possibly
        # stale stored hour_rate). This includes any component we don't name
        # explicitly, so nothing is dropped.
        named = rent_hr + wages_hr + machine_hr + elec_hr + consumable_hr
        total_rows = sum(comp.values())
        # fold any extra/unrecognised components into consumables for the split
        consumable_hr += (total_rows - named)
        net_hr = total_rows
        rates[name] = {
            "rent_hr": rent_hr, "wages_hr": wages_hr, "machine_hr": machine_hr,
            "elec_hr": elec_hr, "consumable_hr": consumable_hr, "net_hr": net_hr,
            "labour_hr": wages_hr, "dep_hr": machine_hr, "total_hr": net_hr,
        }
    # include any computed workstation that ERPNext doesn't have yet
    for name, r in computed.items():
        rates.setdefault(name, r)
    return rates


def mm_to_ftin(mm):
    """1524 → 5′-0″ ; 598 → 1′-11½″. Millimetres stay the single source of
    truth (factory language); feet-inches are PRESENTATION ONLY, rounded to the
    nearest half inch (client language)."""
    mm = _num(mm)
    if not mm:
        return ""
    total_in = round(mm / 25.4 * 2) / 2.0
    ft = int(total_in // 12)
    rem = total_in - ft * 12
    whole = int(rem)
    frac = "½" if (rem - whole) >= 0.49 else ""
    inch = f"{whole}{frac}″"
    return f"{ft}′-{inch}" if ft else inch


def dims_ftin(w, d, h):
    """W x D x H in feet-inches, skipping missing dims."""
    parts = [mm_to_ftin(v) for v in (w, d, h)]
    return " x ".join(p for p in parts if p)


def _num(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _get(settings, field, default=0):
    return _num(getattr(settings, field, default) or default)


def working_days_per_month(settings):
    """L1 — days the factory actually runs: working days (excl. weekly offs) minus
    the 2 paid holidays/month minus the 10 national holidays/yr prorated."""
    return max(
        _get(settings, "working_days_per_month", 26)
        - _get(settings, "paid_holidays_per_month")
        - _get(settings, "national_holidays_per_year") / 12.0,
        0.0,
    )


def productive_hours_per_day(settings):
    """L1 — the 8-hr shift includes a 1-hr paid lunch: productive hrs = shift − lunch."""
    return max(_get(settings, "working_hours_per_day", 8) - _get(settings, "lunch_hours_per_day"), 0.0)


def working_hours_per_month(settings):
    """Productive (billable) hours the factory runs per month — the base over
    which rent, depreciation and salaries are recovered."""
    return working_days_per_month(settings) * productive_hours_per_day(settings)


def staff_rates(settings):
    """L1 — effective ₹/hr per role, derived from MONTHLY SALARY + the holiday
    calendar + the Diwali bonus (annual cost = salary x (12 + bonus months),
    spread over the productive hours). Falls back to the legacy keyed hourly
    rates when no salary is set, so old data/tests still price."""
    whm = working_hours_per_month(settings)
    bonus = _get(settings, "bonus_months")

    def rate(salary_field, legacy_field):
        salary = _get(settings, salary_field)
        if salary and whm:
            return salary * (12 + bonus) / 12.0 / whm
        return _get(settings, legacy_field)

    return {
        "carpenter": rate("carpenter_salary", "carpenter_rate"),
        "helper": rate("helper_salary", "helper_rate"),
        "designer": rate("designer_salary", "design_rate"),
    }


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


def customer_initials(customer_name):
    parts = str(customer_name or "").split()
    cf = parts[0][0] if parts else ""
    cl = parts[-1][0] if len(parts) > 1 else ""
    return (cf + cl).upper()


def sku_code(customer_name, room, article_name):
    return "_".join(x for x in [customer_initials(customer_name), initials(room), abbr(article_name)] if x)


# Default operation standards: which material driver fills each operation's Qty
# and the crew minutes per unit. Editable per SKU on the labor table.
OPERATION_STANDARDS = {
    "Sheet Lamination":       {"qty_source": "laminate_sheets", "min_per_unit": 15},
    "Sheet Tape Removal":     {"qty_source": "sheets",          "min_per_unit": 3},
    "Sheet Cutting":          {"qty_source": "sheets",          "min_per_unit": 20},
    "Edge Banding":           {"qty_source": "edge_parts",      "min_per_unit": 3},
    "Minifix Boring":         {"qty_source": "minifix",         "min_per_unit": 1},
    "Drilling":               {"qty_source": "hinges",          "min_per_unit": 3},
    "Grooving":               {"qty_source": "manual",          "min_per_unit": 5},
    "Assembly":               {"qty_source": "panels",          "min_per_unit": 4},
    "Install Hardware":       {"qty_source": "hardware_total",  "min_per_unit": 2},
    "Disassembly":            {"qty_source": "manual",          "min_per_unit": 15},
    "Packing":                {"qty_source": "sheets",          "min_per_unit": 8},
    "Loading":                {"qty_source": "manual",          "min_per_unit": 30},
    "Transport":              {"qty_source": "manual",          "min_per_unit": 30},
    "Unloading":              {"qty_source": "manual",          "min_per_unit": 30},
    "Assembly (on-site)":     {"qty_source": "manual",          "min_per_unit": 45},
    "Installation":           {"qty_source": "manual",          "min_per_unit": 60},
    "Miscellaneous - extra":  {"qty_source": "manual",          "min_per_unit": 0},
}


MISC_OPERATION = "Miscellaneous - extra"


def op_phase(row):
    """Canonical Operation name for a labor row (the misc/custom row is generic).
    Prefers the native `operation` link; falls back to the legacy `phase` text."""
    if getattr(row, "is_misc", 0):
        return MISC_OPERATION
    return getattr(row, "operation", None) or getattr(row, "phase", None)


def calc_sku(sku, settings, ws_rates=None):
    """Compute all cost figures for one Estimate SKU (native workstation model).

    Each phase's crew minutes = qty x carp_min (carp_min = crew minutes per unit;
    the 2-person crew is priced inside the workstation hour-rate). Phase cost =
    crew-hours x that phase's workstation rate, split into labour / machine (dep)
    / rent for the breakdown, and written back to each row's op_cost.

    `ws_rates` is {workstation_name: {rent_hr, dep_hr, labour_hr, total_hr}} — the
    controller passes the live ERPNext Workstation master rates; if omitted we fall
    back to the computed rates so the function stays unit-testable.
    """
    if ws_rates is None:
        ws_rates = {w["name"]: w for w in workstation_rates(settings)}
    default_ws = "Assembly Station"
    # Margins live on the SELLABLE THING: a SKU with custom margins prices
    # itself; everything else inherits the house policy (Estimate Settings).
    custom = bool(_num(getattr(sku, "use_custom_margins", 0) or 0))
    if custom:
        markup = {
            "material": _num(sku.get("margin_material")),
            "labor": _num(sku.get("margin_labor")),
            "overhead": _num(sku.get("margin_overhead")),
            "design": _num(sku.get("margin_design")),
        }
    else:
        markup = {
            "material": _num(settings.markup_material),
            "labor": _num(settings.markup_labor),
            "overhead": _num(settings.markup_overhead),
            "design": _num(settings.markup_design),
        }
    markup_display = dict(markup, __custom__=custom)

    def cost_rows(rows, fallback_ws, skip_misc):
        """Price a table of step rows at their workstation rates. Returns totals
        split by component bucket; writes each row's op_cost back."""
        t = {"min": 0.0, "wages": 0.0, "dep": 0.0, "rent": 0.0, "other": 0.0}
        for s in rows or []:
            if skip_misc and getattr(s, "is_misc", 0) and not sku.include_misc:
                s.carp_total = 0
                s.helper_total = 0
                s.op_cost = 0
                continue
            crew_min = _num(s.qty) * _num(s.carp_min)  # carp_min = workstation minutes/unit
            s.carp_total = crew_min
            s.helper_total = crew_min
            t["min"] += crew_min
            ws_name = getattr(s, "workstation", None) or OPERATION_WORKSTATION.get(op_phase(s), fallback_ws)
            r = ws_rates.get(ws_name) or ws_rates.get(fallback_ws) or {}
            hrs = crew_min / 60.0
            wages_hr = r.get("wages_hr", r.get("labour_hr", 0))
            machine_hr = r.get("machine_hr", r.get("dep_hr", 0))
            rent_hr = r.get("rent_hr", 0)
            elec_hr = r.get("elec_hr", 0)
            consumable_hr = r.get("consumable_hr", 0)
            # Net Hour Rate: prefer the live ERPNext Workstation total when supplied.
            net_hr = r.get("net_hr", wages_hr + machine_hr + rent_hr + elec_hr + consumable_hr)
            t["wages"] += hrs * wages_hr
            t["dep"] += hrs * machine_hr
            t["rent"] += hrs * rent_hr
            t["other"] += hrs * (elec_hr + consumable_hr)
            s.op_cost = hrs * net_hr
        return t

    lab = cost_rows(sku.labor, default_ws, skip_misc=True)
    crew_min_total = lab["min"]
    labor_cost = lab["wages"]
    machine_cost = lab["dep"]
    rent_cost = lab["rent"]
    other_cost = lab["other"]

    carp_min_total = crew_min_total
    helper_min_total = crew_min_total
    carpenter_cost = labor_cost  # labour is the 2-person crew (folded into wages)
    helper_cost = 0.0
    # Client-supplied lines keep their full pricing on screen (the estimate has
    # to read as a whole picture) but are not money WE spend, so they never
    # enter cost. Filtering here is what keeps the two views honest.
    material_cost = sum(_num(m.line_cost) for m in (sku.materials or [])
                        if not getattr(m, "customer_supplied", 0))
    # J1 — fevicol/abrotape derived consumables: material, but its own bucket.
    joinery_cost = sum(_num(j.amount) for j in (getattr(sku, "joinery_items", None) or []))

    # D1 — design as real workstation labor (Design Desk rates, full component
    # split). Falls back to the legacy hours x rate + flat model when the design
    # table is empty.
    design_rows = getattr(sku, "design_labor", None) or []
    if design_rows:
        des = cost_rows(design_rows, "Design Desk", skip_misc=False)
        design_cost = des["wages"] + des["dep"] + des["rent"] + des["other"]
        design_min_total = des["min"]
        design_wages = des["wages"]
        design_overhead = design_cost - des["wages"]
    else:
        design_cost = _num(sku.design_hours) * _num(settings.design_rate) + _num(sku.design_flat)
        design_min_total = 0.0
        design_wages = design_cost
        design_overhead = 0.0

    # C1 — inward/outward transport at the settings trip rates. Standalone view of
    # THIS SKU; consolidated (shared-trip) billing happens on the Estimate.
    trates = transport_rates(settings)
    transport_cost = (
        _num(getattr(sku, "trips_tempo", 0)) * trates["tempo"]
        + _num(getattr(sku, "trips_ext_lam", 0)) * trates["ext_lam"]
        + _num(getattr(sku, "trips_client_hw", 0)) * trates["client_hw"]
        + _num(getattr(sku, "trips_outward", 0)) * trates["outward"]
    )

    overhead_cost = machine_cost + rent_cost + other_cost
    rent_hours = crew_min_total / 60.0
    internal_cost = material_cost + joinery_cost + labor_cost + overhead_cost + design_cost + transport_cost

    # Joinery consumables are material to the client (same markup bucket).
    client_material = (material_cost + joinery_cost) * (1 + markup["material"] / 100.0)
    client_labor = labor_cost * (1 + markup["labor"] / 100.0)
    client_overhead = overhead_cost * (1 + markup["overhead"] / 100.0)
    client_design = design_cost * (1 + markup["design"] / 100.0)
    # Client view folds labour + overhead + design into one "design & execution" line.
    client_design_exec = client_labor + client_overhead + client_design
    # Transport is NOT in the SKU's client total — trips are shared across the
    # project, so the Estimate bills its consolidated trips (at cost, no markup).
    # It IS inside internal_cost so the SKU's margin view stays honest.
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
        "joinery_cost": joinery_cost,
        "design_cost": design_cost,
        "design_min_total": design_min_total,
        "design_wages": design_wages,
        "design_overhead": design_overhead,
        "transport_cost": transport_cost,
        "internal_cost": internal_cost,
        "client_material": client_material,
        "client_labor": client_labor,
        "client_overhead": client_overhead,
        "client_design": client_design,
        "client_design_exec": client_design_exec,
        "client_total": client_total,
        "markup_pct": markup_display,
    }


# ---------------------------------------------------------------------------
# Repair work (R1) — a different UNIT of estimation.
#
# New work prices an ARTICLE from its parts; repair prices an ACTIVITY on
# something that already exists in the client's home. There are no parts to
# nest, the material is small and often not stocked, and the labour IS the
# cost. So repair has its own engine rather than a special case bolted into
# calc_sku — the two never share a code path, only the wage rates.
#
# The three rules the shop actually works by:
#   * a row's minutes are crew x per-unit minutes x QTY (six chairs at 30 min
#     each is 180 min, not 30);
#   * a row that cannot be priced until someone looks at it ("To Inspect")
#     carries its provisional minutes but is kept OUT of the firm total —
#     these are the rows that eat the margin when they hide at qty 0;
#   * a visit has a floor price. Going out at all costs a day; the day rate
#     stops binding once a full day of carpenter AND helper has been worked.
# ---------------------------------------------------------------------------

PRODUCTIVE_MIN_PER_DAY = 360.0
ON_SITE_WORKSTATION = "On-Site"
TO_INSPECT = "To Inspect"


def repair_row_minutes(row):
    """(carpenter minutes, helper minutes) for one activity row, quantity
    included. `row` is anything with .get() or attributes — a Frappe child row
    or a plain dict, so the engine stays testable without a database."""
    def f(name):
        if isinstance(row, dict):
            return _num(row.get(name))
        return _num(getattr(row, name, 0))

    qty = f("qty") or 1
    return (qty * (f("carpenters") or 0) * f("carp_min"),
            qty * (f("helpers") or 0) * f("helper_min"))


def _row_status(row):
    if isinstance(row, dict):
        return (row.get("status") or "").strip()
    return (getattr(row, "status", "") or "").strip()


def calc_repair(activities, settings, markup_pct=None, visit_charge=None, visits=None):
    """Cost a repair SKU's activity table.

    `markup_pct` / `visit_charge` default to the Estimate Settings policy
    (`markup_repair`, `repair_visit_charge`); both are 0 in code on purpose —
    the real values live only in the site DB.

    `visits` overrides the derived day count for a job that is spread over
    more trips than its minutes imply (two half-days a week apart is two
    visits, not one).
    """
    rates = staff_rates(settings)
    markup = _num(markup_pct if markup_pct is not None else _get(settings, "markup_repair"))
    day_rate = _num(visit_charge if visit_charge is not None else _get(settings, "repair_visit_charge"))

    carp_min = helper_min = 0.0
    hold_carp = hold_helper = 0.0
    to_inspect = 0
    for row in activities or []:
        c, h = repair_row_minutes(row)
        if _row_status(row) == TO_INSPECT:
            to_inspect += 1
            hold_carp += c
            hold_helper += h
            continue
        carp_min += c
        helper_min += h

    # A day on site is the same 360 productive minutes as a day on the floor.
    est_days = max(carp_min, helper_min) / PRODUCTIVE_MIN_PER_DAY
    derived_visits = int(math.ceil(est_days)) if est_days > 0 else 0
    if visits is not None and _num(visits) > 0:
        billed_visits = int(_num(visits))
    else:
        billed_visits = derived_visits

    labor_cost = carp_min / 60.0 * rates["carpenter"] + helper_min / 60.0 * rates["helper"]
    client_labor = labor_cost * (1 + markup / 100.0)
    visit_amount = billed_visits * day_rate
    # The day rate is a FLOOR, not an addition: a full day's work already pays
    # for the day, so the two are never charged on top of each other.
    client_repair = max(client_labor, visit_amount)
    return {
        "carp_min": carp_min,
        "helper_min": helper_min,
        "est_days": est_days,
        "visits": billed_visits,
        "derived_visits": derived_visits,
        "labor_cost": labor_cost,
        "client_labor": client_labor,
        "day_rate": day_rate,
        "visit_amount": visit_amount,
        "visit_topup": max(0.0, visit_amount - client_labor),
        "client_repair": client_repair,
        "markup_pct": markup,
        "to_inspect": to_inspect,
        "to_inspect_carp_min": hold_carp,
        "to_inspect_helper_min": hold_helper,
    }
