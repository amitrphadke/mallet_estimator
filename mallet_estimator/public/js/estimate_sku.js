// Estimate SKU: no buttons. Material + operation quantities import automatically
// on Save when the OpenCutList Estimate PDF is attached in the Material section.
// Process Steps are workstation-based: each step's cost = its Workstation's Net
// Hour Rate x (Qty x Min/Unit / 60). The crew wage is inside the workstation rate,
// so there are no carpenter/helper inputs here.
// Ops 1-6 + 9: qty is fully computed (import drivers / HWD_* material lines) —
// only Min/Unit stays editable. Extra work = extra reviewable rows.
const LOCKED_PHASES = [
  "Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding",
  "Minifix Boring", "Drilling", "Install Hardware",
];

frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    setTimeout(() => { lock_qty(frm); lock_design_columns(frm); }, 300);
    // I1: cache the live Workstation Net Hour Rates so Phase Cost updates instantly
    // as you edit Qty / Min / Operation — no save needed.
    if (!frm.is_new()) {
      frm.call("workstation_net_rates").then((r) => {
        frm._ws_net = (r && r.message) || {};
      });
    }
    // Re-price Phase Costs at the current Workstation rates when the SKU is
    // opened, so changing a workstation's operating costs is reflected without a
    // manual re-save. Only when the form has no unsaved edits; reloads once if
    // anything changed (then stabilises — no loop).
    if (!frm.is_new() && !frm.is_dirty()) {
      frm.call("recompute").then((r) => {
        if (r && r.message && r.message.changed) frm.reload_doc();
      });
    }
    // Pull every step's Min/Unit + Workstation from its Operation master (after you
    // change an Operation's Std Time). Overwrites per-SKU overrides.
    if (!frm.is_new()) {
      frm.add_custom_button(__("Reset times from Operations"), () => {
        frappe.confirm(
          __("Reset each step's Min/Unit &amp; Workstation to its Operation master values? This overwrites any per-SKU overrides."),
          () =>
            frm.call("reset_step_times").then((r) => {
              if (r && r.message) {
                frappe.show_alert(
                  { message: __("Reset {0} steps from Operation masters", [r.message.steps]), indicator: "green" },
                  5
                );
              }
              frm.reload_doc();
            })
        );
      });
    }
    // Rebuild the material lines from the attached OpenCutList PDF + Parts CSV at
    // the current import logic — no need to detach/re-attach the files.
    if (!frm.is_new() && frm.doc.estimate_pdf) {
      frm.add_custom_button(__("Re-import from files"), () => {
        frappe.confirm(
          __("Rebuild material &amp; hardware lines from the attached PDF/CSV? This replaces the current material lines."),
          () =>
            frm.call("reimport").then((r) => {
              if (r && r.message) {
                frappe.show_alert(
                  { message: __("Re-imported: {0} materials", [r.message.materials]), indicator: "green" },
                  5
                );
              }
              frm.reload_doc();
            })
        );
      });
    }
    render_cost_breakup(frm);
    // Manual extras (e.g. hydraulic lift) — dialog keeps imported rows untouchable.
    if (!frm.is_new()) {
      frm.add_custom_button(__("Add material row"), () => add_material_dialog(frm), __("Materials"));
    }
    // Price backwards from revenue: type the pre-tax price you want (₹ or
    // ₹/sq ft) — margins are back-solved onto THIS SKU as custom margins
    // (material stays put; labor/overhead/design carry the uplift).
    if (!frm.is_new() && !frm.doc.rates_frozen) {
      frm.add_custom_button(__("Price from target"), () => target_price_dialog(frm));
    }
    // Pull the current price-list rate onto every material line — the everyday
    // flow after pricing red-flagged items on the Estimation (Assumed) list.
    // No re-parse of the PDFs; qty and manual rows stay as they are.
    if (!frm.is_new() && !frm.doc.rates_frozen && (frm.doc.materials || []).length) {
      frm.add_custom_button(
        __("Refresh rates from price list"),
        () => {
          const run = () =>
            frm.call("refresh_rates").then((r) => {
              const m = (r && r.message) || {};
              frappe.show_alert(
                {
                  message: m.changed
                    ? __("Updated {0} line rate(s) from the price list", [m.changed])
                    : __("Rates already match the price list"),
                  indicator: m.changed ? "green" : "blue",
                },
                5
              );
              if (m.unpriced) {
                frappe.show_alert(
                  { message: __("Still unpriced: {0}", [m.unpriced]), indicator: "red" },
                  8
                );
              }
              frm.reload_doc();
            });
          frm.is_dirty() ? frm.save().then(run) : run();
        },
        __("Materials")
      );
    }
    // Start over: remove every attached file + all data derived from them.
    if (!frm.is_new()) {
      frm.add_custom_button(__("Remove all files (start over)"), () => {
        frappe.confirm(
          __("Remove ALL attached files and every line derived from them (materials, joinery, parts, execution design)? Steps and identity stay."),
          () =>
            frm.call("reset_files").then(() => {
              frappe.show_alert({ message: __("SKU cleared — attach fresh PDFs to re-import"), indicator: "green" }, 5);
              frm.reload_doc();
            })
        );
      }, __("Files"));
    }
    // V1: seed the execution design (actual materials) from the estimate lines.
    if (!frm.is_new() && (frm.doc.materials || []).length) {
      frm.add_custom_button(__("Build execution design"), () => {
        frappe.confirm(
          __("Seed the execution materials from the estimate (one row per line)? You then swap in the real client-chosen items; variance is tracked."),
          () =>
            frm.call("build_execution_design").then((r) => {
              const m = (r && r.message) || {};
              frappe.show_alert({ message: __("Execution design: {0} line(s)", [m.rows || 0]), indicator: "green" }, 5);
              frm.reload_doc();
            })
        );
      }, __("Execution"));
    }
  },
});

