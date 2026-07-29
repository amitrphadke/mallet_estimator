// UI (end-to-end) test — drives the real ERPNext desk in a browser against a
// live bench. Run in CI via `cypress run` (see .github/workflows/ui-tests.yml).

describe("Mallet Estimator — desk UI", () => {
  beforeEach(() => {
    cy.login();
  });

  it("verify_setup reports every master present (config health-check)", () => {
    cy.request("/api/method/mallet_estimator.install.verify_setup")
      .its("body.message")
      .then((res) => {
        expect(res.all_ok, `failed checks: ${JSON.stringify(res.failed)}`).to.be.true;
      });
  });

  it("Estimate SKU material grid is stock-backed (Item link + UOM columns)", () => {
    cy.visit("/app/estimate-sku/new");
    cy.get('[data-fieldname="materials"]', { timeout: ### }).scrollIntoView().should("be.visible");
    cy.get('[data-fieldname="materials"] .grid-heading-row').within(() => {
      cy.contains("Material Item");
      cy.contains("UOM");
      cy.contains("Rate");
    });
  });

  it("Estimate Settings exposes the Verify setup / Create masters buttons", () => {
    cy.visit("/app/estimate-settings");
    cy.get(".page-actions", { timeout: ### }).should("exist");
    cy.get("body").should(($b) => {
      expect($b.text()).to.match(/Verify setup|Create \/ refresh manufacturing masters/);
    });
  });
});
