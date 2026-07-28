# Mallet Estimator — ERPNext app

Turns your SketchUp + OpenCutList design into a **priced estimate**, a client **quotation**,
and (on approval) an ERPNext **manufacturing + project** job — all inside your ERPNext site.
It reproduces your factory's cost model (material + labour + machinery + factory-space rent +
design) and gives you an **estimate-vs-actual margin** report at the end of each project.

> **Status:** running on staging (`mcft-stg`). Author-built, tested through the estimate +
> quotation flow; the Work Order / Job Card execution chain is being finished. Install on a
> staging site first (see "Deploy" at the bottom).

---

## How you use it (end-to-end, plain language)

### One-time setup
1. Open **Estimate Settings** (search with ⌘K / Ctrl+K).
2. Fill your rates: **Carpenter ₹/hr, Helper ₹/hr, Design ₹/hr**, **Monthly Rent**, working
   days/hours, sheet size (2440×1220), wastage %.
3. Look at the **Workstation Cost Calculator** on that page — it shows, per workstation, the
   **Net Hour Rate** each process step is charged at.
4. Click **"Create / refresh manufacturing masters"** once. This creates your 7 **Workstations**,
   17 **Operations**, a **Routing**, the standard **Rooms**, and the print format. The popup
   confirms what was created.

**Costing is workstation-based.** Each process step is priced at its **Workstation's Net Hour
Rate** — the native ERPNext *Operating Components Cost* table on the Workstation
(Manufacturing → Workstation → Operating Costs). The components are:

| Workstation | Rent | Wages (crew) | Machinery (dep.) | Electricity | Consumables | **Net ₹/hr** |
|---|--:|--:|--:|--:|--:|--:|
| Panel Saw | 200* | 264 | 10 | 50 | 50 | **564*** |
| Edge Bander | 18 | 264 | 5 | 40 | 60 | 387 |
| Drill Press | 14 | 264 | 2 | 20 | 20 | 320 |
| Pasting Station | 27 | 264 | 0 | 10 | 40 | 341 |
| Assembly Station | 60 | 264 | 1 | 10 | 20 | 355 |
| Project Room | 60 | 264 | 0 | 20 | 10 | 354 |
| On-Site | 0 | 264 | 0 | 0 | 20 | 284 |

\* Panel Saw shows the values you set by hand; the installer preserves any workstation you've
already configured and only seeds the rest. **Wages (the 2-person crew, carpenter ₹157 + helper
₹107) are folded into the workstation rate** — there is no separate carpenter/helper charge on
the step. Edit any component right on the Workstation and the Net Hour Rate re-sums; the
estimator reads that live rate. A step's cost = its Workstation Net Hour Rate × (Qty × Min/Unit ÷ 60).

### Per project
5. Create an ERPNext **Project** for the customer (Projects → New). Everything hangs off this.

### Per article (SKU)
6. In SketchUp/OpenCutList, export the article's **Estimate PDF** and its **Parts CSV**
   (`..._Loft.csv`). Optionally save a **rendered image** of the article.
7. In ERPNext, create a new **Estimate SKU**:
   - Pick the **Project** (required) and **Room** (required — dropdown; add new rooms as
     master data, or pick **Other**). Customer is filled from the Project.
   - Type the **Article name** (e.g. "Wardrobe"). A code like `YS_MB_WAR` is generated
     (customer initials + room + article) — every article is tagged to its customer.
   - Enter the **outer size**, a **description**, and attach the **Article image** (it prints
     on the client estimate).
   - In the **Material** section, attach the **Estimate PDF** and the **Parts CSV**, then
     **Save**.
8. On Save, the app automatically:
   - reads the **accurate sheet/hardware quantities** from the Estimate PDF,
   - creates/links an ERPNext **Item** per material (your rate card — set the prices once),
   - fills the **17 process steps** with quantities derived from the design (sheets, minifix,
     hinges, drawer rails, etc.) and each step's **Workstation** and **Phase Cost**,
   - stores the **parts list** (with the QR part numbers from your labels) for the shop floor,
   - computes **Material + Labour + Machine + Rent + Design = internal cost**, then the
     **client price** using your markups.
   - The first four steps (Lamination / Tape / Cutting / Edge Banding) are **calculated and
     locked**; the rest you can fine-tune (minutes per unit, workstation, quantity).

### Quote the project
9. Create an **Estimate** (the doctype), pick the **Project**. While it is a **Draft** it
   **automatically lists every SKU** of that project and totals them — no manual adding, no
   duplicates. Add another SKU later and it is pulled in automatically; the **Refresh SKUs**
   button re-pulls on demand.
10. **Approve = Submit.** When the estimate is right, click **Submit**. This is the approval /
    lock point: the SKU list and totals are **frozen** as the baseline and can no longer be
    edited. (Only submitted estimates should be quoted.)
11. On the submitted estimate, click **Create Quotation** → an ERPNext **Quotation** is created
    for the customer, one line per article at its client price, linked to the Project.
12. **Print** the Estimate with the **"Mallet Client Estimate"** format → a clean PDF grouped
    by room, with each article's image, description, size, material amounts and totals
    (overhead is folded into "Design & execution" — the client never sees your factory costs).

