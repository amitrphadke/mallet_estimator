# Mallet Estimator — ERPNext app for made-to-order furniture

Turns your SketchUp + OpenCutList design into a **priced estimate**, a client **quotation**, and
(on approval) a full ERPNext **buy → make → deliver → invoice** job — all inside your ERPNext
site, using **native ERPNext objects** (Items, BOMs, Work Orders, Warehouses, Sales/Purchase
cycles) so nothing is reinvented. It reproduces your factory's cost model (material + workstation
time + design) and gives an **estimate-vs-actual margin** per project.

> **Status:** running on staging (`mcft-stg`). Deploy to a staging site first (see "Deploy").

---

## The big picture — how the whole ERP flows

This is a **make-to-order** shop, so everything hangs off a **Project** (one customer job) and the
flow follows the four standard ERP cycles:

```
  DESIGN            ESTIMATE / SALES              PROCUREMENT (Procure-to-Pay)
  SketchUp   ->  Estimate SKU ─┐             ┌─ Material Request ─ Purchase Order ─ Purchase Receipt
  OpenCutList     (per article)│             │        (raw material into the stores)
   PDF + CSV                   ▼             │
                          Estimate ─ Submit ─ Quotation ─ Sales Order ──┤
                          (per project)        (Quote-to-Cash)          │
                                                                        ▼
                                              MANUFACTURING (Make-to-Order)
                                        BOM ─ Work Order ─ Job Cards (16 phases)
                                        raw material consumed  ->  Finished Good
                                                                        │
                                              DELIVER & BILL (Finance)  ▼
                                        Delivery Note ─ Sales Invoice ─ Payment
```

Every cost (material issued, purchases, job-card time) and the revenue (invoice) posts against
the **Project** → the **Project Margin** report shows estimated vs actual.

---

## 1. One-time setup

1. Open **Estimate Settings** (⌘K / Ctrl+K → "Estimate Settings").
2. Fill your rates: **Carpenter ₹/hr, Helper ₹/hr, Design ₹/hr**, **Monthly Rent**, working
   days/hours, sheet size (2440×1220), wastage %.
3. Click **"Create / refresh manufacturing masters"** once. The popup reports what it made:
   - 7 **Workstations**, 17 **Operations**, a **Routing**, standard **Rooms**, the print format;
   - the material **Item Groups**, the **UOMs** (Sheet / Meter / Roll / Square Meter), the Item
     **dimension fields**, and the factory **Warehouses**.
4. Set your **material prices** (see §2) and you're ready.

### Workstation cost (what each process step is charged)
Each step is priced at its **Workstation's Net Hour Rate** — the native ERPNext *Operating
Components Cost* table (Manufacturing → Workstation → Operating Costs). Four standard components
(machine depreciation folded into Consumables):

| Workstation | Rent | Wages (crew) | Electricity | Consumables | **Net ₹/hr** |
|---|--:|--:|--:|--:|--:|
| Panel Saw | 200* | 264 | 50 | 50 | **564*** |
| Edge Bander | 18 | 264 | 40 | 65 | 387 |
| Drill Press | 14 | 264 | 20 | 22 | 320 |
| Pasting Station | 27 | 264 | 10 | 40 | 341 |
| Assembly Station | 60 | 264 | 10 | 21 | 355 |
| Project Room | 60 | 264 | 20 | 10 | 354 |
| On-Site | 0 | 264 | 0 | 20 | 284 |

\* The installer preserves any workstation you configured by hand and seeds the rest. **Wages =
the 2-person crew (carpenter ₹157 + helper ₹107), folded in** — a step has only **Qty** and
**Min/Unit**; there are no carpenter/helper inputs. Edit a component on the Workstation and the
Net Hour Rate re-sums. **Step cost = Net Hour Rate × (Qty × Min/Unit ÷ 60).**

---

## 2. Material inventory (raw material as real stock)

Materials are **native ERPNext stock Items** — the OpenCutList PDF only classifies *which*
material and *how many*; the **cost comes from ERPNext**, never from the PDF.

**Item Groups** (created for you), from the OpenCutList code prefix:

| Code prefix | Example | Item Group | Stocked in | Bought in |
|---|---|---|---|---|
| `SG_` (plywood/MDF) | `SG_PLY_V0_a_a_16mm` | Sheet Goods | **Sheet** (1220×2440) | Sheet |
| `SG_LAM_` / `DL_` | `SG_LAM_V0_12mm_a_a` | Laminate | **Sheet** | Sheet |
| `EB_` | `EB_PVC_IN_a` | Edge Banding | **Meter** | **Roll = 50 m** |
| `HWD_` | `HWD_Hinge` | Hardware | Nos | Nos |
| `SW_` | `SW_Teak` | Solid Wood | Nos | Nos |

- **UOM math is built in:** a plywood **Sheet** carries a `1 Sheet = 2.9768 m²` conversion (1220×2440);
  edge banding is stocked per **Meter** with a `1 Roll = 50 Meter` purchase conversion, so you
  **buy rolls, stock/consume metres**. Hardware is Nos.
