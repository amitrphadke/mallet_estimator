import frappe


def execute():
    """Backfill Estimate.estimation_mode on the estimates that already exist.

    The mode was always DERIVED (from the SKUs an estimate carries) and only
    ever computed on the fly, so nothing outside the form could see it. Now it
    is stored — the list view's indicator and standard filter read the column —
    which means every pre-existing estimate needs the value written once. A
    plain db_set per doc: submitted estimates must keep their frozen baseline,
    so we never re-run validate here."""
    if not frappe.db.has_column("Estimate", "estimation_mode"):
        return
    csv_mode, pdf_mode = "CSV-Nest", "OCL PDF (standard)"
    for name in frappe.get_all("Estimate", pluck="name"):
        try:
            modes = frappe.get_all(
                "Execution Estimate SKU",
                filters={"parent": name, "parenttype": "Estimate", "parentfield": "skus"},
                pluck="estimate_sku")
            if not modes:
                mode = ""
            else:
                found = {
                    frappe.db.get_value("Estimate SKU", s, "estimation_mode") or pdf_mode
                    for s in modes if s
                }
                # A mixed estimate predates the exclusivity rule — leave it
                # blank rather than claim a mode it does not have; the next
                # save will refuse the mix and force the user to split it.
                mode = found.pop() if len(found) == 1 else ""
            frappe.db.set_value("Estimate", name, "estimation_mode", mode, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"stamp estimate mode {name}")
    frappe.db.commit()
