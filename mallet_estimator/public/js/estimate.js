// Estimate: draft auto-aggregates a Project's SKUs; Submit = approve & freeze;
// approved estimate -> Create Quotation -> Build BOMs. Changes after approval go
// through Amend (native ERPNext), which keeps the approved baseline intact.
frappe.ui.form.on("Estimate", {
  refresh(frm) {
    const draft = frm.doc.docstatus === 0;
    const approved = frm.doc.docstatus === 1;

    render_estimate_bifurcation(frm);
    render_mode_headline(frm);
    if (!frm.is_new()) render_sku_files(frm);

    // CSV-Nest and OCL-PDF SKUs are EXCLUSIVE on one estimate (packing is
    // computed here for CSV-Nest, by OpenCutList for PDF — the counts can't be
    // added together). Once the estimate carries SKUs, the picker only offers
    // the matching mode; the server enforces it either way.
    frm.set_query("estimate_sku", "skus", () => {
      const mode = frm.__estimate_mode;
      return mode ? { filters: { estimation_mode: mode } } : {};
    });
    // The Add-SKUs grid: an 'Existing SKU' row picks a CSV-Nest SKU that
    // already exists (same project first); leave it blank to create one from
    // the columns beside it. Either way you never leave this screen.
    frm.set_query("existing_sku", "intake", () => {
      const f = frm.doc.project ? { project: frm.doc.project } : {};
      if (frm.__estimate_mode) f.estimation_mode = frm.__estimate_mode;
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
        __("Draft — use the <b>Add SKUs</b> grid above: each row either picks an <b>existing</b> SKU or <b>creates</b> one (Room · Name · then either a <b>Part List CSV</b> for CSV-Nest or a <b>Material Estimate PDF</b> for OCL-PDF; 7 Views PDF either way). Add as many rows as you need, then <b>Save</b> — created SKUs arrive priced, with operations, workstations and décor map seeded. One estimate holds ONE mode: <b>CSV-Nest</b> nests all its SKUs together (shared-material saving, valid when the set is ordered together), <b>OCL PDF</b> prices each article standalone (what an article costs if ordered on its own later). <b>Submit</b> to approve and freeze before quoting."),
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

// One line per SKU right on the estimate: article | room | Part CSV | Views
// PDF | sheets | issues | total | open. Attach/replace either file without
// leaving this screen; the SKU save re-prices the estimate (consolidation
// included) and the panel re-renders on reload.
function render_sku_files(frm) {
  const $w = frm.get_field("sku_files_html") && frm.get_field("sku_files_html").$wrapper;
  if (!$w) return;
  if (!(frm.doc.skus || []).length) {
    $w.empty();
    return;
  }
  frm.call("sku_files_overview").then((r) => {
    const rows = (r && r.message) || [];
    if (!rows.length) {
      frm.__estimate_mode = null;
      $w.empty();
      return;
    }
    const modes = Array.from(new Set(rows.map((x) => x.mode || "OCL PDF (standard)")));
    frm.__estimate_mode = modes.length === 1 ? modes[0] : null;
    const draft = frm.doc.docstatus === 0;
    const file_cell = (row, fieldname, label) => {
      const url = row[fieldname];
      const chip = url
        ? `<a href="${encodeURI(url)}" target="_blank" title="${esc(label)}">✓ ${esc(label)}</a>`
        : `<span class="text-muted">✗ ${esc(label)}</span>`;
      const btn = draft && !row.frozen
        ? ` <button class="btn btn-xs btn-default sku-attach" data-sku="${esc(row.sku)}"
              data-field="${esc(fieldname)}" title="${esc(__("Attach / replace"))}">📎</button>`
        : "";
      return chip + btn;
    };
    const body = rows.map((row) => {
      const badges =
        (row.mode === "CSV-Nest" ? ` <span class="badge">CSV-Nest</span>` : "") +
        (row.frozen ? ` <span class="badge">${esc(__("frozen"))}</span>` : "") +
        (row.unpriced ? ` <span class="badge" style="background:#e24c4c;color:#fff">${esc(__("unpriced"))}</span>` : "") +
        (row.issues ? ` <span class="badge" style="background:#e69500;color:#fff">${row.issues} ${esc(__("issue(s)"))}</span>` : "");
      return `<tr class="sku-line" data-sku="${esc(row.sku)}" style="cursor:pointer"
                  title="${esc(__("Click to show this SKU's material lines here"))}">
        <td><span class="sku-caret" style="display:inline-block;width:12px">▸</span>
            <b>${esc(row.article || row.sku)}</b>
            <div class="small text-muted" style="margin-left:12px">${esc(row.code || "")}
              · <a href="/app/estimate-sku/${encodeURIComponent(row.sku)}"
                   onclick="event.stopPropagation()">${esc(__("open"))}</a></div></td>
        <td>${esc(row.room || "")}</td>
        <td>${file_cell(row, "parts_csv", __("Part CSV"))}</td>
        <td>${file_cell(row, "views_pdf", __("Views PDF"))}</td>
        <td class="text-right">${row.sheets ? cstr(Math.round(row.sheets * 100) / 100) : ""}</td>
        <td class="text-right">${row.client_total != null ? format_currency(row.client_total) : ""}${badges}</td>
      </tr>
      <tr class="sku-mats" data-for="${esc(row.sku)}" style="display:none">
        <td colspan="6" style="background:#fbfbfb"></td>
      </tr>`;
    }).join("");
    let mode_note;
    if (modes.length !== 1) {
      mode_note = `<div class="text-danger small" style="margin-top:6px">${esc(
        __("Mixed estimation modes — save will refuse this; keep one mode per estimate."))}</div>`;
    } else if (modes[0] === "CSV-Nest") {
      mode_note = `<div class="text-muted small" style="margin-top:6px">${esc(__("Estimation mode:"))}
        <b>CSV-Nest</b> — ${esc(__("parts of ALL these SKUs are nested together, so each price already includes the shared-material saving. Valid only if the client orders this set together."))}</div>`;
    } else {
      mode_note = `<div class="small" style="margin-top:6px;padding:6px 8px;border-left:3px solid #e69500;background:#fff8ec">
        ${esc(__("Estimation mode:"))} <b>OCL PDF (standard)</b> — ${esc(
        __("sheet counts come already packed from each SKU's OpenCutList PDF. These prices are STANDALONE: no shared-material saving, because an article ordered on its own needs its own purchase → cutting → installation run. Use this basis when quoting articles the client may pick individually later."))}</div>`;
    }
    $w.html(`
      ${mode_note}
      <div style="overflow-x:auto">
        <table class="table table-bordered" style="margin-top:6px">
          <thead><tr>
            <th>${esc(__("Article"))}</th><th>${esc(__("Room"))}</th>
            <th>${esc(__("Part List CSV"))}</th><th>${esc(__("7 Views PDF"))}</th>
            <th class="text-right">${esc(__("Sheets"))}</th>
            <th class="text-right">${esc(__("Client Total"))}</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`);
    $w.find(".sku-attach").on("click", function (e) {
      e.stopPropagation();
      sku_attach_uploader(frm, $(this).data("sku"), $(this).data("field"));
    });
    // Click a line to read its material lines right here — no page jump.
    $w.find("tr.sku-line").on("click", function () {
      const sku = $(this).data("sku");
      const $slot = $w.find(`tr.sku-mats[data-for="${sku}"]`);
      const $caret = $(this).find(".sku-caret");
      if ($slot.is(":visible")) {
        $slot.hide();
        $caret.text("▸");
        return;
      }
      $slot.show();
      $caret.text("▾");
      if ($slot.data("loaded")) return;
      $slot.find("td").html(`<span class="text-muted">${esc(__("Loading…"))}</span>`);
      frm.call("sku_materials", { sku }).then((r) => {
        $slot.data("loaded", 1);
        $slot.find("td").html(render_sku_materials((r && r.message) || {}));
      });
    });
  });
}

function sku_attach_uploader(frm, sku, fieldname) {
  const csv = fieldname === "parts_csv";
  new frappe.ui.FileUploader({
    doctype: "Estimate SKU",
    docname: sku,
    allow_multiple: false,
    restrictions: { allowed_file_types: [csv ? ".csv" : ".pdf"] },
    on_success(file) {
      frappe.call({
        method: "mallet_estimator.api.attach_sku_file",
        args: { sku, fieldname, file_url: file.file_url },
        freeze: true,
        freeze_message: __("Importing…"),
        callback() {
          frappe.show_alert({ message: __("{0} updated", [sku]), indicator: "green" });
          frm.reload_doc();
        },
      });
    },
  });
}


// Read-only material lines of one SKU, rendered inside the files panel. Costed
// exactly as the SKU stores them (the estimate's consolidated allocation is
// reported separately in the cost breakup); nothing here is editable — the SKU
// form remains the only place material lines can change.
function render_sku_materials(m) {
  const rows = m.rows || [];
  if (!rows.length) {
    return `<span class="text-muted">${esc(__("No material lines yet — attach this SKU's Part List CSV."))}</span>`;
  }
  const body = rows.map((r) => {
    const flags =
      (r.manual ? ` <span class="badge">${esc(__("manual"))}</span>` : "") +
      (r.client_supplied ? ` <span class="badge">${esc(__("client-supplied"))}</span>` : "");
    return `<tr>
      <td><code>${esc(r.material || "")}</code>${flags}
          <div class="small text-muted">${esc(r.description || "")}</div></td>
      <td>${esc(r.item || "")}</td>
      <td class="text-right">${format_number(r.qty || 0)}</td>
      <td>${esc(r.uom || "")}</td>
      <td class="text-right">${format_currency(r.rate || 0)}${
        r.discount ? `<div class="small text-muted">− ${format_currency(r.discount)}</div>` : ""}</td>
      <td class="text-right">${format_currency(r.amount || 0)}</td>
      <td class="text-right">${format_currency(r.tax || 0)}<div class="small text-muted">${
        cstr(r.applied_tax || r.std_tax || 0)}%${
        r.tax_saved ? " · −" + format_currency(r.tax_saved) : ""}</div></td>
      <td class="text-right"><b>${format_currency(r.amount_with_tax || 0)}</b></td>
    </tr>`;
  }).join("");
  return `
    <div class="small text-muted" style="margin-bottom:4px">
      ${esc(__("Material lines"))} — <b>${esc(m.article || m.sku)}</b> · ${esc(m.mode || "")}
      · ${esc(__("material cost"))} ${format_currency(m.material_cost || 0)}
      · <i>${esc(__("read-only; edit on the SKU form"))}</i>
    </div>
    <div style="overflow-x:auto">
      <table class="table table-bordered" style="font-size:12px;margin:0">
        <thead><tr>
          <th>${esc(__("Generic code"))}</th><th>${esc(__("Item"))}</th>
          <th class="text-right">${esc(__("Qty"))}</th><th>${esc(__("UOM"))}</th>
          <th class="text-right">${esc(__("MRP"))}</th>
          <th class="text-right">${esc(__("Taxable"))}</th>
          <th class="text-right">${esc(__("Tax"))}</th>
          <th class="text-right">${esc(__("Landed"))}</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