### If the article changes during execution (before handover)
Do **not** edit the approved estimate in place — that would destroy the baseline the
**Project Margin** report compares against. Use ERPNext's native **Amend**:
- Open the approved estimate → **Cancel** → **Amend**. This creates a linked new version
  (`MEST-EST-2026-0001-1`, tracked via *Amended From*) while the original stays as the frozen
  baseline / audit trail.
- Edit the amended draft (or its SKUs), **Submit** it, then **Create Quotation** again (or revise
  the existing Quotation / Sales Order). The amend chain records exactly what changed and when.

So the rule is: **approved = frozen; a scope change = a new amended version**, never an in-place edit.

### On approval → manufacture (ERPNext-native)
13. Convert the **Quotation → Sales Order** (standard ERPNext button).
14. On the Estimate, click **Build BOMs** → a **BOM** per article (materials + operations).
15. From the **Sales Order → Create → Work Order** → ERPNext creates **Work Orders** and
    **Job Cards** for each process step. The shop floor works/scans the job cards (what to do
    now/next).
16. Job Card time + material issued + purchases post against the **Project**; invoices too.

### See your real margin
17. Open the **Project Margin** report. Per project it shows **Estimated** cost/price/margin vs
    **Actual** labour + material + billed, and the **Margin Variance** — so you see exactly
    where a custom job ate into the margin.

---

## What ERPNext data this creates (so nothing is a black box)

| You do | ERPNext gets |
|---|---|
| Fill Estimate Settings → Create masters | **Workstations**, **Operations**, **Routing**, **Rooms**, print format |
| Save an Estimate SKU with the PDF | one **Item** per material (rate card) + one **Item** for the article |
| Create an Estimate + Create Quotation | a **Quotation** for the customer, linked to the Project |
| Build BOMs | a **BOM** per article |
| Sales Order → Create Work Order | **Work Orders** + **Job Cards** |
| Shop floor + purchases + invoices | costs & revenue on the **Project** → **Project Margin** report |

Everything is standard ERPNext underneath — so Sales, Purchase, Inventory, Manufacturing,
Projects and Accounting all see the same data.

---

## Notes
- **No buttons on the SKU** — the import runs on Save. Just attach the two files.
- **Rooms** are a master (Estimate Room). Add/rename them there; "Other" is included.
- **Rates** live natively on each **Workstation** (Manufacturing → Workstation → *Operating
  Components Cost*: Rent + Wages + Machinery + Electricity + Consumables → **Net Hour Rate**).
  Estimate Settings holds the inputs used to *seed* those (crew wage, rent, footprints, machine
  capital); once seeded you tune each workstation directly and the estimator reads the live rate.
- **Approval:** an Estimate is **submittable** — Draft (editable, auto-pulls SKUs) → **Submit**
  (approved, frozen baseline) → Create Quotation. Post-approval changes go through **Amend**.
- The standalone React prototype is in `../SKU_Estimator` (same cost model, offline) — this
  Frappe app is the ERPNext-integrated version.

## Deploy (Frappe Cloud custom app)
1. Push this repo to GitHub.
2. Frappe Cloud → your **Bench group → Apps → Add App** (GitHub, branch `main`) → **Deploy**.
3. Site → **Install App** → `mallet_estimator`.
4. Open **Estimate Settings** → **Create / refresh manufacturing masters**.
5. After any code update: push → **Deploy** → hard-refresh the browser (⌘⇧R) to load new JS.

### Auto-deploy on `git push` (optional)
By default `git push` only updates GitHub; you then click **Deploy** in Frappe Cloud. To make a
push deploy **staging** automatically:
1. **One-time (Frappe Cloud dashboard):** open the **staging bench group** → **Tags** → add the
   tag **`auto-deploy`**. (Leave the **production** bench group *without* this tag so prod is
   never auto-deployed.)
2. **Per push:** include the marker **`press-deploy`** in the commit message. Frappe Cloud then
   creates and deploys a new build on every tagged bench where the app is installed. To target
   only one bench, use `press-deploy-bench-<bench-id>`.

So: tag staging once, put `press-deploy` in your commits, and each `git push` auto-deploys
staging — while production stays a deliberate manual **Deploy**.

### Fully hands-off via GitHub Actions (recommended for active dev)
`.github/workflows/deploy-staging.yml` triggers a Frappe Cloud deploy on every push to `main`,
so you don't click anything and Frappe Cloud emails you the result. One-time setup:
1. In **Frappe Cloud → your account/team → API Access**, generate an **API key + secret**.
2. In **GitHub → repo → Settings → Secrets and variables → Actions**, add secrets
   `FRAPPE_CLOUD_API_KEY` and `FRAPPE_CLOUD_API_SECRET`. Optionally add variables `FC_HOST`
   (default `frappecloud.com`) and `FC_BENCH` (default `bench-44687`, your staging bench id).
3. Push → the workflow triggers the deploy; Frappe Cloud builds and emails the outcome.

If the workflow errors with a bad method name, the `press.api` endpoint in the workflow may
differ for your FC version — adjust that one line.
