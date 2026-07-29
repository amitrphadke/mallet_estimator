// Minimal support file — a self-contained cy.login() that authenticates against
// the running bench via the login API (no dependency on Frappe's cypress commands).
Cypress.Commands.add("login", (usr = "Administrator", pwd = "admin") => {
  return cy.request({
    method: "POST",
    url: "/api/method/login",
    body: { usr, pwd },
    failOnStatusCode: true,
  });
});
