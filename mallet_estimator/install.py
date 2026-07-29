import json
import os

import frappe

from mallet_estimator.estimator import (
    DEFAULT_MACHINES, STEP_TEMPLATE, WORKSTATIONS, OPERATION_WORKSTATION,
    ROUTING_NAME, WS_COMPONENTS, workstation_rates,
)
from mallet_estimator import inventory
from mallet_estimator.inventory import ensure_inventory_masters, ensure_warehouses

PRINT_FORMAT_NAME = "Mallet Client Estimate"
WORKSPACE_NAME = "Mallet Estimator"


DEFAULT_ROOMS = [
    "Master Bedroom", "Kids Bedroom", "Guest Bedroom", "Living Room", "Dining Room",
    "Kitchen", "Study", "Pooja Room", "Foyer", "Balcony", "Bathroom", "Utility", "Other",
]


def after_install():
    seed_settings()
    _safe(ensure_rooms)
    _safe(ensure_inventory_masters)
    _safe(ensure_warehouses)
    _safe(ensure_manufacturing_masters)
    _safe(ensure_print_format)
    _safe(ensure_workspace)


def after_migrate():
    # Keep only the LIGHT, fast masters in sync on every migrate. The heavier
    # ones — inventory Item custom fields (a schema rebuild on the big Item table)
    # and the warehouse tree — are created by after_install and the "Create /
    # refresh manufacturing masters" button, NOT here, so a deploy's site-migration
    # step never stalls on them.
    _safe(ensure_rooms)
    _safe(ensure_manufacturing_masters)
    _safe(ensure_print_format)
    _safe(ensure_workspace)


def ensure_rooms():
    for r in DEFAULT_ROOMS:
        if not frappe.db.exists("Estimate Room", r):
            doc = frappe.new_doc("Estimate Room")
            doc.room_name = r
            doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _safe(fn):
    try:
        fn()
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"mallet_estimator {fn.__name__}")


def _op_name(phase):
    # Doc names avoid "/" which is awkward in Frappe routing/urls.
    return phase.replace(" / ", " - ").replace("/", "-")


def _ensure_operating_components():
    """Make sure the native 'Workstation Operating Component' masters
    (Rent/Wages/Electricity/Consumables) exist. ERPNext ships these, but create
    any that are missing so seeding a workstation's costs never fails on a bad
    link. (autoname = field:component_name.)"""
    dt = "Workstation Operating Component"
    if not frappe.db.exists("DocType", dt):
        return
    for c in WS_COMPONENTS:
        if not frappe.db.exists(dt, c):
            try:
                doc = frappe.new_doc(dt)
                doc.component_name = c
                doc.insert(ignore_permissions=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"mallet_estimator operating component: {c}")


def _set_workstation_costs(ws, rate):
    """Populate a Workstation's native `workstation_costs` child table from the
    computed component rates. Only used when the table is empty (never clobbers
    hand-tuned rates). Returns True if rows were set."""
    if not ws.meta.has_field("workstation_costs"):
        return False
    if getattr(ws, "workstation_costs", None):
        return False  # already configured (e.g. a hand-set Panel Saw) — leave it
    for label, val in rate["components"]:
        if not val:
            continue
        ws.append("workstation_costs", {
            "operating_component": label,
            "operating_cost": round(val, 2),
        })
    return bool(getattr(ws, "workstation_costs", None))


