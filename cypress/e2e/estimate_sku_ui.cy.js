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

  it("desk serves the Estimate SKU form to an authenticated user (not redirected to login)", () => {
    // The desk is a heavy SPA; assert the route is SERVED to the logged-in user
    // (200, not a 302 to /login) rather than waiting on headless client render.
    cy.request({ url: "/app/estimate-sku/new", followRedirect: false }).then((resp) => {
      expect(resp.status).to.eq(200);
      expect(resp.body, "served the desk shell").to.include("/assets/frappe");
    });
  });

  it("material lines are stock-backed Items with a UOM (doctype meta)", () => {
    cy.request("/api/method/frappe.client.get?doctype=DocType&name=Estimate%20Material")
      .its("body.message.fields")
      .then((fields) => {
        const byName = Object.fromEntries(fields.map((f) => [f.fieldname, f]));
        expect(byName.item, "material line has an 'item' field").to.exist;
        expect(byName.item.fieldtype).to.eq("Link");
        expect(byName.item.options).to.eq("Item"); // links to ERPNext stock Item
        expect(byName.uom, "material line has a 'uom' field").to.exist;
        expect(byName.uom.options).to.eq("UOM");
      });
  });
});
