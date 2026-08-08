// Estimate: draft auto-aggregates a Project's SKUs; Submit = approve & freeze;
// approved estimate -> Create Quotation -> Build BOMs. Changes after approval go
// through Amend (native ERPNext), which keeps the approved baseline intact.
frappe.ui.form.on("Estimate", {
  refresh(frm) {
    const draft = frm.doc.docstatus === 0;
    const approved = frm.doc.docstatus === 1;

    render_estimate_bifurcation(frm);
    render_mode_headline(frm);
    // ONE grid for the SKUs: which file column it offers depends on the mode,
    // and clicking a row fills the two detail tables underneath it.
    apply_mode_columns(frm);
    bind_sku_selection(frm);
    // Creating a SKU from the grid should ask for the NAME and nothing else —
    // project, customer and mode are already settled by the estimate you are
    // standing on, so they are handed to the quick-entry dialog rather than
    // asked for again.
    const link = frm.fields_dict.skus && frm.fields_dict.skus.grid
      && frm.fields_dict.skus.grid.get_field
      && frm.fields_dict.skus.grid.get_field("estimate_sku");
    if (link) {
      link.get_route_options_for_new_doc = () => ({
        project: frm.doc.project,
        customer: frm.doc.customer,
        estimation_mode: frm.doc.estimation_mode || undefined,
        // A repair estimate makes repair SKUs: same box, same flow, and the
        // new SKU opens on its activity grid instead of asking for a CSV.
        work_type: frm.doc.work_scope === "Repair" ? "Repair" : undefined,
      });
    }
    if (!frm.is_new()) render_sku_detail(frm);

    // CSV-Nest and OCL-PDF SKUs are EXCLUSIVE on one estimate (packing is
    // computed here for CSV-Nest, by OpenCutList for PDF — the counts can't be
    // added together). Once the estimate carries SKUs, the picker only offers
    // the matching mode; the server enforces it either way.
    frm.set_query("estimate_sku", "skus", () => {
      const f = frm.doc.project ? { project: frm.doc.project } : {};
      if (frm.doc.estimation_mode) f.estimation_mode = frm.doc.estimation_mode;
      return { filters: f };
    });
    // The Add-SKUs grid: an 'Existing SKU' row picks a CSV-Nest SKU that
    // already exists (same project first); leave it blank to create one from
    // the columns beside it. Either way you never leave this screen.
    // Legacy intake grid (hidden, folded into the SKUs grid) — kept wired so
    // an estimate saved before this change still behaves if it is unhidden.
    frm.set_query("existing_sku", "intake", () => {
      const f = frm.doc.project ? { project: frm.doc.project } : {};
      if (frm.doc.estimation_mode) f.estimation_mode = frm.doc.estimation_mode;
      return { filters: f };
    });

    // The two prints, clearly separated. Both carry ONLY client-shared numbers
    // by construction (leak-safe); the execution copy adds views + purchase data.
    if (!frm.is_new()) {
      const printview = (fmt) =>
        window.open(
          frappe.urllib.get_full_url(
            "/printview?doctype=Estimate&name=" + encodeURIComponent(frm.doc.name) +
            "&format=" + encodeURIComponent(fmt) + "&no_letterhead=1"
          )
        );
      frm.add_custom_button(__("Print Client Estimate"), () => printview("Mallet Client Estimate"), __("Print"));
      frm.add_custom_button(__("Print Execution Estimate"), () => printview("Mallet Execution Estimate"), __("Print"));
    }

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
            label: __("Compare with (same project & client)"),
            get_query: () => ({ filters: {
              name: ["!=", frm.doc.name],
              project: frm.doc.project,
              customer: frm.doc.customer,
            } }),
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

    // --- Draft: SKUs are born HERE (estimate-first CSV-Nest flow) -----------
    if (draft && !frm.is_new()) {
      frm.add_custom_button(__("Add all project SKUs"), () => {
        frm.call("refresh_skus").then((r) => {
          const m = (r && r.message) || {};
          frappe.show_alert({
            message: __("Added {0} SKU(s) · {1} total · {2}{3}", [m.added || 0, m.count || 0, format_currency(m.client || 0),
              m.skipped ? __(" · {0} skipped (other mode)", [m.skipped]) : ""]),
            indicator: "green",
          });
          frm.reload_doc();
        });
      });
      frm.dashboard.add_comment(
        __("Draft — the <b>SKUs</b> grid below is the whole flow: in <b>Estimate SKU</b> either pick an existing SKU or type a new name and choose <b>Create</b>, then drop that SKU's <b>Part List CSV</b> (CSV-Nest) or <b>Material Estimate PDF</b> (OCL PDF) and its <b>7 Views PDF</b> in the same row. <b>Save</b> — each SKU arrives imported, nested, priced, with operations and décor map seeded. Click any row to read its grouped material lines and pricing summary underneath. One estimate holds ONE mode: <b>CSV-Nest</b> nests all its SKUs together (shared-material saving, valid when the set is ordered together), <b>OCL PDF</b> prices each article standalone. <b>Submit</b> to approve and freeze before quoting."),
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
  // room-wise summary: subtotals, sqft and ₹/sq ft per room
  const roomRows = (d.rooms || []).map((g) => `
      <tr><td>${esc(g.room)} <span class="text-muted">(${g.count} SKU${g.count > 1 ? "s" : ""})</span></td>
        <td class="text-right">${(g.sqft || 0).toFixed(2)}</td>
        <td class="text-right">${g.per_sqft ? money(g.per_sqft) : "—"}</td>
        <td class="text-right">${money(g.subtotal)}</td></tr>`).join("");
  const roomTable = roomRows ? `
    <h5 style="margin:18px 0 6px">Room-wise summary</h5>
    <table class="table table-bordered" style="font-size:14px">
      <thead><tr><th>Room</th><th class="text-right">Facial sq ft</th><th class="text-right">₹ / sq ft</th><th class="text-right">Subtotal (client, excl. transport &amp; GST)</th></tr></thead>
      <tbody>${roomRows}</tbody>
    </table>` : "";
  f.$wrapper.html(`
    <h5 style="margin:8px 0 6px">Bifurcation — all SKUs combined</h5>
    <table class="table table-bordered" style="font-size:14px;margin:0;width:100%">
      <thead><tr><th style="width:40%">Component</th><th class="text-right">Amount</th><th class="text-right">% of pre-tax</th><th class="text-right">GST ${b.gst_pct || 18}%</th><th class="text-right">Incl. GST</th></tr></thead>
      <tbody>${rows}
        <tr style="font-weight:700;border-top:2px solid var(--gray-600)"><td>Total before taxes</td><td class="text-right">${money(b.pre_tax)}</td><td class="text-right">100%</td><td class="text-right">${money(b.taxes)}</td><td class="text-right">${money(b.grand_total)}</td></tr>
        <tr style="font-weight:700"><td>Taxes (GST ${b.gst_pct || 18}%)</td><td class="text-right">${money(b.taxes)}</td><td colspan="3"></td></tr>
        <tr style="font-weight:700"><td>Grand Total incl. GST</td><td class="text-right">${money(b.grand_total)}</td><td colspan="3"></td></tr>
        ${sq}
      </tbody>
    </table>
    ${roomTable}`);
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

// --- Estimation v2: per-SKU files panel + bulk intake ----------------------

function esc(s) {
  return frappe.utils.escape_html ? frappe.utils.escape_html(String(s == null ? "" : s)) : String(s == null ? "" : s);
}

// Which kind of estimate am I looking at? The stored estimation_mode (derived
// from the SKUs, read-only in the header) also gets a coloured headline at the
// very top of the form, because the mode decides how the numbers below it were
// arrived at — nesting them together vs pricing each article on its own.
function render_mode_headline(frm) {
  const mode = frm.doc.estimation_mode;
  if (!mode) {
    // No SKUs yet — nothing is committed, so claim nothing.
    if (frm.dashboard.clear_headline) frm.dashboard.clear_headline();
    return;
  }
  const csv = mode === "CSV-Nest";
  const colour = csv ? "blue" : "orange";
  const rule = csv ? "#1f7aec" : "#e69500";
  const tint = csv ? "#eef5ff" : "#fff8ec";
  const what = csv
    ? __("every SKU on this estimate is nested TOGETHER — sheet counts and the shared-material saving are computed here, which holds only if the whole set is ordered together")
    : __("each SKU is priced STANDALONE from its OpenCutList PDF — no shared-material saving, so this is what an article costs if it is ordered on its own later");
  frm.dashboard.set_headline(
    `<div style="padding:6px 10px;border-left:3px solid ${rule};background:${tint}">` +
      `<b>${esc(mode)}</b> ${esc(__("estimate"))} — ${esc(what)}.</div>`,
    colour
  );
}

// --- The SKUs grid is the ONE table ---------------------------------------
// Search / select / create a SKU in its link column, drop this SKU's input
// files in the same row, read its numbers in the same row. Which file column
// you get depends on the mode, because a SKU takes ONE kind of input.
function apply_mode_columns(frm) {
  const grid = frm.fields_dict.skus && frm.fields_dict.skus.grid;
  if (!grid || typeof grid.update_docfield_property !== "function") return;
  const mode = frm.doc.estimation_mode;
  // Before the first SKU the estimate is committed to neither mode, so offer
  // both columns — whichever file lands first decides it.
  // A repair estimate takes no OpenCutList input at all — its work is typed
  // into the activity grid — so it is offered no file columns.
  const repair = frm.doc.work_scope === "Repair";
  const show = { parts_csv: !repair && (!mode || mode === "CSV-Nest"),
                 estimate_pdf: !repair && (!mode || mode === "OCL PDF (standard)"),
                 views_pdf: !repair };
  Object.keys(show).forEach((f) => {
    try {
      grid.update_docfield_property(f, "hidden", show[f] ? 0 : 1);
      grid.update_docfield_property(f, "in_list_view", show[f] ? 1 : 0);
    } catch (e) {
      // a pre-migrate site has no such column — never break the form over it
    }
  });
  grid.refresh();
}

// Selecting a row drives the two detail tables below the grid. Selection is a
// UI concern only (never stored), so it lives on the form object.
function bind_sku_selection(frm) {
  const grid = frm.fields_dict.skus && frm.fields_dict.skus.grid;
  if (!grid || !grid.wrapper) return;
  grid.wrapper.off("click.mallet_sku").on("click.mallet_sku", ".grid-row", function () {
    const cdn = $(this).attr("data-name");
    const row = cdn && locals["Execution Estimate SKU"] && locals["Execution Estimate SKU"][cdn];
    if (row && row.estimate_sku && row.estimate_sku !== frm.__selected_sku) {
      select_sku(frm, row.estimate_sku);
    }
  });
}

function select_sku(frm, sku) {
  frm.__selected_sku = sku;
  highlight_selected_row(frm);
  render_sku_detail(frm);
}

function highlight_selected_row(frm) {
  const grid = frm.fields_dict.skus && frm.fields_dict.skus.grid;
  if (!grid || !grid.wrapper) return;
  grid.wrapper.find(".grid-row").each(function () {
    const cdn = $(this).attr("data-name");
    const row = cdn && locals["Execution Estimate SKU"] && locals["Execution Estimate SKU"][cdn];
    const on = row && row.estimate_sku === frm.__selected_sku;
    $(this).css("box-shadow", on ? "inset 3px 0 0 0 #1f7aec" : "");
  });
}

// Pick up where the user left off; otherwise open on the first SKU so the
// detail tables are never empty for no reason.
function render_sku_detail(frm) {
  const $mat = frm.get_field("sku_materials_html") && frm.get_field("sku_materials_html").$wrapper;
  const $sum = frm.get_field("sku_summary_html") && frm.get_field("sku_summary_html").$wrapper;
  if (!$mat || !$sum) return;
  const rows = (frm.doc.skus || []).filter((r) => r.estimate_sku);
  if (!rows.length) {
    frm.__selected_sku = null;
    $mat.html(`<div class="text-muted">${esc(
      __("No SKUs yet. Add a row above: pick an existing SKU or type a new name to create one, then drop its Part List CSV (CSV-Nest) or Material Estimate PDF (OCL PDF) in the same row and Save."))}</div>`);
    $sum.empty();
    return;
  }
  if (!rows.some((r) => r.estimate_sku === frm.__selected_sku)) {
    frm.__selected_sku = rows[0].estimate_sku;
  }
  highlight_selected_row(frm);
  const sku = frm.__selected_sku;
  $mat.html(`<div class="text-muted">${esc(__("Loading…"))}</div>`);
  $sum.empty();
  if (frm.__board && frm.__board.sku === sku) {
    frm.__board.load();
  } else {
    frm.__board = new mallet.MaterialBoard({
      wrapper: $mat,
      sku: sku,
      // The estimate is where the numbers are read AND corrected — the board
      // is as editable here as it is on the SKU form; there is just less room.
      editable: frm.doc.docstatus === 0,
      on_change: () => frm.reload_doc(),
    });
    frm.__board.load();
  }
  frm.call("sku_materials", { sku }).then((r) => {
    if (frm.__selected_sku !== sku) return;
    $sum.html(render_pricing_summary((r && r.message) || {}));
  });
}

// The client pricing summary for the selected SKU — the same bifurcation the
// SKU prints, plus the man-days behind it (what the client is really buying).
function render_pricing_summary(m) {
  const b = m.bifurcation || {};
  if (!b.rows || !b.rows.length) {
    return `<div class="text-muted">${esc(__("No pricing yet for this SKU."))}</div>`;
  }
  const gst = b.gst_pct || 18;
  const rows = b.rows.map((r) => `<tr>
      <td>${esc(r.label)}</td>
      <td class="text-right">${format_currency(r.amount || 0)}</td>
      <td class="text-right">${format_number(r.pct || 0, null, 1)}%</td>
      <td class="text-right">${format_currency(r.gst || 0)}</td>
      <td class="text-right">${format_currency(r.gross || 0)}</td>
    </tr>`).join("");
  const d = m.days || {};
  const per_day = d.productive_min_per_day || 360;
  const days_rows = `
    <tr><td>${esc(__("Carpenter minutes"))}</td>
        <td class="text-right">${format_number(d.carp_min || 0)}</td>
        <td class="text-right" colspan="3">${esc(__("{0} min = 1 productive day", [per_day]))}</td></tr>
    <tr><td>${esc(__("Helper minutes"))}</td>
        <td class="text-right">${format_number(d.helper_min || 0)}</td>
        <td class="text-right" colspan="3"></td></tr>
    <tr><td><b>${esc(__("Man-days for this SKU"))}</b></td>
        <td class="text-right"><b>${format_number(d.est_days || 0, null, 2)}</b></td>
        <td class="text-right text-muted" colspan="3">${esc(
          __("longer of the two trades ÷ {0} min", [per_day]))}</td></tr>`;
  const sq = m.sqft || {};
  const sqft_rows = sq.sqft
    ? `<tr style="background:#f4f5f6"><td colspan="5"><b>${esc(
         __("Per square foot (facial area: {0} sq ft — two greatest outer dims)",
            [format_number(sq.sqft, null, 2)]))}</b></td></tr>
       <tr><td>${esc(__("Material / sq ft"))}</td>
           <td class="text-right">${format_currency(sq.material_per_sqft || 0)}</td>
           <td colspan="3"></td></tr>
       <tr><td>${esc(__("Labor (design & execution) / sq ft"))}</td>
           <td class="text-right">${format_currency(sq.labor_per_sqft || 0)}</td>
           <td colspan="3"></td></tr>
       <tr><td><b>${esc(__("SKU / sq ft (pre-tax)"))}</b></td>
           <td class="text-right"><b>${format_currency(sq.total_per_sqft || 0)}</b></td>
           <td colspan="3"></td></tr>`
    : "";
  return `
    <div style="margin-bottom:4px"><b>${esc(__("Pricing summary"))}</b>
      <span class="text-muted small"> — ${esc(m.article || m.sku)} · ${esc(__("client pricing"))}</span></div>
    <div style="overflow-x:auto">
      <table class="table table-bordered" style="font-size:12px;margin:0">
        <thead><tr>
          <th>${esc(__("Component"))}</th>
          <th class="text-right">${esc(__("Amount"))}</th>
          <th class="text-right">${esc(__("% of pre-tax"))}</th>
          <th class="text-right">${esc(__("GST {0}%", [gst]))}</th>
          <th class="text-right">${esc(__("Incl. GST"))}</th>
        </tr></thead>
        <tbody>
          ${rows}
          <tr style="border-top:2px solid #d1d8dd">
            <td><b>${esc(__("Total before taxes"))}</b></td>
            <td class="text-right"><b>${format_currency(b.pre_tax || 0)}</b></td>
            <td class="text-right">100%</td>
            <td class="text-right"><b>${format_currency(b.taxes || 0)}</b></td>
            <td class="text-right"><b>${format_currency(b.grand_total || 0)}</b></td>
          </tr>
          <tr><td><b>${esc(__("Grand Total incl. GST"))}</b></td>
              <td class="text-right"><b>${format_currency(b.grand_total || 0)}</b></td>
              <td colspan="3"></td></tr>
          <tr style="background:#f4f5f6"><td colspan="5"><b>${esc(__("Effort"))}</b></td></tr>
          ${days_rows}
          ${sqft_rows}
        </tbody>
      </table>
    </div>
    <div class="small text-muted" style="margin-top:4px">${esc(
      __("Transport is billed on the Estimate (trips shared across SKUs)."))}</div>`;
}
