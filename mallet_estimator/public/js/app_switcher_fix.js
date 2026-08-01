// Make the Mallet Estimator workspace tile on the app switcher.
//
// Frappe's server-side get_workspace_sidebar_items leaves `app` = null for our
// custom desk app's workspace (first-party workspaces get it set), so the switcher
// can't attach the workspace to the app tile. Every underlying source is correct
// (Workspace.app, Module Def app_name, modules.txt, module_app all = mallet_estimator),
// so we simply correct the boot data the switcher reads, before it renders.
frappe.provide("frappe.boot");

(function () {
  const APP = "mallet_estimator";
  const WS = "Mallet Estimator";

  function fix() {
    try {
      const b = frappe.boot;
      if (!b) return;
      // 1) the field the switcher checks first
      const wsi = b.workspace_sidebar_item;
      if (wsi && wsi[WS] && !wsi[WS].app) wsi[WS].app = APP;
      // 2) the module->app fallback (some paths look this up by display name)
      if (b.module_app && !b.module_app[WS]) b.module_app[WS] = APP;
    } catch (e) {
      // never let a UI shortcut break the desk
      // eslint-disable-next-line no-console
      console.warn("mallet_estimator app-switcher fix:", e);
    }
  }

  fix(); // boot is already populated when app_include_js runs
  $(document).on("startup", fix);
  $(document).ready(fix);
  if (frappe.after_ajax) frappe.after_ajax(fix);
})();
