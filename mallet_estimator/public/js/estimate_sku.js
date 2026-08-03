// Estimate SKU: no buttons. Material + operation quantities import automatically
// on Save when the OpenCutList Estimate PDF is attached in the Material section.
// Process Steps are workstation-based: each step's cost = its Workstation's Net
// Hour Rate x (Qty x Min/Unit / 60). The crew wage is inside the workstation rate,
// so there are no carpenter/helper inputs here.
const LOCKED_PHASES = ["Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding"];

frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    setTimeout(() => { lock_qty(frm); lock_material_rows(frm); }, 300);
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
  carp_min: (frm, cdt, cdn) => recompute_total(frm, cdt, cdn),
});

// I3: Material + Labor cost totals update INSTANTLY as rows are edited — no save.
// Imported rows are locked (the PDF is the source); MANUAL rows (extra hardware
// like a bed hydraulic lift) are editable and survive re-imports.
frappe.ui.form.on("Estimate Material", {
  materials_add: (frm, cdt, cdn) => {
    frappe.model.set_value(cdt, cdn, "is_manual", 1);
    lock_material_rows(frm);
  },
  item: (frm, cdt, cdn) => {
    const row = locals[cdt][cdn];
    if (!row || !row.item || !row.is_manual) return;
    frm.call("get_landed_rate", { item_code: row.item }).then((r) => {
      const m = (r && r.message) || {};
      if (m.uom) frappe.model.set_value(cdt, cdn, "uom", m.uom);
      frappe.model.set_value(cdt, cdn, "unit_cost", m.rate || 0).then(() => {
        if (!row.qty) frappe.model.set_value(cdt, cdn, "qty", 1);
        recompute_material(frm, cdt, cdn);
      });
    });
  },
  qty: (frm, cdt, cdn) => recompute_material(frm, cdt, cdn),
  unit_cost: (frm, cdt, cdn) => recompute_material(frm, cdt, cdn),
  customer_supplied: (frm, cdt, cdn) => recompute_material(frm, cdt, cdn),
  materials_remove: (frm) => update_live_totals(frm),
});

// Imported rows can't be hand-edited; manual rows can.
function lock_material_rows(frm) {
  const grid = frm.fields_dict.materials && frm.fields_dict.materials.grid;
  if (!grid || !grid.grid_rows_by_docname) return;
  (frm.doc.materials || []).forEach((row) => {
    const gr = grid.grid_rows_by_docname[row.name];
    if (!gr || !gr.toggle_editable) return;
    // rate is NEVER hand-editable (it comes from the price list, history there)
    ["item", "qty", "description"].forEach((f) =>
      gr.toggle_editable(f, !!row.is_manual));
    gr.toggle_editable("unit_cost", false);
  });
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
  const f = frm.get_field("cost_breakup_html");
  if (!f || !f.$wrapper) return;
  let d = null;
  try { d = JSON.parse(frm.doc.cost_breakup || "null"); } catch (e) { d = null; }
  if (!d || !(d.groups || []).length) { f.$wrapper.empty(); return; }
  const money = (v) => format_currency(v || 0);
  const esc = frappe.utils.escape_html;
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
        <tr style="font-weight:700;border-top:2px solid var(--gray-600)"><td>Internal Cost</td><td class="text-right">${money(d.internal)}</td></tr>
        <tr><td>Client: Material</td><td class="text-right">${money(d.client_material)}</td></tr>
        <tr><td>Client: Design &amp; Execution</td><td class="text-right">${money(d.client_design_exec)}</td></tr>
        <tr style="font-weight:700"><td>Client Total</td><td class="text-right">${money(d.client_total)}</td></tr>
      </tbody>
    </table>
    <p class="text-muted" style="font-size:11.5px;margin:6px 0 0">${esc(d.note || "")}</p>`);
}

// include_misc toggle re-prices instantly too.
frappe.ui.form.on("Estimate SKU", {
  include_misc: (frm) => update_live_totals(frm),
});
