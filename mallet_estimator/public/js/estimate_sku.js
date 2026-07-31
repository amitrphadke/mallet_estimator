// Estimate SKU: no buttons. Material + operation quantities import automatically
// on Save when the OpenCutList Estimate PDF is attached in the Material section.
// Process Steps are workstation-based: each step's cost = its Workstation's Net
// Hour Rate x (Qty x Min/Unit / 60). The crew wage is inside the workstation rate,
// so there are no carpenter/helper inputs here.
const LOCKED_PHASES = ["Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding"];

frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    setTimeout(() => lock_qty(frm), 300);
    // Re-price Phase Costs at the current Workstation rates when the SKU is
    // opened, so changing a workstation's operating costs is reflected without a
    // manual re-save. Only when the form has no unsaved edits; reloads once if
    // anything changed (then stabilises — no loop).
    if (!frm.is_new() && !frm.is_dirty()) {
      frm.call("recompute").then((r) => {
        if (r && r.message && r.message.changed) frm.reload_doc();
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
  },
});

frappe.ui.form.on("Estimate Labor", {
  phase: (frm) => lock_qty(frm),
  labor_add: (frm) => lock_qty(frm),
  qty: (frm, cdt, cdn) => recompute_total(frm, cdt, cdn),
  carp_min: (frm, cdt, cdn) => recompute_total(frm, cdt, cdn),
});

// Live Total Min = Qty x Min/Unit as you type (Phase Cost is priced on Save from
// the live Workstation rate).
function recompute_total(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  if (!row) return;
  frappe.model.set_value(cdt, cdn, "carp_total", (row.qty || 0) * (row.carp_min || 0));
}

function lock_qty(frm) {
  const grid = frm.fields_dict.labor && frm.fields_dict.labor.grid;
  if (!grid || !grid.grid_rows_by_docname) return;
  (frm.doc.labor || []).forEach((row) => {
    const gr = grid.grid_rows_by_docname[row.name];
    if (gr && gr.toggle_editable) gr.toggle_editable("qty", !LOCKED_PHASES.includes(row.phase));
  });
}