def ensure_manufacturing_masters():
    """Create the 7 Workstations (native operating-component hour rates), 17
    Operations and the standard Routing as ERPNext manufacturing masters.
    Idempotent: existing records keep their hand-tuned rates; a workstation that
    has no operating-cost rows yet gets them backfilled. Each record is created
    independently so one failure doesn't abort the rest."""
    settings = frappe.get_single("Estimate Settings")
    rates = {w["name"]: w for w in workstation_rates(settings)}
    result = {"workstations": 0, "workstations_costed": 0, "operations": 0, "routing": 0, "errors": []}

    def fail(label, exc):
        result["errors"].append(f"{label}: {exc}")
        frappe.log_error(frappe.get_traceback(), f"mallet_estimator masters: {label}")

    _ensure_operating_components()

    for w in WORKSTATIONS:
        try:
            r = rates[w["name"]]
            if frappe.db.exists("Workstation", w["name"]):
                # Backfill operating costs only if this workstation has none yet,
                # so a manually configured station (your Panel Saw) is preserved.
                ws = frappe.get_doc("Workstation", w["name"])
                if _set_workstation_costs(ws, r):
                    ws.save(ignore_permissions=True)
                    result["workstations_costed"] += 1
                continue
            ws = frappe.new_doc("Workstation")
            ws.workstation_name = w["name"]
            _set_workstation_costs(ws, r)
            ws.insert(ignore_permissions=True)
            result["workstations"] += 1
        except Exception as exc:
            fail(f"Workstation {w['name']}", exc)

    for t in STEP_TEMPLATE:
        op_name = _op_name(t["phase"])
        try:
            if frappe.db.exists("Operation", op_name):
                continue
            op = frappe.new_doc("Operation")
            op.name = op_name
            op.workstation = OPERATION_WORKSTATION.get(t["phase"])
            op.insert(ignore_permissions=True, set_name=op_name)
            result["operations"] += 1
        except Exception as exc:
            fail(f"Operation {op_name}", exc)

    try:
        if not frappe.db.exists("Routing", ROUTING_NAME):
            routing = frappe.new_doc("Routing")
            routing.routing_name = ROUTING_NAME
            for i, t in enumerate(STEP_TEMPLATE, start=1):
                routing.append("operations", {
                    "sequence_id": i,
                    "operation": _op_name(t["phase"]),
                    "workstation": OPERATION_WORKSTATION.get(t["phase"]),
                    "time_in_mins": 0,
                })
            routing.insert(ignore_permissions=True)
            result["routing"] = 1
    except Exception as exc:
        fail("Routing", exc)

    frappe.db.commit()
    return result


@frappe.whitelist()
def setup():
    """Manually (re)create all app masters — callable from the Estimate Settings
    button. Returns a summary so the UI can report what was created and any error."""
    if not frappe.has_permission("Estimate Settings", "write"):
        frappe.throw("Not permitted")
    seed_settings()
    _safe(ensure_rooms)
    inv, wh = {}, {}
    try:
        inv = ensure_inventory_masters()
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "mallet_estimator ensure_inventory_masters")
        inv = {"errors": [f"inventory: {exc}"]}
    try:
        wh = ensure_warehouses()
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "mallet_estimator ensure_warehouses")
        wh = {"errors": [f"warehouses: {exc}"]}
    result = ensure_manufacturing_masters()
    result["inventory"] = inv
    result["warehouses"] = wh
    for fn in (ensure_print_format, ensure_workspace):
        try:
            fn()
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), f"mallet_estimator {fn.__name__}")
            result.setdefault("errors", []).append(f"{fn.__name__}: {exc}")
    result["workspace_exists"] = bool(frappe.db.exists("Workspace", WORKSPACE_NAME))
    return result


WAREHOUSE_LEAVES = [
    "Board & Sheet Store", "Hardware Store", "Cut Parts - Table 1", "Cut Parts - Table 2",
    "Assembly Area", "Project Room", "Packed / Dispatch", "Customer Provided",
]
ITEM_CUSTOM_FIELDS = ["mallet_oc_code", "mallet_thickness_mm", "mallet_sheet_length_mm", "mallet_sheet_width_mm"]


def ensure_demo_company():
    """Create a demo Company if the site has none — used by CI (so warehouses can
    be created) and for a quick local demo. No-op when a company already exists."""
    if frappe.db.get_value("Company", {}, "name"):
        return
    frappe.get_doc({
        "doctype": "Company", "company_name": "Mallet Demo", "abbr": "MD",
        "default_currency": "INR", "country": "India",
    }).insert(ignore_permissions=True)
    frappe.db.commit()


