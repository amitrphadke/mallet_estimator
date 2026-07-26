from frappe.model.document import Document

from mallet_estimator.estimator import DEFAULT_MACHINES


class EstimateSettings(Document):
    def onload(self):
        # Seed the standard machines the first time the settings are opened.
        if not self.machines:
            for m in DEFAULT_MACHINES:
                self.append("machines", m)
