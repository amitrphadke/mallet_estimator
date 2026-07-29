// UI (end-to-end) test — drives the real ERPNext desk in a browser.
// Run against a running bench:  bench --site <site> run-ui-tests mallet_estimator --headless
// (Frappe provides cy.login / cy.visit / cy.fill_field via its Cypress harness.)

context("Estimate SKU — desk UI", () => {
  before(() => {
    cy.login();
    cy.visit("/app/estimate-settings");
  });

  it("Verify setup reports all masters present", () => {
    cy.visit("/app/estimate-settings");
    cy.get(".custom-actions").contains("Verify setup").click({ force: true });
    // the health-check popup lists a row per check; none should be a ❌
    cy.get(".modal-body", { timeout: 20000 }).should("be.visible");
    cy.get(".modal-body").should("contain", "✅").and("not.contain", "❌");
    cy.hide_dialog && cy.hide_dialog();
  });

  it("New Estimate SKU shows the material grid as ERPNext Items with UOM", () => {
    cy.visit("/app/estimate-sku/new");
    // the Material Lines grid must expose the Item link + UOM columns (stock-backed),
    // not a plain text 'Material' box.
    cy.get('[data-fieldname="materials"]').scrollIntoView().should("be.visible");
    cy.get('[data-fieldname="materials"] .grid-heading-row').within(() => {
      cy.contains("Material Item");
      cy.contains("UOM");
      cy.contains("Rate");
    });
  });
});