@frappe.whitelist()
def verify_setup():
    """Config health-check — assert every master this app needs exists and is
    shaped right. Returns {checks:[{name, ok, detail}], all_ok, failed}. Drives
    the 'Verify setup' button AND is asserted by the automated tests, so the same
    contract is checked by hand and in CI."""
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def missing(dt, names, by_field=None):
        out = []
        for n in names:
            exists = frappe.db.exists(dt, {by_field: n}) if by_field else frappe.db.exists(dt, n)
            if not exists:
                out.append(n)
        return out

    groups = [inventory.PARENT_GROUP, inventory.CLIENT_SKU_GROUP] + inventory.ITEM_GROUPS
    m = missing("Item Group", groups)
    chk("Item Groups", not m, ("missing: " + ", ".join(m)) if m else f"{len(groups)} present")

    uoms = ["Sheet", "Meter", "Roll", "Square Meter"]
    m = missing("UOM", uoms)
    chk("UOMs", not m, ("missing: " + ", ".join(m)) if m else "Sheet, Meter, Roll, Square Meter ✓")

    meta = frappe.get_meta("Item")
    m = [f for f in ITEM_CUSTOM_FIELDS if not meta.has_field(f)]
    chk("Item custom fields", not m, ("missing: " + ", ".join(m)) if m else "thickness + sheet L/W + OC code ✓")

    m = missing("Warehouse", WAREHOUSE_LEAVES, by_field="warehouse_name")
    chk("Warehouses", not m, ("missing: " + ", ".join(m)) if m else f"{len(WAREHOUSE_LEAVES)} present")

    m = missing("Workstation", [w["name"] for w in WORKSTATIONS])
    chk("Workstations", not m, ("missing: " + ", ".join(m)) if m else f"{len(WORKSTATIONS)} present")

    n_ops = frappe.db.count("Operation")
    chk("Operations", n_ops >= len(STEP_TEMPLATE), f"{n_ops} operations")
    chk("Routing", frappe.db.exists("Routing", ROUTING_NAME), ROUTING_NAME)
    chk("Print format", frappe.db.exists("Print Format", PRINT_FORMAT_NAME), PRINT_FORMAT_NAME)
    chk("Workspace", frappe.db.exists("Workspace", WORKSPACE_NAME), WORKSPACE_NAME)

    n_unpriced = frappe.db.count("Item", {
        "item_group": ["in", inventory.ITEM_GROUPS], "disabled": 0, "valuation_rate": 0,
        "last_purchase_rate": 0, "standard_rate": 0,
    })
    chk("Material prices", True, f"{n_unpriced} material(s) still unpriced" if n_unpriced else "all materials priced")

    failed = [c["name"] for c in checks if not c["ok"]]
    return {"checks": checks, "all_ok": not failed, "failed": failed}


def ensure_workspace():
    """Create/refresh the desk Workspace programmatically (disk sync of
    workspaces is unreliable across benches)."""
    if frappe.db.exists("Workspace", WORKSPACE_NAME):
        frappe.delete_doc("Workspace", WORKSPACE_NAME, ignore_permissions=True, force=True)

    ws = frappe.new_doc("Workspace")
    ws.name = WORKSPACE_NAME
    ws.label = WORKSPACE_NAME
    ws.title = WORKSPACE_NAME
    ws.public = 1
    ws.module = "Mallet Estimator"
    ws.icon = "project"
    ws.content = json.dumps([{"id": "mest_card", "type": "card", "data": {"card_name": "Estimating", "col": 4}}])
    for typ, label, dt in [
        ("Card Break", "Estimating", None),
        ("Link", "Estimate SKU", "Estimate SKU"),
        ("Link", "Estimate", "Estimate"),
        ("Link", "Estimate Settings", "Estimate Settings"),
    ]:
        row = {"type": typ, "label": label}
        if dt:
            row.update({"link_type": "DocType", "link_to": dt})
        ws.append("links", row)
    ws.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()


def seed_settings():
    # Persist the single so default rates/rent are stored (workstation costing
    # reads them). Machine masters live as ERPNext Workstations, not here.
    settings = frappe.get_single("Estimate Settings")
    settings.flags.ignore_permissions = True
    settings.save()
    frappe.db.commit()


def ensure_print_format():
    path = os.path.join(
        frappe.get_app_path("mallet_estimator"),
        "templates", "print", "mallet_client_estimate.html",
    )
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
        pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
    else:
        pf = frappe.new_doc("Print Format")
        pf.name = PRINT_FORMAT_NAME

    pf.doc_type = "Estimate"
    pf.module = "Mallet Estimator"
    pf.print_format_type = "Jinja"
    pf.custom_format = 1
    pf.standard = "No"
    pf.disabled = 0
    pf.html = html
    pf.flags.ignore_permissions = True
    pf.save()
    frappe.db.commit()
