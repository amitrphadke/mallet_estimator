# Testing & configuration verification

The app is tested in **four layers**, cheapest/fastest first. Everything runs
automatically on every push via **`.github/workflows/ci.yml`**; you can run each
layer by hand too.

| Layer | What it proves | Needs a bench? | Where |
|---|---|---|---|
| 1. **Unit** | Cost engine + OpenCutList parsing math | No (pure Python) | `mallet_estimator/tests/test_estimator.py`, `test_opencutlist.py` |
| 2. **Config health-check** | Every master exists & is shaped right | Yes | `verify_setup()` + `tests/test_setup.py` |
| 3. **Integration** | Items/UOMs/warehouses + the Estimate SKU flow | Yes | `tests/test_inventory.py`, `doctype/estimate_sku/test_estimate_sku.py` |
| 4. **UI (E2E)** | The desk actually renders it | Yes + browser | `cypress/integration/estimate_sku_ui.js` |

---

## Layer 1 — Unit tests (run anywhere, no ERPNext)

Pure functions (workstation rates, `calc_sku`, OpenCutList CSV parse/aggregate).
No database, milliseconds to run:

```bash
python -m unittest mallet_estimator.tests.test_estimator mallet_estimator.tests.test_opencutlist -v
```

Add one for any new pure logic — keep it frappe-free so it runs in the fast CI job.

## Layer 2 — Config health-check (`verify_setup`)

`mallet_estimator.install.verify_setup()` asserts that every master the app needs
exists and is correct: Item Groups, UOMs, Item custom fields, Warehouses,
Workstations, Operations, Routing, print format, workspace, and how many materials
are still unpriced. It returns `{checks:[{name, ok, detail}], all_ok, failed}`.

Two ways to run it:
- **In the UI:** Estimate Settings → **Verify setup** → a ✅/❌ table popup. Run it
  after any deploy or "Create / refresh manufacturing masters".
- **In code / CI:** `bench --site <site> execute mallet_estimator.install.verify_setup`
  (and `tests/test_setup.py` asserts `all_ok` after creating the masters).

This is the fastest "is my ERPNext configured right?" check — the same contract is
verified by hand and by CI, so they can't drift.

## Layer 3 — Integration tests (Frappe + ERPNext)

`FrappeTestCase`/`IntegrationTestCase` cases that create real records in a throwaway
test DB and assert behaviour: material Items get the right group/UOM/conversions
(edge banding Meter + Roll = 50 m; plywood Sheet + m² conversion), classification
(`SG_LAM_*` → Laminate), idempotency (no duplicate Items), the Estimate SKU cost
compute, customer-supplied material = free, and the article Item landing in the
**Client SKU** group.

```bash
bench --site <site> run-tests --app mallet_estimator
```

## Layer 4 — UI end-to-end (Cypress)

Drives the real desk in a headless browser — confirms the **Verify setup** popup is
all-green and that the Estimate SKU **Material Lines** grid shows the ERPNext **Item**
link + **UOM** columns (not a plain-text box):

```bash
bench --site <site> run-ui-tests mallet_estimator --headless
```

(Layer 4 needs a running site + Cypress; it's wired for local/optional use. To add it
to CI, start the bench (`bench start`) in the workflow and call `run-ui-tests`.)

---

## CI (`.github/workflows/ci.yml`)

On every push to `main` / PR:
- **unit** job — runs Layer 1 in seconds on plain Python.
- **integration** job — boots MariaDB + Redis, `bench init` (Frappe v16), installs
  **ERPNext** + **mallet_estimator** on a fresh site, and runs Layers 2–3 via
  `bench run-tests --app mallet_estimator`. On failure it dumps the last error logs.

This is separate from **`deploy-staging.yml`** (which builds + updates the Frappe
Cloud site). Tests gate correctness; deploy ships it.

## Adding tests
- New pure helper → add a case to `test_estimator.py`/`test_opencutlist.py`.
- New master/field/warehouse → add it to `verify_setup()` **and** `test_setup.py`.
- New doctype behaviour → a `FrappeTestCase` next to the doctype.
- New screen/field the user must see → a Cypress assertion.