frappe.ui.form.on("Estimate Labor", {
  operation: (frm, cdt, cdn) => {
    lock_qty(frm);
    recompute_total(frm, cdt, cdn);
  },
  workstation: (frm, cdt, cdn) => recompute_total(frm, cdt, cdn),
  labor_add: (frm) => lock_qty(frm),
  labor_remove: (frm) => update_live_totals(frm),
  qty: (frm, cdt, cdn) => recompute_total(frm, cdt, cdn),
  carp_min: (frm, cdt, cdn) => {
    show_min_unit_benefit(frm, cdt, cdn);
    recompute_total(frm, cdt, cdn);
  },
});

// Scale insight: changing Min/Unit vs the Operation master's Std Time shows the
// benefit instantly (e.g. sheet cutting 40 → 30 min when cutting two SKUs'
// sheets in one go): minutes saved x qty, priced at the workstation rate.
function show_min_unit_benefit(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  const std = +(row.std_min || 0);
  const cur = +(row.carp_min || 0);
  const qty = +(row.qty || 0);
  if (!std || !qty || cur === std) return;
  const mins = (std - cur) * qty;
  const rates = frm._ws_net || {};
  const rate = rates[row.workstation] || rates.__default__ || 0;
  const rupees = (mins / 60) * rate;
  const gain = mins > 0;
  frappe.show_alert({
    message: gain
      ? __("{0}: saving {1} min vs Std ({2}/unit) ≈ {3}", [row.operation, Math.round(mins), std, format_currency(rupees)])
      : __("{0}: {1} min MORE than Std ({2}/unit) ≈ {3} extra", [row.operation, Math.round(-mins), std, format_currency(-rupees)]),
    indicator: gain ? "green" : "orange",
  }, 6);
}

// Décor map edits re-point the laminate/edge material lines ON SAVE — remind
// the user so the change doesn't look ignored.
const decor_changed = (frm) => {
  if (frm.__decor_alerted) return;
  frm.__decor_alerted = true;
  frappe.show_alert({
    message: __("Décor map changed — Save to re-point the laminate/edge lines."),
    indicator: "blue",
  }, 5);
};
frappe.ui.form.on("Estimate SKU Decor", {
  slot: decor_changed, domain: decor_changed, brand: decor_changed,
  code: decor_changed, decor_name: decor_changed, short: decor_changed,
  sku_decors_remove: (frm) => decor_changed(frm),
});

// I3: totals update instantly. Imported material rows are FULLY read-only (the
// PDFs are the source); extra hardware goes through the Add-material dialog —
// priced from the stock price list, flagged is_manual so it survives re-imports.
frappe.ui.form.on("Estimate Material", {
  customer_supplied: (frm, cdt, cdn) => recompute_material(frm, cdt, cdn),
  materials_remove: (frm) => update_live_totals(frm),
});

