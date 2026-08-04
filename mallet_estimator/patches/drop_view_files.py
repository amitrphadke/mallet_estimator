import frappe


def execute():
    """The per-view PNG attachments cluttered the SKU sidebar — views are now
    extracted on the fly at print time. Delete the stored view files and clear
    the tracking field (the ISO render in article_image stays)."""
    if frappe.db.has_column("Estimate SKU", "views_images"):
        frappe.db.sql("update `tabEstimate SKU` set views_images = NULL")
    for fd in frappe.get_all(
        "File",
        filters={"attached_to_doctype": "Estimate SKU", "file_name": ["like", "%\\_view\\_%"]},
        pluck="name",
    ):
        try:
            frappe.delete_doc("File", fd, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"drop view file {fd}")
    frappe.db.commit()
