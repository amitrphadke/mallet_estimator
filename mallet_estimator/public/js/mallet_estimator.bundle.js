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
      if (!frappe.session || frappe.session.user === "Guest") return;
      // The desk navbar is a flex row whose RIGHT-HAND <ul class="navbar-nav">
      // holds the items. Appending a bare span to the CONTAINER put the badge
      // outside that row, where it never showed; insert a real nav item.
      const host = document.querySelector(".navbar .navbar-nav")
        || document.querySelector("header .navbar-nav")
        || document.querySelector(".navbar .container")
        || document.querySelector("header.navbar")
        || document.querySelector(".navbar");
      if (!host) return;
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
          // No commit means the built image kept no git tree — say so plainly
          // instead of showing a version that looks like it might be a commit.
          el.textContent = v.commit ? "MEst @ " + v.commit : "MEst v" + (v.version || "?") + " (no commit)";
          el.title = "mallet_estimator v" + (v.version || "?") +
            (v.branch ? " · " + v.branch : "") + (v.commit ? " · " + v.commit : "") +
            " · source: " + (v.source || "?");
          el.style.cssText =
            "font-size:11px;opacity:.7;white-space:nowrap;letter-spacing:.2px;";
          if (host.tagName === "UL") {
            const li = document.createElement("li");
            li.className = "nav-item";
            li.style.cssText = "display:flex;align-items:center;margin-right:10px;";
            li.appendChild(el);
            host.insertBefore(li, host.firstChild);
          } else {
            el.style.cssText += "margin-left:8px;align-self:center;";
            host.appendChild(el);
          }
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

// --- Wide-screen layout for the estimating forms ---------------------------
// The desk centres every page in a narrow column, which is right for a
// two-field settings form and wrong for an estimate: the tables that matter
// here (materials, SKUs, décor, labour) end up scrolling sideways inside a
// half-empty screen. On a large monitor we take the whole width — tables get
// all of it, and single fields pack into three columns instead of one long
// ribbon of white space. Guarded at 1400px so laptops keep the stock layout,
// and scoped to our own forms so nothing else in the desk shifts.
(function () {
  const WIDE_DOCTYPES = ["Estimate", "Estimate SKU", "Estimate Settings"];
  const CSS = `
@media (min-width: 1400px) {
  .mallet-wide .container,
  .mallet-wide .page-body .container { max-width: none; }
  .mallet-wide .layout-main-section-wrapper { max-width: none; }
  /* tables take every pixel they can, so no sideways scrolling */
  .mallet-wide .form-grid-container,
  .mallet-wide .form-grid,
  .mallet-wide .grid-body { width: 100%; }
  .mallet-wide .grid-static-col { white-space: normal; }
  /* a section whose fields sit in ONE column packs into three */
  .mallet-wide .form-section > .section-body > .form-column:only-child > form {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    column-gap: 18px;
  }
  .mallet-wide .form-section > .section-body > .form-column:only-child > form
    > .frappe-control[data-fieldtype="Table"],
  .mallet-wide .form-section > .section-body > .form-column:only-child > form
    > .frappe-control[data-fieldtype="HTML"],
  .mallet-wide .form-section > .section-body > .form-column:only-child > form
    > .frappe-control[data-fieldtype="Text Editor"],
  .mallet-wide .form-section > .section-body > .form-column:only-child > form
    > .frappe-control[data-fieldtype="Code"],
  .mallet-wide .form-section > .section-body > .form-column:only-child > form
    > .form-section-heading { grid-column: 1 / -1; }
}`;

  function style() {
    if (document.getElementById("mallet-wide-css")) return;
    const el = document.createElement("style");
    el.id = "mallet-wide-css";
    el.textContent = CSS;
    document.head.appendChild(el);
  }

  function mark() {
    try {
      style();
      const wrap = document.querySelector(".page-container:not(.hide), .page-container");
      if (!wrap) return;
      const dt = frappe.get_route && frappe.get_route()[1];
      wrap.classList.toggle("mallet-wide", WIDE_DOCTYPES.indexOf(dt) !== -1);
    } catch (e) {
      // layout is a nicety; never let it break the desk
      // eslint-disable-next-line no-console
      console.warn("mallet_estimator wide layout:", e);
    }
  }

  $(document).ready(mark);
  $(document).on("startup", mark);
  if (frappe.router && frappe.router.on) frappe.router.on("change", mark);
})();
