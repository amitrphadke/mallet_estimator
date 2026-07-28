// Estimate: draft auto-aggregates a Project's SKUs; Submit = approve & freeze;
// approved estimate -> Create Quotation -> Build BOMs. Changes after approval go
// through Amend (native ERPNext), which keeps the approved baseline intact.
frappe.ui.form.on("Estimate", {
  refresh(frm) {
    const draft = frm.doc.docstatus === 0;
    const approved = frm.doc.docstatus === 1;

    // --- Draft: pull in SKUs added after this estimate was created ----------
    if (draft && !frm.is_new()) {
      frm.add_custom_button(__("Refresh SKUs"), () => {
        frm.call("refresh_skus").then((r) => {
          const m = (r && r.message) || {};
          frappe.show_alert({
            message: __("Pulled {0} SKU(s) · total {1}", [m.count || 0, format_currency(m.client || 0)]),
            indicator: "green",
          });
          frm.reload_doc();
        });
      }).addClass("btn-primary");
      frm.dashboard.add_comment(
        __("Draft — SKUs auto-refresh as you add them. <b>Submit</b> to approve and freeze this estimate before quoting."),
        "blue", true
      );
    }

    // --- Approved: quotation / BOMs ---------------------------------------
    if (frm.doc.quotation) {
      frm.add_custom_button(__("View Quotation"), () =>
        frappe.set_route("Form", "Quotation", frm.doc.quotation)
      );
    } else if (approved) {
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

    if (approved) {
      frm.add_custom_button(__("Build BOMs"), () => {
        frappe.confirm(__("Create/refresh a BOM per SKU (materials + operations) for manufacturing?"), () => {
          frm.call("build_boms").then((r) => {
            const m = (r && r.message) || {};
            let body = __("BOMs created: {0}", [(m.boms || []).length]);
            if (m.errors && m.errors.length) body += "<br><b>" + __("Errors") + ":</b><br>" + m.errors.join("<br>");
            frappe.msgprint({ title: __("Build BOMs"), message: body, indicator: (m.errors && m.errors.length) ? "orange" : "green" });
          });
        });
      }, __("Manufacture"));
    }
  },
});
