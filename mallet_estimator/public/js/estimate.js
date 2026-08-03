// Estimate: draft auto-aggregates a Project's SKUs; Submit = approve & freeze;
// approved estimate -> Create Quotation -> Build BOMs. Changes after approval go
// through Amend (native ERPNext), which keeps the approved baseline intact.
frappe.ui.form.on("Estimate", {
  refresh(frm) {
    const draft = frm.doc.docstatus === 0;
    const approved = frm.doc.docstatus === 1;

    render_estimate_bifurcation(frm);

    // Margin text boxes — same global margins as on the SKU form.
    if (draft && !frm.is_new()) {
      frm.add_custom_button(__("Set margins %"), () => estimate_margins_dialog(frm));
    }

    // Scale mode: design the WHOLE estimate as one SKU (shared sheets, one-go
    // cutting) — creates a combined Estimate SKU pre-filled with every member
    // SKU's dims + facial sqft and their ISO renders; attach the whole-project
    // estimate PDF + part list there.
    if (!frm.is_new() && (frm.doc.skus || []).length) {
      frm.add_custom_button(__("Create combined SKU (scale mode)"), () => {
        frappe.confirm(
          __("Create ONE combined SKU carrying every member SKU's details and facial area? It is excluded from estimate totals until you switch over."),
          () =>
            frm.call("create_combined_sku").then((r) => {
              const m = (r && r.message) || {};
              if (m.name) {
                frappe.show_alert({
                  message: __("Combined SKU {0}: {1} member(s), {2} sq ft facial area", [m.name, m.members, (m.sqft || 0).toFixed(2)]),
                  indicator: "green",
                }, 6);
                frappe.set_route("Form", "Estimate SKU", m.name);
              }
            })
        );
      });
    }

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

// Margin text boxes (global Estimate Settings) — decide the % made on each
// total; applying re-pulls the SKUs so the aggregated bifurcation reprices.
function estimate_margins_dialog(frm) {
  frappe.call("mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku.get_margins").then((r) => {
    const m = (r && r.message) || {};
    const d = new frappe.ui.Dialog({
      title: __("Margins — % you make on each total"),
      fields: [
        { fieldname: "material", fieldtype: "Percent", label: __("Material margin %"), default: m.material },
        { fieldname: "labor", fieldtype: "Percent", label: __("Labor margin %"), default: m.labor },
        { fieldname: "overhead", fieldtype: "Percent", label: __("Overhead margin %"), default: m.overhead },
        { fieldname: "design", fieldtype: "Percent", label: __("Design margin %"), default: m.design },
      ],
      primary_action_label: __("Apply"),
      primary_action(values) {
        d.hide();
        frappe.call("mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku.set_margins", values).then(() => {
          frappe.show_alert({ message: __("Margins applied — repricing all SKUs"), indicator: "green" }, 4);
          frm.call("refresh_skus").then(() => frm.reload_doc());
        });
      },
    });
    d.show();
  });
}

// Same bifurcation table as on each SKU, aggregated (built server-side on save).
function render_estimate_bifurcation(frm) {
  const f = frm.get_field("cost_breakup_html");
  if (!f || !f.$wrapper) return;
  let d = null;
  try { d = JSON.parse(frm.doc.cost_breakup || "null"); } catch (e) { d = null; }
  const b = d && d.bifurcation;
  if (!b || !(b.rows || []).length) { f.$wrapper.empty(); return; }
  const money = (v) => format_currency(v || 0);
  const esc = frappe.utils.escape_html;
  const rows = b.rows.map((r) => `
      <tr><td>${esc(r.label)}</td>
        <td class="text-right">${money(r.amount)}</td>
        <td class="text-right">${(r.pct || 0).toFixed(1)}%</td>
        <td class="text-right">${money(r.gst)}</td>
        <td class="text-right">${money(r.gross)}</td></tr>`).join("");
  const sq = d.sqft ? `
      <tr style="background:var(--subtle-fg, #f4f5f6)"><td colspan="5" style="font-weight:700">Per square foot (total facial area: ${(d.sqft.sqft || 0).toFixed(2)} sq ft across all SKUs)</td></tr>
      <tr><td>Material / sq ft</td><td class="text-right">${money(d.sqft.material_per_sqft)}</td><td colspan="3"></td></tr>
      <tr><td>Labor (design &amp; execution) / sq ft</td><td class="text-right">${money(d.sqft.labor_per_sqft)}</td><td colspan="3"></td></tr>
      <tr style="font-weight:700"><td>Estimate / sq ft (pre-tax, excl. transport)</td><td class="text-right">${money(d.sqft.total_per_sqft)}</td><td colspan="3"></td></tr>` : "";
  f.$wrapper.html(`
    <h6 style="margin:8px 0 4px">Bifurcation — all SKUs combined</h6>
    <table class="table table-bordered" style="font-size:12.5px;margin:0">
      <thead><tr><th>Component</th><th class="text-right">Amount</th><th class="text-right">% of pre-tax</th><th class="text-right">GST ${b.gst_pct || 18}%</th><th class="text-right">Incl. GST</th></tr></thead>
      <tbody>${rows}
        <tr style="font-weight:700;border-top:2px solid var(--gray-600)"><td>Total before taxes</td><td class="text-right">${money(b.pre_tax)}</td><td class="text-right">100%</td><td class="text-right">${money(b.taxes)}</td><td class="text-right">${money(b.grand_total)}</td></tr>
        <tr style="font-weight:700"><td>Taxes (GST ${b.gst_pct || 18}%)</td><td class="text-right">${money(b.taxes)}</td><td colspan="3"></td></tr>
        <tr style="font-weight:700"><td>Grand Total incl. GST</td><td class="text-right">${money(b.grand_total)}</td><td colspan="3"></td></tr>
        ${sq}
      </tbody>
    </table>`);
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
