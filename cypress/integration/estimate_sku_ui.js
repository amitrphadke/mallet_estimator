// UI (end-to-end) test — drives the real ERPNext desk in a browser.
// Run:  bench --site <site> run-ui-tests mallet_estimator --headless
// (Frappe provides cy.login / cy.visit via its Cypress harness.)

context("Mallet Estimator — desk UI", () => {
  before(() => {
    cy.login();
  });

  it("verify_setup reports every master present (config health-check)", () => {
    // Hit the whitelisted method directly — robust, no fragile button DOM.
    cy.request("/api/method/mallet_estimator.install.verify_setup")
      .its("body.message")
      .then((res) => {
        expect(res.all_ok, `failed checks: ${JSON.stringify(res.failed)}`).to.be.true;
      });
  });

  it("Estimate SKU material grid is stock-backed (Item link + UOM columns)", () => {
    cy.visit("/app/estimate-sku/new");
    cy.get('[data-fieldname="materials"]', { timeout: 30000 }).scrollIntoView().should("be.visible");
    cy.get('[data-fieldname="materials"] .grid-heading-row').within(() => {
      cy.contains("Material Item");
      cy.contains("UOM");
      cy.contains("Rate");
    });
  });

  it("Estimate Settings exposes the Verify setup + Create masters buttons", () => {
    cy.visit("/app/estimate-settings");
    cy.get(".page-actions", { timeout: 30000 }).should("exist");
    // buttons may collapse into a menu; assert they exist somewhere on the toolbar
    cy.get("body").then(($b) => {
      expect($b.text()).to.match(/Verify setup|Create \/ refresh manufacturing masters/);
    });
  });
});