// Profit is a function of revenue: type the price you want, get the margins.
// Material margin stays at its current effective value; the remaining uplift is
// solved as ONE factor across labor/overhead/design and saved as this SKU's
// custom margins.
function target_price_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __("Price this SKU from a target (pre-tax)"),
    fields: [
      { fieldname: "target", fieldtype: "Currency", label: __("Target price (₹, pre-tax)") },
      { fieldname: "per_sqft", fieldtype: "Currency", label: __("… or target ₹ / sq ft (facial area)"),
        description: __("Used only when the ₹ target above is empty; needs outer W/D/H.") },
      { fieldname: "now", fieldtype: "HTML",
        options: `<p class="text-muted" style="font-size:12px">${__("Currently: client total {0} · internal cost {1}",
          [format_currency(frm.doc.client_total || 0), format_currency(frm.doc.internal_cost || 0)])}</p>` },
    ],
    primary_action_label: __("Solve & apply"),
    primary_action(values) {
      d.hide();
      frm.call("apply_target_price", { target: values.target || 0, per_sqft: values.per_sqft || 0 }).then((r) => {
        const m = (r && r.message) || {};
        frappe.msgprint({
          title: __("Priced from target"),
          indicator: m.below_cost ? "red" : "green",
          message: __("Client total {0} · conversion margin {1}% · blended margin {2}% · profit {3}{4}", [
            format_currency(m.client_total || 0), m.conversion_margin_pct, m.blended_margin_pct,
            format_currency(m.profit || 0),
            m.below_cost ? "<br><b style='color:var(--red-600,#c0392b)'>" + __("TARGET IS BELOW INTERNAL COST") + "</b>" : "",
          ]),
        });
        frm.reload_doc();
      });
    },
  });
  d.show();
}

function add_material_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __("Add material row (manual)"),
    fields: [
      { fieldname: "item", fieldtype: "Link", options: "Item", label: __("Item"), reqd: 1,
        description: __("Must exist in stock with a rate on the Estimation (Assumed) price list.") },
      { fieldname: "qty", fieldtype: "Float", label: __("Qty"), default: 1, reqd: 1 },
    ],
    primary_action_label: __("Add"),
    primary_action(values) {
      frm.call("get_landed_rate", { item_code: values.item }).then((r) => {
        const m = (r && r.message) || {};
        const row = frm.add_child("materials", {
          item: values.item, material: values.item, description: values.item,
          qty: values.qty || 1, uom: m.uom || null,
          unit_cost: m.rate || 0, line_cost: (values.qty || 1) * (m.rate || 0),
          is_manual: 1,
        });
        frm.refresh_field("materials");
        update_live_totals(frm);
        if (!(m.rate || 0)) {
          frappe.msgprint({
            message: __("{0} has NO rate on the price list — the line entered at 0. Key its rate on Estimation (Assumed) and re-add.", [values.item]),
            indicator: "red",
          });
        }
        d.hide();
      });
    },
  });
  d.show();
}

function recompute_material(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  if (!row) return;
  const cost = row.customer_supplied ? 0 : (row.qty || 0) * (row.unit_cost || 0);
  frappe.model.set_value(cdt, cdn, "line_cost", cost).then(() => update_live_totals(frm));
}

// I3: EVERY total live — material, joinery, labor, design, internal and the
// client trio — recomputed client-side on each edit. The save stays authoritative
// (client-side folds full phase cost under the labor markup; identical when the
// labor and overhead markups match).
function update_live_totals(frm) {
  const mat = (frm.doc.materials || []).reduce((s, m) => s + (m.line_cost || 0), 0);
  const joi = (frm.doc.joinery_items || []).reduce((s, j) => s + (j.amount || 0), 0);
  const lab = (frm.doc.labor || []).reduce(
    (s, r) => s + ((r.is_misc && !frm.doc.include_misc) ? 0 : (r.op_cost || 0)), 0);
  const des = (frm.doc.design_labor || []).reduce((s, r) => s + (r.op_cost || 0), 0);
  const mk = (frm._ws_net && frm._ws_net.__markups__) || { material: 0, labor: 0, overhead: 0, design: 0 };
  const cm = (mat + joi) * (1 + (mk.material || 0) / 100);
  const cde = lab * (1 + (mk.labor || 0) / 100) + des * (1 + (mk.design || 0) / 100);
  frm.set_value("material_cost", mat);
  if (frm.get_field("joinery_cost")) frm.set_value("joinery_cost", joi);
  frm.set_value("labor_cost", lab);
  frm.set_value("design_cost", des);
  frm.set_value("internal_cost", mat + joi + lab + des);
  frm.set_value("client_material", cm);
  frm.set_value("client_design_exec", cde);
  frm.set_value("client_total", cm + cde);
  // I-days live floor: carpenter minutes / 360 (server adds the helper side on save)
  if (frm.get_field("est_days")) {
    const mins = (frm.doc.labor || []).reduce(
      (s, r) => s + ((r.is_misc && !frm.doc.include_misc) ? 0 : (r.carp_total || 0)), 0);
    frm.set_value("est_days", mins / 360);
  }
  update_live_breakup(frm);  // the summary tables move with the totals
}

