import frappe

# These articles were named after the file they were imported from, so the
# generated code said the room twice and the file format once:
# YS_MB_MB_WAR_CSV. The grammar is not at fault — "MB_WAR_CSV" is genuinely
# what the article was called — so the fix is the name, not the rule.
#
# Matched on the CURRENT article name rather than on document ids, so this is
# a no-op on any site that does not carry them, and it stays readable as what
# it is: four names being corrected.
RENAMES = {
    "MB_WAR_CSV": "Wardrobe",
    "MB_LOFT_CSV": "Loft",
    "MB_BED_CSV": "Bed",
    "GB_BED_CSV": "Bed",
}


def execute():
    """Rename the articles that were called after their import file.

    Runs BEFORE recode_skus_to_current_naming on purpose. Renaming first means
    the recode derives the final code in one pass — YS_MB_WAR — and each Item
    is renamed once. The other order would rename every Item twice, first to
    YS_MB_MB_WAR_CSV and then again, for no reason a reader of the stock ledger
    could work out afterwards.

    A frozen SKU is left alone: approve means freeze, and a client has been
    shown that name."""
    if not frappe.db.has_column("Estimate SKU", "article_name"):
        return
    frozen_field = frappe.get_meta("Estimate SKU").has_field("rates_frozen")
    done = 0
    for old, new in RENAMES.items():
        for name in frappe.get_all("Estimate SKU", filters={"article_name": old},
                                   pluck="name"):
            if frozen_field and frappe.db.get_value("Estimate SKU", name, "rates_frozen"):
                continue
            frappe.db.set_value("Estimate SKU", name, "article_name", new,
                                update_modified=False)
            done += 1
    frappe.db.commit()
    print(f"rename_csv_article_names: {done} article(s) renamed")