- **Created once, never duplicated** — the item_code is the OpenCutList code (+ thickness for
  sheets), so re-importing the same design reuses the same Items.
- Each sheet/laminate Item records **Length / Width / Thickness** (custom fields) and its
  OpenCutList code.
- **Cost source (in order):** moving-average **valuation** (from Purchase Receipts) → **last
  purchase rate** → a **buying Item Price** → the Item's **standard rate**. Anything with **no
  price yet** is flagged on the SKU (a popup + the "Materials Needing a Price" field).
- **Mallet Materials report** (Reports → Mallet Materials): every material Item, its rate, the
  cost source, a **Priced?** flag and **stock qty** — one screen to maintain prices.

### Client-supplied material
Occasionally a client buys plywood/laminate and ships it to you. Tick **"Cust. Supplied"** on that
material line — it stays tracked but is **excluded from the client price**. In stock terms, receive
it with a **Material Receipt / Purchase Receipt (rate 0)** into the **Customer Provided** warehouse
(or mark the Item *Customer Provided* with the customer), so it never inflates your valuation.

### Finished articles
The article you estimate (e.g. `YS_MB_WAR`) becomes an Item in its own **Client SKU** group — so
client pieces never mix with regular products and the whole group can be **archived when the
project closes**.

---

## 3. Warehouses (your factory mapped to ERPNext)

Created under your company's **All Warehouses**:

| Physical area | ERPNext Warehouse | Used for |
|---|---|---|
| 7 storage racks (sheets/boards) | **Raw Materials → Board & Sheet Store** | plywood & laminate on receipt |
| Hardware racks | **Raw Materials → Hardware Store** | hinges, screws, handles, minifix |
| 2 tables (1 rack each) | **Work In Progress → Cut Parts - Table 1 / Table 2** | cut panels during a job |
| Assembly area | **Work In Progress → Assembly Area** | assembling the article |
| Project room | **Work In Progress → Project Room** | finished article takes shape |
| Packed, ready to ship (also the racks) | **Finished Goods → Packed / Dispatch** | dismantled/packed FG awaiting delivery |
| Client-shipped material | **Customer Provided** | plywood/laminate the client supplies |

