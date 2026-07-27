// Estimate SKU form: import material quantities from the OpenCutList Estimate PDF
// (+ parts CSV for the edge-banding part count). The computed qty cells (steps
// 1-4) are locked natively via read_only_depends_on on the child field.
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
