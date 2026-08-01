// Project (F4): abstract material code -> chosen actual Item + vendor + rate.
// "Refresh assumed rates" fills the planning rate + variance from the Estimation
// price list (read-only). "Apply choices" pushes each actual rate onto the real
// buying price list + records the vendor, so procurement uses the chosen price —
// the estimate keeps valuing at assumed, and Project margin shows the variance.
frappe.ui.form.on("Project", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button(__("Refresh assumed rates"), () => {
      frappe.call({
        method: "mallet_estimator.inventory.refresh_material_choices",
        args: { project: frm.doc.name },
      }).then((r) => {
        const m = (r && r.message) || {};
        frappe.show_alert({ message: __("Refreshed {0} choice(s)", [m.rows || 0]), indicator: "green" });
        frm.reload_doc();
      });
    }, __("Material Choices"));

    frm.add_custom_button(__("Apply choices (procurement)"), () => {
      frappe.confirm(
        __("Push each choice's ACTUAL rate onto the buying price list and record its vendor? The estimate keeps using the assumed rate."),
        () => {
          frappe.call({
            method: "mallet_estimator.inventory.apply_material_choices",
            args: { project: frm.doc.name },
          }).then((r) => {
            const m = (r && r.message) || {};
            let body = __("Applied: {0}", [(m.applied || []).length]);
            if (m.skipped && m.skipped.length) {
              body += "<br>" + __("Skipped (no item/rate): {0}", [m.skipped.join(", ")]);
            }
            frappe.msgprint({ title: __("Apply choices"), message: body, indicator: "green" });
          });
        }
      );
    }, __("Material Choices"));
  },
});
