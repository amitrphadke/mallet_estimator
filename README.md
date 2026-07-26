# Mallet Estimator — ERPNext custom app

A Frappe/ERPNext custom app that turns the standalone SKU estimator into a native part of
your ERPNext SaaS install on `mcftpvtltd.m.frappe.cloud`. It estimates a SKU's execution
cost (**material + labor + machinery + rent + design**), links each SKU to an ERPNext
**Item**, rolls SKUs up per **room** into an **Execution Estimate**, and produces an ERPNext
**Quotation** plus a printable client estimate.

> **Not yet run against a live bench.** This app was authored as source and validated at the
> code level (Python compiles, DocType JSON parses, Jinja parses). Install it on a **local or
> staging bench first**, run through the smoke test below, then promote to production.

## What it adds

| DocType | Purpose |
|---|---|
| **Estimate Settings** (Single) | Factory-wide rates, rent, working time, markups, and the machines table (seeded on install). |
| **Estimate SKU** | One article/estimate. Identity + room + outer boundary, Material lines, the 16+1 Labor steps, Design. Computes all costs and **creates/links an ERPNext Item**. |
| **Execution Estimate** (submittable) | One project/customer. Rolls up its SKUs, shows totals, and has **Create Quotation**. |
| Estimate Material / Estimate Labor / Estimate Machine / Execution Estimate SKU | Child tables. |
| **Mallet Client Estimate** (Print Format) | Client-facing document: grouped by room, amounts only, "Design & execution" line, room-wise summary, assumed unit-price schedule. Overhead never shown. |

Cost math lives in `mallet_estimator/estimator.py` and mirrors the React prototype
(`../SKU_Estimator`) exactly — verified: 1610 carpenter-min / 895 helper-min, code `KP_MB_VAN`.

## How data flows with ERPNext

- **Customer** — picked on Estimate SKU / Execution Estimate (native `Customer` link). Drives
  the SKU code (`ClientInitials_Room_Article`, e.g. `KP_MB_VAN`).
- **Item** — each Estimate SKU creates/updates an `Item` (item_code = SKU code, non-stock,
  `standard_rate` = client total) when *Create / update Item on save* is ticked.
- **Quotation** — *Create Quotation* on an Execution Estimate makes a `Quotation`
  (`quotation_to = Customer`), one line per SKU at its client total. Convert it to Sales Order
  / Sales Invoice with ERPNext's normal flow. See
  https://docs.frappe.io/erpnext/quotation.

## Install on a bench (local or staging)

```bash
# from your bench directory, with an ERPNext v14/v15 site
bench get-app mallet_estimator /path/to/mallet_estimator      # or a git URL
bench --site <your-site> install-app mallet_estimator
bench --site <your-site> migrate
bench --site <your-site> clear-cache
```

`after_install` seeds Estimate Settings (rates + 5 machines) and the print format.

## Deploy to Frappe Cloud (custom app plan)

1. **Push this folder to a Git repo** (GitHub, private is fine):
   ```bash
   cd mallet_estimator
   git init && git add -A && git commit -m "Mallet Estimator v0.0.1"
   git branch -M main
   git remote add origin <your-repo-url>
   git push -u origin main
   ```
   Keep the app on the **same major version branch as your bench** (v14 or v15).
2. In the **Frappe Cloud dashboard** → your **Bench group** → **Apps** → **Add App** →
   **From GitHub**, add the repo and branch. (First time you may need to install the Frappe
   Cloud GitHub app on the repo.)
3. **Deploy** the bench group (this builds a new image with the app).
4. On your **site** → **Apps** → **Install** → `mallet_estimator`.
5. Open **Estimate Settings**, confirm rates/rent and the machines, set your markups.

> I can't log into or deploy to your frappe.cloud instance for you — steps 1–4 are yours to
> run (I can guide each command). Everything up to the push is in this repo.

## Smoke test after install

1. **Estimate Settings** — set carpenter/helper/design rates, rent, working days/hours; check
   the 5 machines seeded.
2. **New Estimate SKU** — pick Customer, set Article = "Vanity", Room = "Master Bedroom",
   outer 600×450×720. Save → labor auto-seeds the 16+1 steps and `sku_code` = `KP_MB_VAN`.
3. On the SKU, **Material ▸ Import OpenCutList CSV** (attach or paste) → lines fill in; enter
   carpenter/helper minutes on the labor rows; save → costs compute and an **Item** is created.
4. **New Execution Estimate** — pick the Customer, add the SKU(s) in the table, save (totals
   roll up), **Submit**, then **Create Quotation**.
5. Print the Execution Estimate with the **Mallet Client Estimate** format.

## Updating later

Push changes to the repo, redeploy the bench on Frappe Cloud, then `bench migrate` runs
`after_migrate`, which refreshes the print format from
`templates/print/mallet_client_estimate.html`.

## Relationship to the React app

`../SKU_Estimator` (Vite/React) remains as the standalone prototype and shares the exact cost
model. This Frappe app is the ERPNext-native implementation; use whichever fits — but only the
Frappe app integrates live with Customers, Items and Quotations.
