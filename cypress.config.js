// Self-contained Cypress config — runs against a live bench (bench start) without
// depending on Frappe's own cypress harness. Invoked in CI as:
//   cd apps/mallet_estimator && <frappe>/node_modules/.bin/cypress run --browser chrome
const { defineConfig } = require("cypress");

module.exports = defineConfig({
  e2e: {
    baseUrl: "http://localhost:8000",
    specPattern: "cypress/e2e/**/*.cy.js",
    supportFile: "cypress/support/e2e.js",
    defaultCommandTimeout: 20000,
    pageLoadTimeout: 60000,
    video: false,
    screenshotOnRunFailure: false,
    setupNodeEvents() {},
  },
});
