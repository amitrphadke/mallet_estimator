// Estimate list: the mode is the first thing you need to know about an estimate
// — CSV-Nest estimates nest all their SKUs together (shared-material saving,
// valid only if the set is ordered together), OCL-PDF estimates price each
// article standalone. Reading it used to mean opening the doc, so the row
// indicator now carries BOTH the approval state and the mode, and the mode is
// a sidebar filter as well.
frappe.listview_settings["Estimate"] = {
  add_fields: ["estimation_mode"],

  get_indicator(doc) {
    const mode =
      doc.estimation_mode === "CSV-Nest"
        ? __("CSV-Nest")
        : doc.estimation_mode
        ? __("OCL PDF")
        : __("no SKUs");
    // Colour stays the ERPNext docstatus convention (people scan for it);
    // the mode rides in the label so one glance answers both questions.
    if (doc.docstatus === 2) {
      return [__("Cancelled") + " · " + mode, "red", "docstatus,=,2"];
    }
    if (doc.docstatus === 1) {
      return [__("Approved") + " · " + mode, "green", "docstatus,=,1"];
    }
    return [__("Draft") + " · " + mode, "orange", "docstatus,=,0"];
  },
};