// Live Total Min = Qty x Min/Unit, and live Phase Cost = crew-hours x the
// Workstation Net Hour Rate — both update as you type, no save (I1). The save
// still recomputes authoritative values server-side.
function recompute_total(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  if (!row) return;
  const total = (row.qty || 0) * (row.carp_min || 0);
  frappe.model.set_value(cdt, cdn, "carp_total", total);
  const rates = frm._ws_net || {};
  const net = row.workstation && rates[row.workstation] != null ? rates[row.workstation] : rates.__default__;
  if (net != null) {
    frappe.model.set_value(cdt, cdn, "op_cost", (total / 60) * net).then(() => update_live_totals(frm));
  } else {
    update_live_totals(frm);
  }
}

// Design steps: only Qty + Remarks are editable — the pipeline itself (operation,
// workstation, min/unit) is fixed by the Operation masters. Grid-level lock so the
// shared Estimate Labor doctype stays editable on the Process Steps table.
function lock_design_columns(frm) {
  const g = frm.fields_dict.design_labor && frm.fields_dict.design_labor.grid;
  if (!g || !g.update_docfield_property) return;
  ["operation", "workstation", "carp_min", "in_factory", "is_misc", "phase"].forEach((f) => {
    try { g.update_docfield_property(f, "read_only", 1); } catch (e) { /* field absent */ }
  });
}

function lock_qty(frm) {
  const grid = frm.fields_dict.labor && frm.fields_dict.labor.grid;
  if (!grid || !grid.grid_rows_by_docname) return;
  (frm.doc.labor || []).forEach((row) => {
    const gr = grid.grid_rows_by_docname[row.name];
    if (gr && gr.toggle_editable) gr.toggle_editable("qty", !LOCKED_PHASES.includes(row.operation));
  });
}

// C1: render the grouped cost grid (built server-side as JSON on save) — each
// group shows its lines and a bold GROUP TOTAL (Sheet Goods total, Hardware
// total, Labor & Overhead total, …).
function render_cost_breakup(frm) {
  let d = null;
  try { d = JSON.parse(frm.doc.cost_breakup || "null"); } catch (e) { d = null; }
  render_cost_breakup_data(frm, d);
}

// The cost summary must move the moment ANYTHING affecting it moves — margins
// included. Rebuild the client side of the saved breakup from the CURRENT
// effective markups (frm._ws_net.__markups__) + the doc's cost fields, and
// re-render. Costs are untouched by margin edits, so this live view is exact
// for them; the save stays authoritative for everything.
function update_live_breakup(frm) {
  let d = null;
  try { d = JSON.parse(frm.doc.cost_breakup || "null"); } catch (e) { d = null; }
  if (!d) return;
  const mk = (frm._ws_net && frm._ws_net.__markups__) || {};
  const gp = (d.bifurcation && d.bifurcation.gst_pct) || d.gst_pct || 18;
  const a = {
    material: ((frm.doc.material_cost || 0) + (frm.doc.joinery_cost || 0)) * (1 + (mk.material || 0) / 100),
    labor: (frm.doc.labor_cost || 0) * (1 + (mk.labor || 0) / 100),
    design: (frm.doc.design_cost || 0) * (1 + (mk.design || 0) / 100),
    overhead: (frm.doc.overhead_cost || 0) * (1 + (mk.overhead || 0) / 100),
    transport: d.transport || 0,
  };
  const labels = {
    material: "Material (incl. joinery consumables)",
    labor: "Labor (carpentry wages)",
    design: "Design",
    overhead: "Factory Overhead",
    transport: "Transport (shared across SKUs, at cost)",
  };
  const rows = ["material", "labor", "design", "overhead", "transport"].map((k) => ({
    label: labels[k], amount: a[k], gst: a[k] * gp / 100, gross: a[k] * (1 + gp / 100),
  }));
  const pre = rows.reduce((s, r) => s + r.amount, 0);
  rows.forEach((r) => (r.pct = pre ? (r.amount / pre) * 100 : 0));
  const client_total = a.material + a.labor + a.design + a.overhead;
  d.bifurcation = { rows, pre_tax: pre, taxes: pre * gp / 100, grand_total: pre * (1 + gp / 100), gst_pct: gp };
  d.markup_pct = Object.assign({}, mk, { __custom__: !!frm.doc.use_custom_margins });
  d.client_material = a.material;
  d.client_design_exec = a.labor + a.design + a.overhead;
  d.client_total = client_total;
  d.gst_amount = client_total * gp / 100;
  d.client_total_with_gst = client_total * (1 + gp / 100);
  d.profit = client_total + (d.transport || 0) - (d.internal || 0);
  d.margin_pct = client_total ? (d.profit / client_total) * 100 : 0;
  if (d.sqft && d.sqft.sqft) {
    d.sqft.material_per_sqft = a.material / d.sqft.sqft;
    d.sqft.labor_per_sqft = d.client_design_exec / d.sqft.sqft;
    d.sqft.total_per_sqft = client_total / d.sqft.sqft;
  }
  render_cost_breakup_data(frm, d);
}

