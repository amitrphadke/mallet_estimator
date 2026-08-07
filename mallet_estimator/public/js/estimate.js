// Estimate: draft auto-aggregates a Project's SKUs; Submit = approve & freeze;
// approved estimate -> Create Quotation -> Build BOMs. Changes after approval go
// through Amend (native ERPNext), which keeps the approved baseline intact.
frappe.ui.form.on("Estimate", {
  refresh(frm) {
    const draft = frm.doc.docstatus === 0;
    const approved = frm.doc.docstatus === 1;

    render_estimate_bifurcation(frm);
    if (!frm.is_new()) render_sku_files(frm);

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
      frm.add_custom_button(__("Add SKUs from files…"), () => {
        if (frm.is_dirty()) {
          frappe.msgprint(__("Save the estimate first."));
          return;
        }
        add_skus_from_files_dialog(frm);
      }).addClass("btn-primary");
      frm.add_custom_button(__("Add SKU (CSV-Nest)"), () => {
        if (frm.is_dirty()) {
          frappe.msgprint(__("Save the estimate first."));
          return;
        }
        const d = new frappe.ui.Dialog({
          title: __("New CSV-Nest SKU"),
          fields: [
            { fieldname: "article_name", fieldtype: "Data", label: __("Article name"), reqd: 1,
              description: __("e.g. Wardrobe — the SKU code (YS_MB_WAR) is generated on save") },
            { fieldname: "room", fieldtype: "Link", options: "Estimate Room", label: __("Room"), reqd: 1 },
          ],
          primary_action_label: __("Create & open"),
          primary_action(values) {
            d.hide();
            frm.call("add_csv_nest_sku", values).then((r) => {
              if (r && r.message) {
                frappe.show_alert({
                  message: __("{0} created and added — attach the Part List CSV + 7 Views PDF, Save, then fill the décor map.", [r.message]),
                  indicator: "green",
                });
                frappe.set_route("Form", "Estimate SKU", r.message);
              }
            });
          },
        });
        d.show();
      });
      frm.add_custom_button(__("Add all project SKUs"), () => {
        frm.call("refresh_skus").then((r) => {
          const m = (r && r.message) || {};
          frappe.show_alert({
            message: __("Added {0} SKU(s) · {1} total · {2}", [m.added || 0, m.count || 0, format_currency(m.client || 0)]),
            indicator: "green",
          });
          frm.reload_doc();
        });
      });
      frm.dashboard.add_comment(
        __("Draft — add SKUs from here: <b>Add SKUs from files…</b> takes every Part List CSV (+ matching views PDFs) in one go, one SKU per CSV; the panel under the SKU table shows each SKU's files, sheets and price on one line (📎 to attach/replace without leaving this screen). Every SKU save re-prices this estimate with consolidated nesting, so adding/removing SKUs shows how shared material moves each price. <b>Submit</b> to approve and freeze before quoting."),
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
      $w.empty();
      return;
    }
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
      return `<tr>
        <td><a href="/app/estimate-sku/${encodeURIComponent(row.sku)}">${esc(row.article || row.sku)}</a>
            <div class="small text-muted">${esc(row.code || "")}</div></td>
        <td>${esc(row.room || "")}</td>
        <td>${file_cell(row, "parts_csv", __("Part CSV"))}</td>
        <td>${file_cell(row, "views_pdf", __("Views PDF"))}</td>
        <td class="text-right">${row.sheets ? cstr(Math.round(row.sheets * 100) / 100) : ""}</td>
        <td class="text-right">${row.client_total != null ? format_currency(row.client_total) : ""}${badges}</td>
      </tr>`;
    }).join("");
    $w.html(`
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
    $w.find(".sku-attach").on("click", function () {
      sku_attach_uploader(frm, $(this).data("sku"), $(this).data("field"));
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

// Bulk intake: drop every SKU's Part List CSV (+ matching views PDFs, paired
// by file-name stem) in one go — each CSV becomes a CSV-Nest SKU on this
// estimate, named after its file.
function add_skus_from_files_dialog(frm) {
  const collected = [];
  const d = new frappe.ui.Dialog({
    title: __("Add SKUs from files"),
    fields: [
      { fieldname: "room", fieldtype: "Link", options: "Estimate Room",
        label: __("Room for this batch"), reqd: 1,
        description: __("Applied to every SKU created here — change per SKU later if needed.") },
      { fieldname: "uploader_html", fieldtype: "HTML" },
    ],
    primary_action_label: __("Create SKUs"),
    primary_action(values) {
      if (!collected.length) {
        frappe.msgprint(__("Upload at least one Part List CSV."));
        return;
      }
      d.hide();
      frm.call({
        doc: frm.doc,
        method: "add_skus_from_files",
        args: { files: collected, room: values.room },
        freeze: true,
        freeze_message: __("Nesting & pricing each SKU…"),
      }).then((r) => {
        const rows = (r && r.message) || [];
        const lines = rows.map((x) =>
          `${esc(x.article)} — ${x.sheets ? x.sheets + " " + __("sheets") : __("no sheets?")}` +
          (x.views ? "" : " · " + __("no views PDF matched")) +
          (x.issues ? ` · ${x.issues} ${__("issue(s)")}` : "") +
          (x.unpriced ? ` · <b>${__("unpriced rates!")}</b>` : ""));
        frappe.msgprint({
          title: __("Created {0} SKU(s)", [rows.length]),
          message: lines.join("<br>") || __("Nothing created."),
          indicator: "green",
        });
        frm.reload_doc();
      });
    },
  });
  d.show();
  new frappe.ui.FileUploader({
    wrapper: d.get_field("uploader_html").$wrapper,
    doctype: "Estimate",
    docname: frm.doc.name,
    allow_multiple: true,
    restrictions: { allowed_file_types: [".csv", ".pdf"] },
    on_success(file) {
      collected.push({ file_url: file.file_url, file_name: file.file_name || file.name });
    },
  });
}
