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
   ₹/hr made up of **space rent + machine depreciation + the 2-person crew wage**. This is what
   each process step is charged at.
4. Click **"Create / refresh manufacturing masters"** once. This creates your 7 **Workstations**,
   17 **Operations**, a **Routing**, the standard **Rooms**, and the print format. The popup
   confirms what was created.

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
9. Create an **Estimate** (the doctype), pick the **Project**. It **automatically lists every
   SKU** of that project and totals them — no manual adding, no duplicates.
10. Click **Create Quotation** → an ERPNext **Quotation** is created for the customer, one line
    per article at its client price, linked to the Project.
11. **Print** the Estimate with the **"Mallet Client Estimate"** format → a clean PDF grouped
    by room, with each article's image, description, size, material amounts and totals
    (overhead is folded into "Design & execution" — the client never sees your factory costs).

### On approval → manufacture (ERPNext-native)
12. Convert the **Quotation → Sales Order** (standard ERPNext button).
13. On the Estimate, click **Build BOMs** → a **BOM** per article (materials + operations).
14. From the **Sales Order → Create → Work Order** → ERPNext creates **Work Orders** and
    **Job Cards** for each process step. The shop floor works/scans the job cards (what to do
    now/next).
15. Job Card time + material issued + purchases post against the **Project**; invoices too.

### See your real margin
16. Open the **Project Margin** report. Per project it shows **Estimated** cost/price/margin vs
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
- **Rates** live in Estimate Settings (not on individual Workstations, because ERPNext v16
  reorganised the Workstation rate fields). The Cost Calculator shows the resulting ₹/hr.
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
