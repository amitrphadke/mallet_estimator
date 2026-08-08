import frappe

from mallet_estimator import estimator


def execute():
    """Re-derive every auto-named SKU code under the current grammar, and take
    the ERPNext Item with it.

    Codes generated before this evening carry two defects that are visible in
    the list view. An underscore name read as ONE word, so `MB_WAR_CSV` gave
    `MB_` and the article never reached the code — three different articles all
    ended up as `YS_MB_MB_`. And a collision was silent: the second SKU with a
    matching code attached itself to the FIRST one's Item, so several SKUs
    shared one Item and its price became whichever saved last.

    This does NOT re-save the documents. A full save would re-run the whole
    validate pipeline — re-pricing lines, rebuilding labor — and a patch that
    quietly moves the numbers on an estimate someone has already sent is worse
    than the wrong code. Only the code and the Item change, via the same
    sync_item the form uses.

    Two rows are deliberately left alone: a submitted (frozen) SKU, because
    approve means freeze, and a SKU whose auto_name is off, because someone
    chose that code by hand."""
    if not frappe.db.has_column("Estimate SKU", "sku_code"):
        return
    meta = frappe.get_meta("Estimate SKU")
    fields = ["name", "sku_code", "article_name", "room", "customer", "item"]
    for optional in ("auto_name", "rates_frozen", "multi_room"):
        if meta.has_field(optional):
            fields.append(optional)

    # Oldest first, so that where several SKUs share one Item the lowest-
    # numbered claims it. The rest fall through to their own new Item, because
    # sync_item finds their old item name no longer exists. Which SKU inherits
    # the shared Item's history is genuinely ambiguous in the data — this at
    # least decides it the same way every time.
    rows = frappe.get_all("Estimate SKU", fields=fields, order_by="name asc")

    taken, renamed, skipped = set(), 0, 0
    for r in rows:
        if _left_alone(r) and r.sku_code:
            taken.add(r.sku_code)

    for r in rows:
        if _left_alone(r):
            skipped += 1
            continue
        if not r.article_name:
            continue
        customer_name = ""
        if r.customer:
            customer_name = frappe.db.get_value(
                "Customer", r.customer, "customer_name") or r.customer
        room_token = "All Rooms" if r.get("multi_room") else r.room
        code = estimator.sku_code(customer_name, room_token, r.article_name)
        if not code:
            continue
        code = _unique(code, taken)
        taken.add(code)
        if code == r.sku_code:
            continue
        try:
            doc = frappe.get_doc("Estimate SKU", r.name)
            doc.sku_code = code
            doc.sync_item()          # renames the Item, or makes its own
            doc.db_update()          # code + item only; no validate, no repricing
            renamed += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"recode sku {r.name}")
    frappe.db.commit()
    print(f"recode_skus_to_current_naming: {renamed} recoded, {skipped} left alone "
          f"(frozen or hand-named)")


def _left_alone(row):
    """Frozen SKUs and hand-chosen codes are not ours to rewrite."""
    if row.get("rates_frozen"):
        return True
    return "auto_name" in row and not row.get("auto_name")


def _unique(code, taken):
    if code not in taken:
        return code
    n = 2
    while f"{code}_{n}" in taken:
        n += 1
    return f"{code}_{n}"
