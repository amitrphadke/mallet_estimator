// Estimate SKU form: import material quantities from the OpenCutList Estimate PDF
// + parts CSV attached in the form fields. Computed qty cells (steps 1-4) are
// locked natively via read_only_depends_on on the child field.
frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    frm.add_custom_button(__("Import from Estimate PDF"), () => run_import(frm), __("Material"));

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

function run_import(frm) {
  if (frm.is_new() || frm.is_dirty()) {
    frappe.msgprint(__("Attach the Estimate PDF (and Parts CSV) in the Material section and Save first."));
    return;
  }
  if (!frm.doc.estimate_pdf) {
    frappe.msgprint(__("Attach the OpenCutList Estimate PDF in the Material section first."));
    return;
  }
  frappe.call({
    method: "mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku.import_estimate",
    args: { estimate_sku: frm.doc.name, pdf_file_url: frm.doc.estimate_pdf, csv_file_url: frm.doc.parts_csv || null },
    freeze: true,
    freeze_message: __("Reading estimate PDF and pricing from inventory…"),
  }).then((r) => {
    const m = r && r.message;
    if (!m) return;
    frm.reload_doc();
    let msg = __("{0} materials · part count {1} · material cost {2}", [m.materials, m.part_count, format_currency(m.material_cost)]);
    if (m.unpriced) msg += "<br>" + __("{0} material(s) have no rate yet — set their Item price and re-import.", [m.unpriced]);
    frappe.msgprint({ title: __("Estimate imported"), message: msg, indicator: m.unpriced ? "orange" : "green" });
  });
}
