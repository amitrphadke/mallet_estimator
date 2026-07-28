// Estimate SKU: no buttons. Material + operation quantities import automatically
// on Save when the OpenCutList Estimate PDF is attached in the Material section.
// The computed qty cells (steps 1-4) are locked in the grid.
const LOCKED_PHASES = ["Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding"];

frappe.ui.form.on("Estimate SKU", {
  refresh: (frm) => setTimeout(() => lock_qty(frm), 300),
});

frappe.ui.form.on("Estimate Labor", {
  phase: (frm) => lock_qty(frm),
  labor_add: (frm) => lock_qty(frm),
});

function lock_qty(frm) {
  const grid = frm.fields_dict.labor && frm.fields_dict.labor.grid;
  if (!grid || !grid.grid_rows_by_docname) return;
  (frm.doc.labor || []).forEach((row) => {
    const gr = grid.grid_rows_by_docname[row.name];
    if (gr && gr.toggle_editable) gr.toggle_editable("qty", !LOCKED_PHASES.includes(row.phase));
  });
}
