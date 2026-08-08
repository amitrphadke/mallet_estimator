import json

import frappe

CSV_MODE, PDF_MODE = "CSV-Nest", "OCL PDF (standard)"


def execute():
    """Fill in the estimate columns that used to be computed on the fly.

    Two things moved from "derived at render time" to "stored":

    * ``Estimate.estimation_mode`` — the mode was only ever computed inside the
      form, so nothing outside could see it; the list view's indicator and its
      standard filter read the column now.
    * the SKUs grid rows — the grid absorbed the old intake table and files
      panel, so each row now displays the SKU's mode, its input files and its
      nested sheet count.

    Both are a VIEW of data that already exists, so this is a plain write per
    row: submitted estimates must keep their frozen baseline, and re-running
    validate here would reprice them."""
    if not frappe.db.has_column("Estimate", "estimation_mode"):
        return
    row_cols = [c for c in ("estimation_mode", "parts_csv", "estimate_pdf",
                            "views_pdf", "sheets")
                if frappe.db.has_column("Execution Estimate SKU", c)]
    for name in frappe.get_all("Estimate", pluck="name"):
        try:
            rows = frappe.get_all(
                "Execution Estimate SKU",
                filters={"parent": name, "parenttype": "Estimate", "parentfield": "skus"},
                fields=["name", "estimate_sku"])
            found = set()
            for r in rows:
                if not r.estimate_sku:
                    continue
                sku = frappe.db.get_value(
                    "Estimate SKU", r.estimate_sku,
                    ["estimation_mode", "parts_csv", "estimate_pdf", "views_pdf",
                     "import_drivers"], as_dict=True)
                if not sku:
                    continue
                found.add(sku.estimation_mode or PDF_MODE)
                if row_cols:
                    frappe.db.set_value("Execution Estimate SKU", r.name,
                                        _row_values(sku, row_cols), update_modified=False)
            # A mixed estimate predates the exclusivity rule — leave the mode
            # blank rather than claim one it does not have; the next save will
            # refuse the mix and force the user to split it.
            mode = found.pop() if len(found) == 1 else ""
            frappe.db.set_value("Estimate", name, "estimation_mode", mode, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"stamp estimate mode {name}")
    frappe.db.commit()


def _row_values(sku, cols):
    try:
        nest = (json.loads(sku.import_drivers or "{}") or {}).get("__nest__") or {}
    except Exception:
        nest = {}
    values = {
        "estimation_mode": sku.estimation_mode or PDF_MODE,
        "parts_csv": sku.parts_csv,
        "estimate_pdf": sku.estimate_pdf,
        "views_pdf": sku.views_pdf,
        "sheets": sum(float(v.get("sheets") or 0) for v in nest.values()),
    }
    return {c: values[c] for c in cols}