Work Orders draw raw material from the stores, move it through the WIP warehouses along the 16
phases, and land the finished good in **Finished Goods**. (Want per-rack bins? Add child
warehouses under Board & Sheet Store — the app won't clash with them.)

---

## 4. Per project → per article (design → estimate)

5. **Projects → New** — one Project per customer job. Everything hangs off it.
6. In OpenCutList, export the article's **Estimate PDF** and **Parts CSV**; optionally a rendered
   image.
7. **New Estimate SKU:** pick **Project** + **Room** (dropdown master; "Other" allowed); type the
   **Article name** (a code `YS_MB_WAR` is generated); enter outer size, description, attach the
   image; in **Material**, attach the **Estimate PDF** + **Parts CSV** and **Save**.
8. On Save the app: creates/links each material **stock Item** and pulls its **ERPNext cost**
   (edge banding in accurate **metres** from the CSV); fills the **17 process steps** (qty +
   workstation + phase cost); stores the **parts list** (QR numbers) for the shop floor; and
   computes **Material + Execution + Design = internal cost → client price**. Steps 1–4 are
   locked (computed); the rest you can fine-tune. Phase costs auto-refresh when you re-open the SKU.

---

## 5. Quote the project (Quote-to-Cash)

9. **New Estimate**, pick the **Project** — as a **Draft** it auto-lists every SKU of that project
   and totals them (add a SKU later → pulled in automatically; **Refresh SKUs** re-pulls).
10. **Submit = approve & freeze** the baseline (only submitted estimates are quoted).
11. **Create Quotation** → a native **Quotation** for the customer, one line per article.
12. **Print** with **"Mallet Client Estimate"** → room-grouped PDF with images (overhead folded
    into "Design & execution"; the client never sees your factory costs).
13. Client accepts → **Quotation → Sales Order** (standard button). The Sales Order is the
    confirmed job.

> **Change before handover?** Don't edit an approved estimate — **Cancel → Amend** (native) makes
> a linked new version and keeps the original as the audit baseline; re-submit and re-quote.

---

## 6. Buy the raw material (Procure-to-Pay)

14. From the **Sales Order → Create → Material Request** (or Manufacturing raises it from the
    Work Order's shortage), listing the plywood/laminate/edge/hardware you need.
15. **Material Request → Purchase Order** to your supplier.
16. **Purchase Order → Purchase Receipt** when it arrives — receive into **Board & Sheet Store** /
    **Hardware Store**. This sets each Item's **valuation rate**, which is exactly what the
    estimate then uses as cost. (Client-shipped material → receive into **Customer Provided**.)
17. **Purchase Receipt → Purchase Invoice** → **Payment Entry** pays the supplier.

---

## 7. Manufacture (Make-to-Order)

18. On the **Estimate**, click **Build BOMs** → a native **BOM** per article (materials from stock
    + the 16 operations), set as the article's **default BOM**.
19. On the **Estimate**, click **Create Work Orders** → one **draft Work Order** per article, from
    its BOM, **linked to the Project** (this is what makes actuals roll up to Project Margin).
    Warehouses are pre-filled (WIP = *Assembly Area*, FG = *Packed / Dispatch*); adjust if needed.
    (Or use the fully-native path: **Sales Order → Create → Work Order**, or a **Production Plan**
    for several articles at once.)
20. **Submit each Work Order** → ERPNext generates a **Job Card per operation** (the 16 phases),
    each at its **workstation**. The shop floor works / scans each job card in routing order —
    Panel Saw → Edge Bander → Drill → … → Assembly → Disassembly → Pack. Cut parts sit in
    **Cut Parts - Table 1/2**; assembly in **Assembly Area / Project Room**.
21. **Labour actuals:** record time on each **Job Card** (start/finish) → post it as a **Timesheet**
    against the **Project** so the actual wage cost reaches *Project Margin*. **Material actuals:**
    a **Stock Entry (Manufacture)** issues the plywood/hardware from the stores and lands the
    **finished good** in **Finished Goods** — keep the **Project** on these entries.
22. **Ad-hoc operations** (joining oversized panels, cutting a hole for a glass door, etc.) are
    added **as extra Job Cards / operations on the Work Order** when a job needs them — the 16
    phases are the standard path, not a limit.

> **Per-part tracking:** native Job Cards track each *operation at its station*, not individual
> cut parts moving between stations. If you need physical part-by-part traceability, that's a
> custom QR-scan layer over the part numbers already captured in the **Parts** table — ask to add it.

---

## 8. Deliver & bill (Finance)

23. **Sales Order → Delivery Note** → ships the finished good from **Finished Goods** to site
    (on-site assembly/installation are the last operations).
24. **Sales Order / Delivery Note → Sales Invoice** → **Payment Entry** collects from the client.
25. Everything posts to **Accounts** — no separate bookkeeping.

---

## 9. See your real margin

26. **Project Margin** report: per project, **Estimated** cost/price/margin vs **Actual** (material
    issued + job-card labour + purchases) vs **Billed**, and the **variance** — so you see exactly
    where a job ate the margin.

---

## What ERPNext data this creates (nothing is a black box)

| You do | ERPNext gets |
|---|---|
| Estimate Settings → Create masters | Workstations, Operations, Routing, Rooms, **Item Groups**, **UOMs**, Item fields, **Warehouses**, print format |
| Save an Estimate SKU + PDF/CSV | one **stock Item** per material (grouped, UOM'd, priced from ERPNext) + one **Client SKU** Item for the article |
| Set prices | Purchase Receipt (valuation) / buying Item Price / standard rate — via the **Mallet Materials** report |
| Estimate → Submit → Create Quotation | a **Quotation**, then **Sales Order** |
| Material Request → PO → Purchase Receipt | priced **stock** in the stores |
| Build BOMs → Work Order | **BOMs**, **Work Orders**, **Job Cards** |
| Job cards + stock issue + delivery + invoice | costs & revenue on the **Project** → **Project Margin** |

---

## Notes
- **No buttons on the SKU** — import runs on Save; just attach the two files. Phase costs
  re-price automatically when you open the SKU.
- **Rooms** are a master (Estimate Room); "Other" is included.
- **Workstation rates** live natively on each Workstation; Estimate Settings seeds them.
- **Approval:** Estimate is submittable (Draft → Submit → Quotation); changes go via **Amend**.
- The standalone React prototype in `../SKU_Estimator` shares the cost model (offline).

---

## Deploy (Frappe Cloud custom app)
1. Push this repo to GitHub; on Frappe Cloud add it to your **staging bench group → Apps**, then
   **Install App** on the site.
2. Open **Estimate Settings → Create / refresh manufacturing masters**.
3. After a code update: the site must be **updated** to the new build (not just built).

### Hands-off deploy on `git push` (GitHub Actions)
`.github/workflows/deploy-staging.yml` runs the dashboard **"Update Now"** for you on every push
to `main`, scoped to **`mallet_estimator` only** (ERPNext/others are never touched):
1. In **Frappe Cloud → account → API Access**, generate an **API key + secret**.
2. In **GitHub → repo → Settings → Secrets and variables → Actions**, add
   `FRAPPE_CLOUD_API_KEY` and `FRAPPE_CLOUD_API_SECRET` (optionally vars `FC_HOST` =
   `cloud.frappe.io`, `FC_BENCH` = your staging bench id).
3. Push → the workflow calls `press.api.bench.deploy_and_update` (build **+** migrate the site)
   and **waits until the bench actually carries the new commit** before going green — so a green
   check means it's genuinely live, and a failed build fails the job with the error. ERPNext
   updates stay a deliberate manual **Update Now**.
