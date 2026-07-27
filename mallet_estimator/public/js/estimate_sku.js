// Estimate SKU form: import material quantities from the OpenCutList Estimate PDF
// (+ parts CSV for the edge-banding part count), and lock the computed qty cells.
const LOCKED_OPS = ["Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding"];

frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    frm.add_custom_button(__("Import OpenCutList Estimate"), () => open_estimate_dialog(frm), __("Material"));

    frm.add_custom_button(__("Re-seed standard steps"), () => {
      frappe.confirm(__("Replace the labor rows with the standard 16 + 1 steps?"), () => {
        frm.clear_table("labor");
        frm.refresh_field("labor");
        frm.save();
      });
    }, __("Labor"));

    if (frm.doc.item) {
      frm.add_custom_button(__("Open Item"), () => frappe.set_route("Form", "Item", frm.doc.item));
    }
  },
});

// Lock the Qty cell for the four computed operations.
frappe.ui.form.on("Estimate Labor", {
  form_render(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const gr = frm.fields_dict.labor.grid.grid_rows_by_docname[cdn];
    if (gr && gr.toggle_editable) gr.toggle_editable("qty", !LOCKED_OPS.includes(row.phase));
  },
});

function open_estimate_dialog(frm) {
  if (frm.is_new()) {
    frappe.msgprint(__("Save the SKU once before importing material."));
    return;
  }
  const d = new frappe.ui.Dialog({
    title: __("Import OpenCutList Estimate"),
    fields: [
      { fieldname: "pdf", fieldtype: "Attach", label: __("Estimate PDF (material quantities)"), reqd: 1 },
      { fieldname: "hint", fieldtype: "HTML", options: `<div class="text-muted small">${__("The Estimate PDF gives accurate sheet/hardware counts. Prices come from each material's ERPNext Item (inventory rate).")}</div>` },
      { fieldname: "csv", fieldtype: "Attach", label: __("Parts CSV (for edge-banding part count)") },
    ],
    primary_action_label: __("Import"),
    primary_action(v) {
      if (!v.pdf) { frappe.msgprint(__("Attach the Estimate PDF.")); return; }
      frappe.call({
        method: "mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku.import_estimate",
        args: { estimate_sku: frm.doc.name, pdf_file_url: v.pdf, csv_file_url: v.csv || null },
        freeze: true,
        freeze_message: __("Reading estimate PDF and pricing from inventory…"),
      }).then((r) => {
        const m = r && r.message;
        if (!m) return;
        d.hide();
        frm.reload_doc();
        let msg = __("{0} materials · part count {1} · material cost {2}", [m.materials, m.part_count, format_currency(m.material_cost)]);
        if (m.unpriced) msg += "<br>" + __("{0} material(s) have no rate yet — set their Item price and re-import.", [m.unpriced]);
        frappe.msgprint({ title: __("Estimate imported"), message: msg, indicator: m.unpriced ? "orange" : "green" });
      });
    },
  });
  d.show();
}
