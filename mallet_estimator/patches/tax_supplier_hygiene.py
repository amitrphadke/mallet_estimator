import frappe

from mallet_estimator import install


def execute():
    """T2 hygiene:
      • dedupe Item Supplier rows (a docname-casing mismatch appended one row per
        import — keep the first row per supplier, case-insensitive);
      • seed the native GST masters (accounts, 'GST 18%' Item Tax Template applied
        on the material Item Groups, output sales template)."""
    for code in frappe.get_all("Item", filters={"is_purchase_item": 1}, pluck="name"):
        try:
            item = frappe.get_doc("Item", code)
            rows = item.get("supplier_items") or []
            if len(rows) <= 1:
                continue
            seen, keep = set(), []
            for r in rows:
                key = (r.supplier or "").lower()
                if key in seen:
                    continue
                seen.add(key)
                keep.append(r)
            if len(keep) != len(rows):
                item.set("supplier_items", keep)
                item.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"supplier dedupe {code}")

    try:
        install.ensure_gst_masters()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "tax_supplier_hygiene gst masters")

    frappe.db.commit()
