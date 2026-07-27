// Estimate Settings: on-demand creation of the ERPNext manufacturing masters
// (Workstations, Operations, Routing) + print format + workspace.
frappe.ui.form.on("Estimate Settings", {
  refresh(frm) {
    frm.add_custom_button(__("Create / refresh manufacturing masters"), () => {
      frappe.call({
        method: "mallet_estimator.install.setup",
        freeze: true,
        freeze_message: __("Creating Workstations, Operations, Routing…"),
      }).then((r) => {
        const m = (r && r.message) || {};
        let body = __("Workstations created: {0}<br>Operations created: {1}<br>Routing created: {2}", [
          m.workstations || 0, m.operations || 0, m.routing || 0,
        ]);
        if (m.errors && m.errors.length) {
          body += "<br><br><b>" + __("Errors") + ":</b><br>" + frappe.utils.escape_html(m.errors.join("\n")).replace(/\n/g, "<br>");
        }
        frappe.msgprint({
          title: __("Manufacturing setup"),
          message: body,
          indicator: (m.errors && m.errors.length) ? "orange" : "green",
        });
      });
    });
  },
});
