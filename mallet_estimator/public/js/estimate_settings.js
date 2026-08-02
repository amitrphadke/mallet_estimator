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
        const inv = m.inventory || {}, wh = m.warehouses || {};
        let body = __("Workstations created: {0}<br>Operations created: {1}<br>Routing created: {2}<br>Workspace present: {3}", [
          m.workstations || 0, m.operations || 0, m.routing || 0, m.workspace_exists ? "yes ✓" : "NO",
        ]);
        body += __("<br>Item Groups created: {0} · UOMs: {1} · Item fields: {2}<br>Warehouses created: {3}", [
          inv.item_groups || 0, inv.uoms || 0, inv.custom_fields || 0, wh.warehouses || 0,
        ]);
        const allErr = [].concat(m.errors || [], inv.errors || [], wh.errors || []);
        if (allErr.length) { m.errors = allErr; }
        if (m.errors && m.errors.length) {
          body += "<br><br><b>" + __("Errors") + ":</b><br>" + frappe.utils.escape_html(m.errors.join("\n")).replace(/\n/g, "<br>");
        }
        frappe.msgprint({ title: __("Manufacturing setup"), message: body, indicator: (m.errors && m.errors.length) ? "orange" : "green" });
        render_calculator(frm);
      });
    });

    frm.add_custom_button(__("Verify setup"), () => {
      frappe.call({ method: "mallet_estimator.install.verify_setup", freeze: true }).then((r) => {
        const d = (r && r.message) || { checks: [] };
        const rows = (d.checks || []).map((c) =>
          `<tr><td>${c.ok ? "✅" : "❌"}</td><td>${frappe.utils.escape_html(c.name)}</td>` +
          `<td class="text-muted">${frappe.utils.escape_html(c.detail || "")}</td></tr>`
        ).join("");
        frappe.msgprint({
          title: d.all_ok ? __("Setup verified ✓") : __("Setup incomplete"),
          indicator: d.all_ok ? "green" : "red",
          message: `<table class="table table-bordered" style="font-size:12.5px;margin:0"><tbody>${rows}</tbody></table>` +
            (d.all_ok ? "" : `<p class="text-muted" style="margin-top:8px">Run <b>Create / refresh manufacturing masters</b> to fix the ❌ rows.</p>`),
        });
      });
    });
    render_calculator(frm);
  },
  carpenter_salary: (frm) => render_calculator(frm),
  helper_salary: (frm) => render_calculator(frm),
  designer_salary: (frm) => render_calculator(frm),
  bonus_months: (frm) => render_calculator(frm),
  paid_holidays_per_month: (frm) => render_calculator(frm),
  national_holidays_per_year: (frm) => render_calculator(frm),
  lunch_hours_per_day: (frm) => render_calculator(frm),
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
      const sr = d.staff_rates || {};
      const pct = (a) => d.factory_area ? ((a / d.factory_area) * 100).toFixed(1) + "%" : "—";
      const rows = d.rows.map((w) => `
        <tr>
          <td>${frappe.utils.escape_html(w.name)}<br>
            <span class="text-muted" style="font-size:11px">${w.dims && w.dims[0] ? `${w.dims[0]}×${w.dims[1]} ft · ${w.area_sqft} sq ft · ${pct(w.area_sqft)}` : "no footprint"}</span></td>
          <td class="text-right">${money(w.rent_hr)}</td>
          <td class="text-right">${money(w.machine_hr || 0)}</td>
          <td class="text-right">${money(w.wages_hr != null ? w.wages_hr : w.labour_hr)}<br>
            <span class="text-muted" style="font-size:11px">${(w.crew || []).join(" + ")}</span></td>
          <td class="text-right">${money(w.elec_hr || 0)}</td>
          <td class="text-right">${money(w.consumable_hr || 0)}</td>
          <td class="text-right"><b>${money(w.net_hr != null ? w.net_hr : w.total_hr)}</b></td>
        </tr>`).join("");
      wrap.html(`
        <div style="font-size:12.5px">
          <p class="text-muted" style="margin-bottom:8px">
            These seed each ERPNext <b>Workstation</b>'s <b>Operating Components Cost</b> (Net Hour Rate) — what every process step is charged.
            <b>Rent</b> = pure space rent (${money(d.monthly_rent)}/mo over ${d.billable_area} billable sq ft) prorated by footprint over
            <b>${(d.working_hours_per_month || 0).toFixed(0)} productive hrs/mo</b>
            (${(d.working_days_per_month || 0).toFixed(1)} working days × ${(d.productive_hours_per_day || 0).toFixed(1)} hrs after lunch).
            <b>Depreciation</b> = machine capital straight-line, its own component.
            <b>Wages</b> = salary-derived per role: carpenter ${money(sr.carpenter)}/hr · helper ${money(sr.helper)}/hr · designer ${money(sr.designer)}/hr
            (salary × 13 ÷ 12 ÷ productive hrs — includes the Diwali bonus and paid holidays).
            <b>Electricity</b> and <b>Consumables</b> are separate per-workstation components.
            Once seeded, edit any cell on the Workstation and the estimator reads the live rate.
          </p>
          <table class="table table-bordered" style="margin:0">
            <thead><tr>
              <th>Workstation (footprint)</th>
              <th class="text-right">Rent ₹/hr</th>
              <th class="text-right">Depreciation ₹/hr</th>
              <th class="text-right">Wages ₹/hr</th>
              <th class="text-right">Electricity ₹/hr</th>
              <th class="text-right">Consumables ₹/hr</th>
              <th class="text-right">Net ₹/hr</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
          <p class="text-muted" style="margin-top:6px">
            Factory ${d.factory_area} sq ft → workstations occupy ${d.billable_area} sq ft;
            <b>${d.free_area} sq ft free</b> (${pct(d.free_area)}) for you to consume.
            Rent rows recover ${money(d.rent_recovered_month)}/month = 100% of rent.
            Transport trips: tempo ${money((d.transport_rates || {}).tempo)} · ext-laminate ${money((d.transport_rates || {}).ext_lam)}
            · client-hw ${money((d.transport_rates || {}).client_hw)} · outward ${money((d.transport_rates || {}).outward)}.
          </p>
        </div>`);
    });
}
