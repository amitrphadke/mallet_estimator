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

    // Scale comparison: pick another estimate (same SKUs, but modelled as ONE
    // SketchUp file with its own whole-project PDFs) and see bucket-by-bucket
    // what the single-file design saves in material + operation time.
    if (!frm.is_new()) {
      frm.add_custom_button(__("Compare with estimate…"), () => {
        const d = new frappe.ui.Dialog({
          title: __("Compare estimates"),
          fields: [{
            fieldname: "other", fieldtype: "Link", options: "Estimate", reqd: 1,
            label: __("Compare with"),
            get_query: () => ({ filters: { name: ["!=", frm.doc.name] } }),
          }],
          primary_action_label: __("Compare"),
          primary_action(values) {
            d.hide();
            frm.call("compare_with", { other: values.other }).then((r) => {
              const m = (r && r.message) || {};
              if (m.rows) show_estimate_comparison(m);
            });
          },
        });
        d.show();
      });
    }

    // --- Draft: pull in SKUs added after this estimate was created ----------
    if (draft && !frm.is_new()) {
      frm.add_custom_button(__("Add all project SKUs"), () => {
        frm.call("refresh_skus").then((r) => {
          const m = (r && r.message) || {};
          frappe.show_alert({
            message: __("Added {0} SKU(s) · {1} total · {2}", [m.added || 0, m.count || 0, format_currency(m.client || 0)]),
            indicator: "green",
          });
          frm.reload_doc();
        });
      }).addClass("btn-primary");
      frm.dashboard.add_comment(
        __("Draft — pick this estimate's SKUs yourself (Add Row in the SKU table, or 'Add all project SKUs'). The same SKU can serve many estimates. <b>Submit</b> to approve and freeze before quoting."),
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

// Side-by-side estimate comparison (per-SKU PDFs vs one-file whole-project
// PDFs): each bucket's amount in both, the delta, and the saving %.
function show_estimate_comparison(m) {
  const money = (v) => format_currency(v || 0);
  const esc = frappe.utils.escape_html;
  const rows = (m.rows || []).map((r) => {
    const good = (r.delta || 0) < 0; // B cheaper than A = saving
    const style = r.bold ? "font-weight:700;" : "";
    const dstyle = `${style}color:${good ? "var(--green-600, #16794c)" : r.delta ? "var(--red-600, #c0392b)" : "inherit"}`;
    return `<tr style="${style}"><td>${esc(r.label)}</td>
      <td class="text-right">${money(r.a)}</td>
      <td class="text-right">${money(r.b)}</td>
      <td class="text-right" style="${dstyle}">${money(r.delta)}</td>
      <td class="text-right" style="${dstyle}">${r.pct ? r.pct.toFixed(1) + "%" : ""}</td></tr>`;
  }).join("");
  frappe.msgprint({
    title: __("Estimate comparison"),
    wide: true,
    message: `
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr><th>${__("Component")}</th>
          <th class="text-right">${esc(m.a)}</th>
          <th class="text-right">${esc(m.b)}</th>
          <th class="text-right">${__("Δ (B − A)")}</th>
          <th class="text-right">${__("Δ %")}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="text-muted" style="font-size:11.5px">${__("Green = the compared estimate is cheaper (the scale saving of the one-file design).")}</p>`,
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
