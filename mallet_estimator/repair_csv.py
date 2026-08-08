# ---------------------------------------------------------------------------
# Repair Activity CSV — reads the shop's own repair estimation sheet.
#
# Repair work is estimated on a walk-through, in a spreadsheet, and that is not
# going to change: the sheet IS the estimating tool. So the ERP ingests it as
# it is rather than asking anyone to retype a job into a grid, exactly as the
# OpenCutList CSV is ingested for new work.
#
# Pure parsing — no frappe, no database — so the whole shape of the sheet is
# covered by the fast unit-test lane.
# ---------------------------------------------------------------------------

import csv
import io

# The sheet's own column names. Kept verbatim (spaces, dots, capitals) so a
# person comparing the two can see they match.
HEADER_KEYS = ("Activity", "Room Name", "Carpenters (Mins)")
TOTALS_MARKER = "TOTALS"

# Everything under the TOTALS row in the real sheet is a leftover NEW-WORK
# material block (plywood, laminate, tempo trips, removal charges). Reading it
# as repair material would silently inflate the job, so parsing stops dead at
# TOTALS and says what it left behind.
TRAILING_NOTE = (
    "Content below the TOTALS row was ignored — that block is new-work "
    "material, not repair activity. Put new work on its own SKU."
)

# A row nobody can price yet. The sheet spells this out in prose and by
# leaving the quantity at 0, which makes the row cost nothing AND warn
# nobody — the single most expensive habit in the spreadsheet.
INSPECT_HINTS = ("inspect", "tbd", "to be decided", "check if")


def _num(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean(v):
    return str(v or "").strip()


def find_header(rows):
    """Index of the header row — the sheet carries a title block above it, and
    the block's height changes from job to job, so the header is FOUND, not
    assumed to be at a fixed offset."""
    for i, row in enumerate(rows):
        cells = {_clean(c) for c in row}
        if all(k in cells for k in HEADER_KEYS):
            return i
    return -1


def looks_unpriceable(activity_row):
    """True when the row is really 'someone has to look at this first'."""
    if _clean(activity_row.get("status")) == "To Inspect":
        return True
    haystack = " ".join([
        _clean(activity_row.get("description")),
        _clean(activity_row.get("material_note")),
    ]).lower()
    if any(h in haystack for h in INSPECT_HINTS):
        return True
    # No quantity at all means the row contributes nothing to the total; in the
    # sheet that is how an un-scoped job is parked.
    return not _num(activity_row.get("qty"))


def parse_repair_csv(text):
    """(activities, warnings) from the repair estimation sheet.

    Every activity is a dict ready for the Estimate Repair Activity table.
    Minutes are per-unit as typed; the QUANTITY multiplication happens in the
    costing engine (estimator.repair_row_minutes), so the sheet's own
    'Row Total' columns are never trusted — they are hand-maintained and drift.
    """
    rows = list(csv.reader(io.StringIO(text)))
    h = find_header(rows)
    if h < 0:
        return [], ["No header row found — is this the repair estimation sheet?"]
    header = [_clean(c) for c in rows[h]]
    index = {name: i for i, name in enumerate(header) if name}

    def cell(row, name):
        i = index.get(name)
        return _clean(row[i]) if i is not None and i < len(row) else ""

    activities, warnings = [], []
    stopped_at = None
    for n, row in enumerate(rows[h + 1:], start=h + 2):
        if any(TOTALS_MARKER == _clean(c).upper() for c in row):
            stopped_at = n
            break
        activity = cell(row, "Activity")
        if not activity:
            # a blank spacer line inside the block is fine; a run of them is
            # the end of the table in sheets that carry no TOTALS row
            if not any(_clean(c) for c in row):
                continue
            continue
        a = {
            "room": cell(row, "Room Name"),
            "target": cell(row, "SKU"),
            "activity": activity,
            "description": cell(row, "Description"),
            "material_note": cell(row, "Material Description"),
            "material_item": cell(row, "Item"),
            "material_uom": cell(row, "UOM"),
            "qty": _num(cell(row, "Quantity")),
            "carpenters": int(_num(cell(row, "Carpenters (No.)"))),
            "carp_min": _num(cell(row, "Carpenters (Mins)")),
            "helpers": int(_num(cell(row, "Helpers (No.)"))),
            "helper_min": _num(cell(row, "Helpers (Mins)")),
            "workstation": cell(row, "Workstation") or "On-Site",
            "remarks": cell(row, "Remarks"),
            "source_row": n,
            # what the SHEET says the row totals — kept only to reconcile
            "_sheet_carp_total": _num(cell(row, "Row Total (Carp. Mins)")),
            "_sheet_helper_total": _num(cell(row, "Row Total (Helper Mins)")),
        }
        a["status"] = "To Inspect" if looks_unpriceable(a) else "Quoted"
        if a["status"] == "Quoted" and not a["qty"]:
            a["qty"] = 1
        activities.append(a)

    if not activities:
        warnings.append("The sheet's activity block is empty.")
    if stopped_at is not None and any(
        any(_clean(c) for c in row) for row in rows[stopped_at:]
    ):
        warnings.append(TRAILING_NOTE)
    held = [a for a in activities if a["status"] == "To Inspect"]
    if held:
        warnings.append(
            "{0} activity(ies) need a site inspection before they can be priced: {1}. "
            "They are quoted as scope only and excluded from the firm total.".format(
                len(held), ", ".join(a["activity"] for a in held)))
    warnings.extend(reconcile(activities))
    return activities, warnings


def reconcile(activities):
    """Two ways a hand-maintained sheet lies about its own effort.

    (a) A Row Total that no longer matches crew x minutes x qty — a formula
        that stopped being dragged down.
    (b) Minutes typed against a crew of ZERO. The arithmetic is then perfectly
        consistent (0 people x 30 min = 0) and the row silently costs nothing,
        which is why it survives a read-through: the real sheet has a six-lift
        row carrying 30 helper minutes with no helper on it.
    """
    out = []
    for a in activities:
        if a["status"] == "To Inspect":
            continue
        for role, crew, per in (("carpenter", a["carpenters"], a["carp_min"]),
                                ("helper", a["helpers"], a["helper_min"])):
            if per and not crew:
                out.append(
                    "Row {0} ({1}): {2:g} {3} minutes are typed but no {3} is on the "
                    "row, so it costs nothing. Set the head-count or clear the "
                    "minutes.".format(a["source_row"], a["activity"], per, role))
        carp = a["qty"] * a["carpenters"] * a["carp_min"]
        helper = a["qty"] * a["helpers"] * a["helper_min"]
        for label, computed, typed in (("carpenter", carp, a["_sheet_carp_total"]),
                                       ("helper", helper, a["_sheet_helper_total"])):
            if typed and abs(typed - computed) > 0.5:
                out.append(
                    "Row {0} ({1}): sheet says {2:g} {3} minutes, the inputs give "
                    "{4:g} — the ERP uses {4:g}.".format(
                        a["source_row"], a["activity"], typed, label, computed))
            elif computed and not typed:
                out.append(
                    "Row {0} ({1}): the sheet totals no {2} minutes but the inputs "
                    "give {3:g} — the ERP uses {3:g}.".format(
                        a["source_row"], a["activity"], label, computed))
    return out
