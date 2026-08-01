frappe.query_reports["Mallet Estimate vs Execution"] = {
  filters: [
    { fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
    { fieldname: "sku", label: __("Estimate SKU"), fieldtype: "Link", options: "Estimate SKU" },
    { fieldname: "only_over", label: __("Only over-estimate (⚠)"), fieldtype: "Check" },
  ],
  formatter(value, row, col, data, def) {
    value = frappe.query_report.default_formatter(value, row, col, data, def);
    if (data && col.fieldname === "variance" && (data.variance || 0) > 0.005) {
      value = `<span style="color:var(--red-600,#c0392b);font-weight:600;">${value}</span>`;
    }
    return value;
  },
};
