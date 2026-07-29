// Self-contained Cypress config — runs against a live bench (bench start) without
// depending on Frappe's own cypress harness. Invoked in CI as:
//   cd apps/mallet_estimator && npx cypress run
// Exported as a plain object (no require("cypress")) so it resolves even when the
// app dir has no local cypress module (cypress is run via npx).
module.exports = {
  e2e: {
    baseUrl: "http://localhost:8000",
    specPattern: "cypress/e2e/**/*.cy.js",
    supportFile: "cypress/support/e2e.js",
    defaultCommandTimeout: 20000,
    pageLoadTimeout: 60000,
    video: false,
    screenshotOnRunFailure: false,
  },
};
