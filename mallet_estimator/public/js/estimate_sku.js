// Estimate SKU form: OpenCutList CSV import (server-side aggregation) + helpers.
frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    frm.add_custom_button(__("Import OpenCutList CSV"), () => open_csv_dialog(frm), __("Material"));

    frm.add_custom_button(__("Re-seed standard steps"), () => {
      frappe.confirm(__("Replace the labor rows with the standard 16 + 1 steps?"), () => {
        frm.clear_table("labor");
        frm.refresh_field("labor");
        frm.save(); // server seeds the standard steps when labor is empty
      });
    }, __("Labor"));

    if (frm.doc.item) {
      frm.add_custom_button(__("Open Item"), () => frappe.set_route("Form", "Item", frm.doc.item));
    }
  },
});

function open_csv_dialog(frm) {
  if (frm.is_new()) {
    frappe.msgprint(__("Save the SKU once before importing material."));
    return;
  }
  const d = new frappe.ui.Dialog({
    title: __("Import OpenCutList CSV"),
    fields: [
      { fieldname: "file", fieldtype: "Attach", label: __("Native OpenCutList parts CSV") },
      { fieldname: "hint", fieldtype: "HTML", options: `<div class="text-muted small">${__("Export the parts list from OpenCutList (semicolon CSV). It is aggregated into sheets, hardware, edge banding and laminate; each material is priced from its ERPNext Item rate card.")}</div>` },
      { fieldname: "or", fieldtype: "HTML", options: `<div class="text-muted small" style="margin-top:8px">${__("…or paste the CSV text:")}</div>` },
      { fieldname: "text", fieldtype: "Code", label: __("CSV text") },
    ],
    primary_action_label: __("Import"),
    primary_action(values) {
      const run = (csv_text) => {
        if (!csv_text || !csv_text.trim()) { frappe.msgprint(__("Attach a file or paste CSV text.")); return; }
        frm.call({
          method: "import_opencutlist",
          args: { csv_text },
          freeze: true,
          freeze_message: __("Aggregating parts and pricing from the rate card…"),
        }).then((r) => {
          const m = r && r.message;
          if (!m) return;
          d.hide();
          frm.reload_doc();
          let msg = __("{0} parts → {1} materials. Material cost {2}.", [m.parts, m.materials, format_currency(m.material_cost)]);
          if (m.unpriced) msg += " " + __("{0} material(s) have no rate yet — set their Item price.", [m.unpriced]);
          frappe.show_alert({ message: msg, indicator: m.unpriced ? "orange" : "green" }, 10);
        });
      };
      if (values.text) return run(values.text);
      if (values.file) {
        fetch(values.file).then((res) => res.text()).then(run).catch(() => frappe.msgprint(__("Could not read the file.")));
      } else {
        frappe.msgprint(__("Attach a file or paste CSV text."));
      }
    },
  });
  d.show();
}
