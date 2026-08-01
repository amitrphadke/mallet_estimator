import frappe
from frappe.model.document import Document

from mallet_estimator.rate_import import import_supplier_rates


class SupplierRateSheet(Document):
    def _csv_content(self):
        if self.rate_csv:
            return self.rate_csv
        if self.rate_file:
            f = frappe.get_doc("File", {"file_url": self.rate_file})
            content = f.get_content()
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            return content
        return None

    @frappe.whitelist()
    def import_now(self):
        text = self._csv_content()
        if not text:
            frappe.throw("Attach a rate CSV file or paste CSV text first.")
        res = import_supplier_rates(
            self.supplier, text, manufacturer=self.manufacturer,
            item_group=self.item_group, kind=self.kind or "hardware",
        )
        summary = f"Rows {res['rows']} · items {res['items']} · priced {res['priced']}"
        if res["errors"]:
            summary += f" · errors {len(res['errors'])}\n" + "\n".join(res["errors"][:20])
        summary += "\n\n" + "\n".join(res["log"][:200])
        self.db_set("results", summary)
        return res