function render_cost_breakup_data(frm, d) {
  const f = frm.get_field("cost_breakup_html");
  if (!f || !f.$wrapper) return;
  if (!d || !(d.groups || []).length) { f.$wrapper.empty(); return; }
  const money = (v) => format_currency(v || 0);
  const esc = frappe.utils.escape_html;
  // " (+15%)" / " (+80/80/100%)" — effective margins; flagged when this SKU
  // overrides the house policy.
  const mk = (...keys) => {
    const p = d.markup_pct || {};
    const vals = keys.map((k) => +(p[k] || 0));
    const tag = p.__custom__ ? ` · ${__("SKU override")}` : "";
    if (!vals.some((v) => v)) return tag ? ` <span class="text-muted">(${__("SKU override")})</span>` : "";
    const label = vals.every((v) => v === vals[0]) ? `${vals[0]}` : vals.join("/");
    return ` <span class="text-muted">(+${label}%${tag})</span>`;
  };
  let body = "";
  for (const [gname, lines] of d.groups) {
    const shown = lines.filter((r) => r[1]);
    if (!shown.length) continue;
    const gtotal = lines.reduce((s, r) => s + (r[1] || 0), 0);
    body += `<tr style="background:var(--subtle-fg, #f4f5f6);font-weight:700"><td>${esc(gname)} total</td><td class="text-right">${money(gtotal)}</td></tr>`;
    body += shown.map((r) => `<tr><td style="padding-left:24px">${esc(r[0])}</td><td class="text-right">${money(r[1])}</td></tr>`).join("");
  }
  f.$wrapper.html(`
    <table class="table table-bordered" style="font-size:12.5px;margin:0">
      <thead><tr><th>Cost Component</th><th class="text-right">Amount</th></tr></thead>
      <tbody>${body}
        <tr style="font-weight:700;border-top:2px solid var(--gray-600)"><td>Internal Cost — what it costs to MAKE (incl. transport)</td><td class="text-right">${money(d.internal)}</td></tr>
        <tr><td>Client: Material${mk("material")}</td><td class="text-right">${money(d.client_material)}</td></tr>
        <tr><td>Client: Design &amp; Execution${mk("labor", "overhead", "design")}</td><td class="text-right">${money(d.client_design_exec)}</td></tr>
        <tr style="font-weight:700"><td>Client Total (excl. transport &amp; GST)</td><td class="text-right">${money(d.client_total)}</td></tr>
        ${d.transport ? `<tr><td>Transport recovered on the Estimate (at cost)</td><td class="text-right">${money(d.transport)}</td></tr>` : ""}
        ${d.profit != null ? `
        <tr style="font-weight:700;color:${d.profit >= 0 ? "var(--green-600, #16794c)" : "var(--red-600, #c0392b)"}">
          <td>Profit on this SKU (${(d.margin_pct || 0).toFixed(1)}% margin)</td><td class="text-right">${money(d.profit)}</td></tr>` : ""}
        ${d.gst_amount != null ? `
        <tr><td>Output GST ${d.gst_pct || 18}%</td><td class="text-right">${money(d.gst_amount)}</td></tr>
        <tr style="font-weight:700"><td>Client Total incl. GST</td><td class="text-right">${money(d.client_total_with_gst)}</td></tr>` : ""}
      </tbody>
    </table>
    ${render_bifurcation(d.bifurcation, d.sqft)}
    <p class="text-muted" style="font-size:11.5px;margin:6px 0 0">${esc(d.note || "")}</p>`);
}

// The clear Material / Labor / Design / Overhead / Transport / Taxes table —
// amount, % of the pre-tax total, that line's GST, and its gross. Plus the
// facial-sqft rates (two greatest outer dims) when the dims are keyed.
function render_bifurcation(b, sqft) {
  if (!b || !(b.rows || []).length) return "";
  const money = (v) => format_currency(v || 0);
  const esc = frappe.utils.escape_html;
  const rows = b.rows.map((r) => `
      <tr><td>${esc(r.label)}</td>
        <td class="text-right">${money(r.amount)}</td>
        <td class="text-right">${(r.pct || 0).toFixed(1)}%</td>
        <td class="text-right">${money(r.gst)}</td>
        <td class="text-right">${money(r.gross)}</td></tr>`).join("");
  const sq = sqft ? `
      <tr style="background:var(--subtle-fg, #f4f5f6)"><td colspan="5" style="font-weight:700">Per square foot (facial area: ${(sqft.sqft || 0).toFixed(2)} sq ft — two greatest outer dims)</td></tr>
      <tr><td>Material / sq ft</td><td class="text-right">${money(sqft.material_per_sqft)}</td><td colspan="3"></td></tr>
      <tr><td>Labor (design &amp; execution) / sq ft</td><td class="text-right">${money(sqft.labor_per_sqft)}</td><td colspan="3"></td></tr>
      <tr style="font-weight:700"><td>SKU / sq ft (pre-tax)</td><td class="text-right">${money(sqft.total_per_sqft)}</td><td colspan="3"></td></tr>` : `
      <tr><td colspan="5" class="text-muted">Per-sqft rates need the outer W/D/H — key them (or attach the 7 Views PDF).</td></tr>`;
  return `
    <h6 style="margin:14px 0 4px">Bifurcation — client pricing</h6>
    <table class="table table-bordered" style="font-size:12.5px;margin:0">
      <thead><tr><th>Component</th><th class="text-right">Amount</th><th class="text-right">% of pre-tax</th><th class="text-right">GST ${b.gst_pct || 18}%</th><th class="text-right">Incl. GST</th></tr></thead>
      <tbody>${rows}
        <tr style="font-weight:700;border-top:2px solid var(--gray-600)"><td>Total before taxes</td><td class="text-right">${money(b.pre_tax)}</td><td class="text-right">100%</td><td class="text-right">${money(b.taxes)}</td><td class="text-right">${money(b.grand_total)}</td></tr>
        <tr style="font-weight:700"><td>Taxes (GST ${b.gst_pct || 18}%)</td><td class="text-right">${money(b.taxes)}</td><td colspan="3"></td></tr>
        <tr style="font-weight:700"><td>Grand Total incl. GST</td><td class="text-right">${money(b.grand_total)}</td><td colspan="3"></td></tr>
        ${sq}
      </tbody>
    </table>`;
}

// include_misc toggle re-prices instantly too; margin edits refetch the
// effective markups (the server reads the UNSAVED doc) and reprice live.
const refetch_markups = (frm) =>
  frm.call("workstation_net_rates").then((r) => {
    frm._ws_net = (r && r.message) || {};
    update_live_totals(frm);
    update_live_breakup(frm);
  });
// mm stays the stored truth; feet-inches shown alongside for the humans who
// think in inches (nearest half inch).
function mm_to_ftin(mm) {
  mm = +mm || 0;
  if (!mm) return "";
  const totalIn = Math.round((mm / 25.4) * 2) / 2;
  const ft = Math.floor(totalIn / 12);
  const rem = totalIn - ft * 12;
  const whole = Math.floor(rem);
  const inch = `${whole}${rem - whole >= 0.49 ? "½" : ""}″`;
  return ft ? `${ft}′-${inch}` : inch;
}

function show_ftin(frm) {
  const parts = [frm.doc.outer_w, frm.doc.outer_d, frm.doc.outer_h].map(mm_to_ftin).filter(Boolean);
  frm.set_df_property("outer_h", "description",
    parts.length ? __("= {0} (W x D x H, feet-inches)", [parts.join(" x ")]) : "");
}

frappe.ui.form.on("Estimate SKU", {
  refresh: show_ftin,
  include_misc: (frm) => update_live_totals(frm),
  outer_w: show_ftin,
  outer_d: show_ftin,
  outer_h: show_ftin,
  use_custom_margins: refetch_markups,
  margin_material: refetch_markups,
  margin_labor: refetch_markups,
  margin_overhead: refetch_markups,
  margin_design: refetch_markups,
});
