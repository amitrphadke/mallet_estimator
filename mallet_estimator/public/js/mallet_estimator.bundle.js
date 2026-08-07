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

  // "What is running right now?" — a muted badge in the navbar with the
  // estimator's deployed commit (e.g. "MEst @ ce08c1c"), so the running code
  // is visible at a glance on every desk page. Hover shows version + branch.
  let badge_inflight = false;
  function version_badge() {
    try {
      if (document.getElementById("mallet-version-badge") || badge_inflight) return;
      const navbar = document.querySelector(".navbar .container, header.navbar, .navbar");
      if (!navbar || !frappe.session || frappe.session.user === "Guest") return;
      badge_inflight = true;
      frappe.call({
        method: "mallet_estimator.api.version_info",
        callback(r) {
          badge_inflight = false;
          const v = (r && r.message) || {};
          if (!v.commit && !v.version) return;
          if (document.getElementById("mallet-version-badge")) return;
          const el = document.createElement("span");
          el.id = "mallet-version-badge";
          el.textContent = "MEst @ " + (v.commit || v.version);
          el.title = "mallet_estimator v" + (v.version || "?") +
            (v.branch ? " · " + v.branch : "") + (v.commit ? " · " + v.commit : "");
          el.style.cssText =
            "margin-left:8px;font-size:11px;opacity:.55;align-self:center;white-space:nowrap;";
          navbar.appendChild(el);
        },
        error() {
          badge_inflight = false; // never let a badge break the desk
        },
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("mallet_estimator version badge:", e);
    }
  }
  // The desk navbar renders LATE (same lesson as fix() above): hook every
  // signal fix() uses AND retry on a bounded timer until the badge lands.
  $(document).ready(version_badge);
  $(document).on("startup", version_badge);
  if (frappe.after_ajax) frappe.after_ajax(version_badge);
  let badge_tries = 0;
  const badge_timer = setInterval(() => {
    badge_tries += 1;
    if (document.getElementById("mallet-version-badge") || badge_tries > 20) {
      clearInterval(badge_timer);
    } else {
      version_badge();
    }
  }, 1500);
})();
