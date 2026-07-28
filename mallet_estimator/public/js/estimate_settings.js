// Estimate Settings: manufacturing-masters setup button + workstation cost calculator.
frappe.ui.form.on("Estimate Settings", {
  refresh(frm) {
    frm.add_custom_button(__("Create / refresh manufacturing masters"), () => {
      frappe.call({
        method: "mallet_estimator.install.setup",
        freeze: true,
        freeze_message: __("Creating Workstations, Operations, Routing…"),
      }).then((r) => {
        const m = (r && r.message) || {};
        let body = __("Workstations created: {0}<br>Operations created: {1}<br>Routing created: {2}<br>Workspace present: {3}", [
          m.workstations || 0, m.operations || 0, m.routing || 0, m.workspace_exists ? "yes ✓" : "NO",
        ]);
        if (m.errors && m.errors.length) {
          body += "<br><br><b>" + __("Errors") + ":</b><br>" + frappe.utils.escape_html(m.errors.join("\n")).replace(/\n/g, "<br>");
        }
        frappe.msgprint({ title: __("Manufacturing setup"), message: body, indicator: (m.errors && m.errors.length) ? "orange" : "green" });
        render_calculator(frm);
      });
    });
    render_calculator(frm);
  },
  carpenter_rate: (frm) => render_calculator(frm),
  helper_rate: (frm) => render_calculator(frm),
  monthly_rent: (frm) => render_calculator(frm),
  working_days_per_month: (frm) => render_calculator(frm),
  working_hours_per_day: (frm) => render_calculator(frm),
});

function money(v) { return format_currency(v || 0); }

function render_calculator(frm) {
  frappe.call({ method: "mallet_estimator.mallet_estimator.doctype.estimate_settings.estimate_settings.cost_calculator" })
    .then((r) => {
      const d = r && r.message;
      const wrap = frm.get_field("cost_calculator_html").$wrapper;
      if (!d) { wrap.empty(); return; }
      const rows = d.rows.map((w) => `
        <tr>
          <td>${frappe.utils.escape_html(w.name)}</td>
          <td class="text-right">${money(w.rent_hr)}</td>
          <td class="text-right">${money(w.wages_hr != null ? w.wages_hr : w.labour_hr)}</td>
          <td class="text-right">${money(w.elec_hr || 0)}</td>
          <td class="text-right">${money(w.consumable_hr || 0)}</td>
          <td class="text-right"><b>${money(w.net_hr != null ? w.net_hr : w.total_hr)}</b></td>
        </tr>`).join("");
      wrap.html(`
        <div style="font-size:12.5px">
          <p class="text-muted" style="margin-bottom:8px">
            These seed each ERPNext <b>Workstation</b>'s <b>Operating Components Cost</b> (Net Hour Rate) — what every process step is charged.
            <b>Rent</b> = factory rent (${money(d.monthly_rent)}/mo over ${d.billable_area} billable sq ft) prorated by footprint over ${d.working_hours_per_month} hrs/mo.
            <b>Wages</b> = the 2-person crew (carpenter + helper = ${money(d.crew_rate)}/hr), folded in.
            <b>Electricity</b> + <b>Consumables</b> are per-workstation (machine depreciation is folded into Consumables).
            Once seeded, edit any cell on the Workstation and the estimator reads the live rate.
          </p>
          <table class="table table-bordered" style="margin:0">
            <thead><tr>
              <th>Workstation</th>
              <th class="text-right">Rent ₹/hr</th><th class="text-right">Wages ₹/hr</th>
              <th class="text-right">Electricity ₹/hr</th>
              <th class="text-right">Consumables ₹/hr</th><th class="text-right">Net ₹/hr</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
          <p class="text-muted" style="margin-top:6px">Rent rows recover ${money(d.rent_recovered_month)}/month = 100% of rent.</p>
        </div>`);
    });
}
