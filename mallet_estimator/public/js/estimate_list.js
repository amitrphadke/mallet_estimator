// Estimate list: the mode is the first thing you need to know about an estimate
// — CSV-Nest estimates nest all their SKUs together (shared-material saving,
// valid only if the set is ordered together), OCL-PDF estimates price each
// article standalone. Reading it used to mean opening the doc, so the row
// indicator now carries BOTH the approval state and the mode, and the mode is
// a sidebar filter as well.
frappe.listview_settings["Estimate"] = {
  add_fields: ["estimation_mode", "work_scope"],

  get_indicator(doc) {
    // Work scope leads: new work and repair are different businesses (an
    // article you build vs a visit you make), and estimation mode is
    // meaningless on a pure-repair estimate. Mode follows only when it says
    // something — a New + Repair estimate names both.
    const scope = doc.work_scope || "";
    const mode =
      doc.estimation_mode === "CSV-Nest"
        ? __("CSV-Nest")
        : doc.estimation_mode
        ? __("OCL PDF")
        : scope
        ? ""
        : __("no SKUs");
    const what = [scope, mode].filter(Boolean).join(" · ") || __("no SKUs");
    // Colour stays the ERPNext docstatus convention (people scan for it);
    // the mode rides in the label so one glance answers both questions.
    if (doc.docstatus === 2) {
      return [__("Cancelled") + " · " + what, "red", "docstatus,=,2"];
    }
    if (doc.docstatus === 1) {
      return [__("Approved") + " · " + what, "green", "docstatus,=,1"];
    }
    return [__("Draft") + " · " + what, "orange", "docstatus,=,0"];
  },
};
