// Execution Estimate: create a Quotation from the rolled-up SKUs.
frappe.ui.form.on("Execution Estimate", {
  refresh(frm) {
    if (frm.doc.quotation) {
      frm.add_custom_button(__("View Quotation"), () =>
        frappe.set_route("Form", "Quotation", frm.doc.quotation)
      );
    } else if (!frm.is_new()) {
      frm.add_custom_button(__("Create Quotation"), () => {
        frappe.confirm(
          __("Create an ERPNext Quotation for {0} with one line per SKU?", [frm.doc.customer]),
          () => {
            frm.call("create_quotation").then((r) => {
              if (r && r.message) {
                frappe.show_alert({ message: __("Quotation {0} created", [r.message]), indicator: "green" });
                frm.reload_doc();
              }
            });
          }
        );
      }).addClass("btn-primary");
    }
  },
});
