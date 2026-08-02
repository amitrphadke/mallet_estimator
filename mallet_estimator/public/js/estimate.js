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

      frm.add_custom_button(__("Create Work Orders"), () => {
        frappe.confirm(
          __("Create a draft Work Order per article (from its BOM), linked to this Project?"),
          () => {
            frm.call("create_work_orders").then((r) => {
              const m = (r && r.message) || {};
              let body = __("Work Orders created: {0}", [(m.work_orders || []).length]);
              if (m.errors && m.errors.length) body += "<br><b>" + __("Errors") + ":</b><br>" + m.errors.join("<br>");
              body += "<br><br>" + __("Open each Work Order and <b>Submit</b> it to generate its Job Cards — one per phase, at its workstation.");
              frappe.msgprint({ title: __("Create Work Orders"), message: body, indicator: (m.errors && m.errors.length) ? "orange" : "green" });
            });
          }
        );
      }, __("Manufacture"));
    }
  },
});

// I3: transport + GST totals update INSTANTLY as trip rows are edited — no save.
frappe.ui.form.on("Estimate Transport Trip", {
  qty: (frm, cdt, cdn) => recompute_trip(frm, cdt, cdn),
  rate: (frm, cdt, cdn) => recompute_trip(frm, cdt, cdn),
  transport_items_remove: (frm) => update_estimate_totals(frm),
});

frappe.ui.form.on("Estimate", {
  gst_pct: (frm) => update_estimate_totals(frm),
});

function recompute_trip(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  if (!row) return;
  frappe.model.set_value(cdt, cdn, "amount", (row.qty || 0) * (row.rate || 0))
    .then(() => update_estimate_totals(frm));
}

function update_estimate_totals(frm) {
  const transport = (frm.doc.transport_items || []).reduce((s, t) => s + (t.amount || 0), 0);
  const skus_client = (frm.doc.skus || []).reduce((s, r) => s + (r.client_total || 0), 0);
  const skus_internal = (frm.doc.skus || []).reduce((s, r) => s + (r.internal_cost || 0), 0);
  const client = skus_client + transport;
  const gst = client * ((frm.doc.gst_pct == null ? 18 : frm.doc.gst_pct) / 100);
  frm.set_value("total_transport", transport);
  frm.set_value("total_client", client);
  frm.set_value("total_internal", skus_internal + transport);
  frm.set_value("total_gst", gst);
  frm.set_value("total_with_gst", client + gst);
}
