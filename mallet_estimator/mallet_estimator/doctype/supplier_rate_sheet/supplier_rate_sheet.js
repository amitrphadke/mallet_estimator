// S6: import a supplier's rate list -> catalogue Items + per-supplier MRP prices.
frappe.ui.form.on("Supplier Rate Sheet", {
  refresh(frm) {
    if (frm.is_new()) return;
    frm.add_custom_button(__("Import rates"), () => {
      if (!frm.doc.rate_file && !frm.doc.rate_csv) {
        frappe.msgprint(__("Attach a rate CSV file or paste CSV text first."));
        return;
      }
      frappe.confirm(
        __("Create/enrich Items from this rate list and record {0}'s prices?", [frm.doc.supplier || "the supplier"]),
        () => {
          frm.call("import_now").then((r) => {
            const m = (r && r.message) || {};
            frappe.show_alert({
              message: __("Imported {0} row(s) · priced {1}", [m.rows || 0, m.priced || 0]),
              indicator: (m.errors && m.errors.length) ? "orange" : "green",
            });
            frm.reload_doc();
          });
        }
      );
    }).addClass("btn-primary");
  },
});
