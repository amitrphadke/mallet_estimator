import json

import frappe
from frappe import _
from frappe.model.document import Document


def _reattach_file(url, sku_name, fieldname):
    """A file uploaded against the Estimate belongs to the SKU that consumes
    it — re-point it so the SKU's attachment GC owns its own sources."""
    if not url:
        return
    for fname in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
        frappe.db.set_value("File", fname, {
            "attached_to_doctype": "Estimate SKU",
            "attached_to_name": sku_name,
            "attached_to_field": fieldname,
        }, update_modified=False)


def _sku_client_buckets(s):
    """One SKU's CLIENT-side amounts by bucket, at its own effective margins.
    Live path: the fresh _calc from compute_costs. Frozen path: the stored
    bifurcation JSON (what was quoted)."""
    calc = getattr(s, "_calc", None)
    if calc:
        return {
            "material": float(calc.get("client_material") or 0),
            "labor": float(calc.get("client_labor") or 0),
            "design": float(calc.get("client_design") or 0),
            "overhead": float(calc.get("client_overhead") or 0),
        }
    try:
        rows = {r["label"]: r["amount"]
                for r in (json.loads(s.cost_breakup or "{}").get("bifurcation") or {}).get("rows", [])}
    except Exception:
        rows = {}
    def pick(prefix):
        return next((float(v or 0) for l, v in rows.items() if l.startswith(prefix)), 0.0)
    return {
        "material": pick("Material") or float(s.client_material or 0),
        "labor": pick("Labor"),
        "design": pick("Design"),
        "overhead": pick("Factory Overhead"),
    }


class Estimate(Document):
    def on_submit(self):
        """Approving the estimate FREEZES every SKU's rates — later price-list
        changes never alter what was quoted (the price list keeps the history)."""
        for row in self.skus or []:
            if row.estimate_sku:
                frappe.db.set_value("Estimate SKU", row.estimate_sku, "rates_frozen", 1,
                                    update_modified=False)

    def on_cancel(self):
        for row in self.skus or []:
            if row.estimate_sku:
                frappe.db.set_value("Estimate SKU", row.estimate_sku, "rates_frozen", 0,
                                    update_modified=False)

    def validate(self):
        # SKU selection is the ESTIMATE's feature: rows are added by hand (or via
        # 'Add all project SKUs'), so the same SKU can serve many estimates —
        # e.g. one estimate per-SKU-PDFs vs one whole-project-PDF, compared side
        # by side. A draft only refreshes the DATA of the rows it carries; once
        # submitted the list and totals are frozen as the baseline.
        if self.docstatus == 0:
            self.process_intake()
            self.enforce_single_mode()
            self.refresh_sku_rows()
        # Provisional allowances (F6) — amounts are a simple qty x assumed rate,
        # recomputed every save so the client-print subtotal is always right.
        self.compute_allowances()
        self.compute_transport_and_tax()
        self.build_cost_breakup()

    def compute_allowances(self):
        total = 0
        for a in self.allowances or []:
            a.amount = (a.qty or 0) * (a.assumed_rate or 0)
            total += a.amount
        self.total_allowance = total

    def compute_transport_and_tax(self):
        """C1 — consolidated transport as an EDITABLE table: SKUs share trips, so
        the estimate's trip rows (change qty/rate, add more) are what the client
        pays. Rows are seeded once from the Estimate Settings rates. T1 — output
        GST is charged on top of the client total (quote plus GST, always)."""
        if not self.meta.has_field("total_transport"):
            return
        from mallet_estimator.estimator import transport_rates
        settings = frappe.get_single("Estimate Settings")
        rates = transport_rates(settings)
        if self.meta.has_field("transport_items"):
            if not self.get("transport_items"):
                for label, desc, rate in (
                    ("Big Tempo (inward)", "Ply + internal laminate + joinery hardware", rates["tempo"]),
                    ("External Laminate (inward)", "External laminate sheets", rates["ext_lam"]),
                    ("Client Hardware (inward)", "Hinges, rails, handles, lifts", rates["client_hw"]),
                    ("Outward Delivery", "Finished goods to site", rates["outward"]),
                ):
                    self.append("transport_items", {
                        "trip_type": label, "description": desc, "qty": 1, "rate": rate,
                    })
            total = 0
            for t in self.transport_items:
                t.amount = (t.qty or 0) * (t.rate or 0)
                total += t.amount
            self.total_transport = total
        else:
            self.total_transport = 0
        # aggregate_project_skus left the totals transport-free; add the shared
        # trips here, then output GST on the full client amount.
        if self.docstatus == 0:
            self.total_internal = (self.total_internal or 0) + self.total_transport
            self.total_client = (self.total_client or 0) + self.total_transport
        base = float(self.total_client or 0)
        # a NEW doc arrives with gst_pct as the STRING "18" — coerce defensively
        try:
            gst_pct = 18.0 if self.gst_pct in (None, "") else float(self.gst_pct)
        except (TypeError, ValueError):
            gst_pct = 18.0
        self.total_gst = base * gst_pct / 100.0
        self.total_with_gst = base + self.total_gst

    @frappe.whitelist()
    def refresh_skus(self):
        """'Add all project SKUs' — append every Estimate SKU of this Project
        that isn't already a row. Rows the user removed by hand stay removed
        only if they delete them again after this; nothing is ever dropped
        automatically."""
        if self.docstatus != 0:
            frappe.throw(_("This estimate is approved (submitted). Amend it to change the SKUs."))
        from mallet_estimator import consolidate as cons
        existing = {r.estimate_sku for r in (self.skus or [])}
        mode = self.estimate_mode()
        added, skipped = 0, []
        candidates = frappe.get_all(
            "Estimate SKU", filters={"project": self.project},
            fields=["name", "estimation_mode"],
            order_by="room asc, article_name asc",
        ) if self.project else []
        # An estimate is single-mode; when it is still empty the FIRST SKU
        # decides which mode it becomes, and the rest must match.
        if not mode:
            for c in candidates:
                if c.name not in existing:
                    mode = c.get("estimation_mode") or cons.PDF_MODE
                    break
        for c in candidates:
            if c.name in existing:
                continue
            if (c.get("estimation_mode") or cons.PDF_MODE) != mode:
                skipped.append(c.name)
                continue
            self.append("skus", {"estimate_sku": c.name})
            added += 1
        self.save(ignore_permissions=True)
        if skipped:
            frappe.msgprint(
                _("Skipped {0} SKU(s) of the other estimation mode — an estimate "
                  "cannot mix CSV-Nest and OCL PDF: {1}").format(len(skipped), ", ".join(skipped)),
                indicator="orange")
        return {"count": len(self.skus), "added": added, "skipped": len(skipped),
                "mode": mode, "client": self.total_client}

    def refresh_sku_rows(self):
        """Refresh the DATA of the rows this estimate carries (dedupe, reprice
        unfrozen SKUs at current margins/rates) and roll up the totals. The row
        LIST itself is the user's selection — never rebuilt automatically."""
        seen, rows = set(), []
        for r in self.skus or []:
            if r.estimate_sku and r.estimate_sku not in seen:
                seen.add(r.estimate_sku)
                rows.append(r)
        self.set("skus", rows)
        totals = dict(material=0, labor=0, overhead=0, design=0, internal=0, client=0,
                      mat_discount=0, mat_tax=0, mat_tax_saved=0)
        # Client buckets are summed PER SKU (each at its own effective margins —
        # house default or SKU override) — never re-derived from house margins.
        self._client_buckets = dict(material=0.0, labor=0.0, design=0.0, overhead=0.0)
        self._sqft_sum = 0.0
        self._room_groups = {}
        self._room_order = []
        # PASS 1 — load + standalone reprice; collect CSV-Nest inputs
        loaded, nest_inputs = [], {}
        for r in rows:
            if not frappe.db.exists("Estimate SKU", r.estimate_sku):
                continue
            s = frappe.get_doc("Estimate SKU", r.estimate_sku)
            # Reprice at the CURRENT margins/workstation rates before reading —
            # stored totals can pre-date a margin change (frozen SKUs keep the
            # values they were quoted at).
            if not s.get("rates_frozen"):
                try:
                    s.compute_costs()
                except Exception:
                    frappe.log_error(frappe.get_traceback(), f"estimate reprice {s.name}")
                if (s.get("estimation_mode") or "") == "CSV-Nest":
                    try:
                        ni = (json.loads(s.import_drivers or "{}") or {}).get("__nest_inputs__")
                    except Exception:
                        ni = None
                    if ni:
                        nest_inputs[s.name] = ni
            loaded.append((r, s))
        # PASS 2 — cross-SKU consolidation: nest all the estimate's CSV-Nest
        # SKUs' parts together, so shared sheets/rolls make every SKU cheaper
        # than it is alone. IN MEMORY ONLY — an SKU can serve many estimates,
        # so its stored standalone numbers are never overwritten.
        self._consolidation = None
        if len(nest_inputs) >= 2:
            try:
                self._apply_consolidation(loaded, nest_inputs)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"estimate consolidation {self.name}")
        # PASS 3 — roll up (combined values wherever consolidation ran)
        for (r, s) in loaded:
            for k, v in _sku_client_buckets(s).items():
                self._client_buckets[k] += v
            blk = s.facial_sqft_block() or {}
            self._sqft_sum += float(blk.get("sqft") or 0)
            r.item = s.item
            r.room = ("Multiple Rooms" if s.get("multi_room") else s.room)
            r.article_name = s.article_name
            r.internal_cost = s.internal_cost
            r.client_total = s.client_total
            r.est_days = float(s.get("est_days") or 0)
            # the portfolio view: which article carries the project's profit
            transport = float(s.get("transport_cost") or 0)
            r.profit = float(s.client_total or 0) + transport - float(s.internal_cost or 0)
            r.margin_pct = (r.profit / float(s.client_total) * 100.0) if s.client_total else 0
            # room-wise rollup for the on-screen summary
            rn = "Multiple Rooms" if s.get("multi_room") else (s.room or "Unassigned")
            if rn not in self._room_groups:
                self._room_groups[rn] = {"room": rn, "count": 0, "subtotal": 0.0, "sqft": 0.0}
                self._room_order.append(rn)
            g = self._room_groups[rn]
            g["count"] += 1
            g["subtotal"] += float(s.client_total or 0)
            g["sqft"] += float(blk.get("sqft") or 0)
            totals["material"] += s.material_cost or 0
            totals["mat_discount"] += float(s.get("material_discount_total") or 0)
            totals["mat_tax"] += float(s.get("material_tax_total") or 0)
            totals["mat_tax_saved"] += float(s.get("material_tax_saved_total") or 0)
            totals["labor"] += s.labor_cost or 0
            totals["overhead"] += s.overhead_cost or 0
            totals["design"] += s.design_cost or 0
            # Per-SKU transport is a STANDALONE view (client_total already excludes
            # it) — strip it from internal too; the estimate's consolidated trips
            # are added in compute_transport_and_tax.
            totals["internal"] += (s.internal_cost or 0) - (s.get("transport_cost") or 0)
            totals["client"] += s.client_total or 0
        self.total_material = totals["material"]
        if self.meta.has_field("total_material_discount"):
            self.total_material_discount = totals["mat_discount"]
        if self.meta.has_field("total_material_tax"):
            self.total_material_tax = totals["mat_tax"]
        if self.meta.has_field("total_material_with_tax"):
            self.total_material_with_tax = totals["material"] + totals["mat_tax"]
        if self.meta.has_field("total_material_tax_saved"):
            self.total_material_tax_saved = totals["mat_tax_saved"]
        self.total_labor = totals["labor"]
        self.total_overhead = totals["overhead"]
        self.total_design = totals["design"]
        # Transport-free at this point; compute_transport_and_tax (which runs
        # right after in validate) adds the consolidated trips + GST on top.
        self.total_internal = totals["internal"]
        self.total_client = totals["client"]
        if self.meta.has_field("total_days"):
            self.total_days = sum(float(r.est_days or 0) for r in rows)

    @frappe.whitelist()
    def add_csv_nest_sku(self, article_name, room=None):
        """Estimate-first flow: the estimate is where SKUs are born. Creates a
        CSV-Nest SKU pre-linked to this estimate's project/customer, adds its
        row here, and returns the name — the UI routes to it so the user
        attaches the Part List CSV (+ 7 Views PDF) and tunes the décor map.
        Saving the SKU re-prices this estimate automatically (consolidation
        included), so adding/removing SKUs shows exactly how shared material
        moves each price."""
        if self.docstatus != 0:
            frappe.throw(_("This estimate is submitted — amend it first."))
        if not (article_name or "").strip():
            frappe.throw(_("Article name is required."))
        sku = frappe.new_doc("Estimate SKU")
        sku.article_name = article_name.strip()
        if sku.meta.has_field("estimation_mode"):
            sku.estimation_mode = "CSV-Nest"
        if self.get("project"):
            sku.project = self.project
        if self.get("customer") and sku.meta.has_field("customer"):
            sku.customer = self.customer
        if room:
            sku.room = room
        sku.insert()
        self.append("skus", {"estimate_sku": sku.name})
        self.save()
        return sku.name

    def sku_modes(self):
        """{sku: estimation_mode} for the rows this estimate carries."""
        names = [r.estimate_sku for r in (self.skus or []) if r.estimate_sku]
        if not names:
            return {}
        return {
            d.name: d.get("estimation_mode")
            for d in frappe.get_all("Estimate SKU", filters={"name": ["in", names]},
                                    fields=["name", "estimation_mode"])
        }

    def estimate_mode(self):
        """The mode this estimate is committed to (None while it has no SKUs)."""
        from mallet_estimator import consolidate as cons
        csv_nest, pdf = cons.split_by_mode(self.sku_modes())
        if csv_nest and not pdf:
            return cons.CSV_MODE
        if pdf and not csv_nest:
            return cons.PDF_MODE
        return None

    def enforce_single_mode(self):
        """CSV-Nest and OCL-PDF SKUs are mutually exclusive on one estimate.
        Their material packing is decided by different authorities — CSV-Nest
        sheets are nested (and re-nested estimate-wide) HERE, PDF sheet counts
        come already packed from OpenCutList per SKU — so a mixed estimate
        would sum quantities that were never packed together and would show
        the shared-material saving on only part of the job."""
        from mallet_estimator import consolidate as cons
        modes = self.sku_modes()
        if not cons.is_mixed(modes):
            return
        csv_nest, pdf = cons.split_by_mode(modes)
        frappe.throw(
            _("An estimate cannot mix estimation modes — material packing is "
              "computed here for CSV-Nest SKUs and by OpenCutList for PDF SKUs, "
              "so their sheet counts can't be added up together.<br><br>"
              "<b>CSV-Nest:</b> {0}<br><b>OCL PDF:</b> {1}<br><br>"
              "Keep one mode per estimate — remove the odd ones out, or build a "
              "second estimate for them (the same SKU may serve many estimates).")
            .format(", ".join(csv_nest), ", ".join(pdf)),
            title=_("Mixed estimation modes"))

    def process_intake(self):
        """The intake grid IS the estimation UX: one row = Room + Article name
        + Part List CSV (+ views PDF). On save, every complete row becomes a
        CSV-Nest SKU — imported, nested, priced, décor prefilled, operations
        seeded — and its row moves into the skus table below (running just
        before refresh_sku_rows, the new SKU joins consolidation in the SAME
        save). Incomplete rows stay in the grid for the user to finish."""
        if not self.meta.has_field("intake") or not self.get("intake"):
            return
        from mallet_estimator import consolidate as cons
        # The estimate's mode is decided by its first SKU and every later row
        # must match it (mixing is refused with an explanation).
        mode = self.estimate_mode()
        existing_rows = {r.estimate_sku for r in (self.skus or []) if r.estimate_sku}
        remaining, created, picked = [], [], []
        for row in self.intake:
            # (a) the row simply POINTS at an SKU that already exists
            if row.get("existing_sku"):
                name = row.existing_sku
                row_mode = frappe.db.get_value("Estimate SKU", name, "estimation_mode") or cons.PDF_MODE
                if mode and row_mode != mode:
                    frappe.throw(
                        _("<b>{0}</b> is a {1} SKU but this estimate is {2} — the two modes "
                          "cannot share an estimate, because their material packing comes from "
                          "different places. Put it on its own estimate.").format(name, row_mode, mode),
                        title=_("Mixed estimation modes"))
                mode = mode or row_mode
                if name not in existing_rows:
                    self.append("skus", {"estimate_sku": name})
                    existing_rows.add(name)
                    picked.append(name)
                continue
            # (b) or CREATES one — the attached file decides the mode
            try:
                row_mode = cons.intake_row_mode(bool(row.get("parts_csv")),
                                                bool(row.get("estimate_pdf")))
            except ValueError:
                frappe.throw(
                    _("Row <b>{0}</b> has both a Part List CSV and a Material Estimate PDF — "
                      "an SKU is packed by one authority or the other, never both. Keep one.")
                    .format(row.get("article_name") or row.idx),
                    title=_("Which packing applies?"))
            if not (row.get("article_name") and row.get("room") and row_mode):
                remaining.append(row)
                continue
            if mode and row_mode != mode:
                frappe.throw(
                    _("Row <b>{0}</b> would create a {1} SKU but this estimate is {2} — "
                      "one estimate holds one mode. Start a separate estimate for it.")
                    .format(row.get("article_name"), row_mode, mode),
                    title=_("Mixed estimation modes"))
            try:
                sku = frappe.new_doc("Estimate SKU")
                sku.article_name = row.article_name.strip()
                if sku.meta.has_field("estimation_mode"):
                    sku.estimation_mode = row_mode
                if self.get("project"):
                    sku.project = self.project
                if self.get("customer") and sku.meta.has_field("customer"):
                    sku.customer = self.customer
                sku.room = row.room
                for fieldname in ("parts_csv", "estimate_pdf", "partlist_pdf", "views_pdf"):
                    if row.get(fieldname):
                        sku.set(fieldname, row.get(fieldname))
                sku.insert()
                mode = mode or row_mode
                for fieldname in ("parts_csv", "estimate_pdf", "partlist_pdf", "views_pdf"):
                    if row.get(fieldname):
                        _reattach_file(row.get(fieldname), sku.name, fieldname)
                self.append("skus", {"estimate_sku": sku.name})
                existing_rows.add(sku.name)
                created.append(f"{sku.article_name} ({sku.name})")
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"estimate intake {row.get('article_name')}")
                remaining.append(row)
                frappe.msgprint(
                    _("Could not create SKU for <b>{0}</b> — row kept; see the Error Log.").format(
                        row.get("article_name")), indicator="red")
        self.set("intake", remaining)
        if created or picked:
            parts = []
            if created:
                parts.append(_("created {0}: {1}").format(len(created), ", ".join(created)))
            if picked:
                parts.append(_("added {0} existing: {1}").format(len(picked), ", ".join(picked)))
            frappe.msgprint(_("SKUs — {0}").format("; ".join(parts)),
                            indicator="green", alert=True)

    @frappe.whitelist()
    def sku_files_overview(self):
        """One line per SKU for the estimate screen — files present, nest
        state, issues, totals. Drives the 'SKU Files & Status' panel."""
        rows = []
        for r in self.skus or []:
            if not r.estimate_sku or not frappe.db.exists("Estimate SKU", r.estimate_sku):
                continue
            s = frappe.db.get_value(
                "Estimate SKU", r.estimate_sku,
                ["name", "article_name", "room", "multi_room", "estimation_mode",
                 "parts_csv", "views_pdf", "estimate_pdf", "import_drivers",
                 "unpriced_materials", "client_total", "rates_frozen", "sku_code"],
                as_dict=True)
            drivers = {}
            try:
                drivers = json.loads(s.import_drivers or "{}") or {}
            except Exception:
                pass
            nest = drivers.get("__nest__") or {}
            rows.append({
                "sku": s.name, "article": s.article_name, "code": s.sku_code,
                "room": "Multiple Rooms" if s.multi_room else s.room,
                "mode": s.estimation_mode or "OCL PDF (standard)",
                "parts_csv": s.parts_csv, "views_pdf": s.views_pdf,
                "estimate_pdf": s.estimate_pdf,
                "sheets": sum(float(v.get("sheets") or 0) for v in nest.values()),
                "issues": len(drivers.get("__issues__") or []),
                "unpriced": bool(s.unpriced_materials),
                "client_total": s.client_total,
                "frozen": bool(s.rates_frozen),
            })
        return rows

    @frappe.whitelist()
    def sku_materials(self, sku):
        """READ-ONLY material lines of one of this estimate's SKUs, for the
        inline panel — check what a SKU is made of without leaving the
        estimate. Quantities are the SKU's own (stored) values; the estimate's
        consolidated allocation is reported separately in cost_breakup."""
        if sku not in {r.estimate_sku for r in (self.skus or [])}:
            frappe.throw(_("{0} is not on this estimate.").format(sku))
        doc = frappe.get_doc("Estimate SKU", sku)
        doc.check_permission("read")
        return {
            "sku": doc.name,
            "article": doc.article_name,
            "code": doc.get("sku_code"),
            "mode": doc.get("estimation_mode") or "OCL PDF (standard)",
            "material_cost": doc.get("material_cost"),
            "rows": [
                {"material": m.get("material"), "item": m.get("item"),
                 "description": m.get("description"), "qty": m.get("qty"),
                 "uom": m.get("uom"), "rate": m.get("unit_cost"),
                 "amount": m.get("line_cost"),
                 "amount_with_tax": m.get("amount_with_tax"),
                 "tax": m.get("tax_amount"), "discount": m.get("discount_amount"),
                 "tax_saved": m.get("tax_saved"), "std_tax": m.get("tax_rate_policy"),
                 "applied_tax": m.get("tax_rate"),
                 "manual": bool(m.get("is_manual")),
                 "client_supplied": bool(m.get("customer_supplied"))}
                for m in (doc.get("materials") or [])
            ],
        }

    @frappe.whitelist()
    def add_skus_from_files(self, files, room=None):
        """Bulk estimate-first intake: `files` = [{file_url, file_name}] just
        uploaded against this estimate — every CSV becomes a CSV-Nest SKU
        named after its file, and a PDF sharing the CSV's name-stem becomes
        that SKU's 7 Views PDF. Each SKU imports (nesting, décor prefill,
        ops, costing) on insert; files are re-attached to their SKU so its
        attachment GC owns them. Returns a per-SKU summary for the dialog."""
        if isinstance(files, str):
            files = json.loads(files or "[]")
        if self.docstatus != 0:
            frappe.throw(_("This estimate is submitted — amend it first."))

        def stem(f):
            n = (f.get("file_name") or f.get("file_url") or "").rsplit("/", 1)[-1]
            return n.rsplit(".", 1)[0].strip().lower()

        def is_ext(f, ext):
            return (f.get("file_name") or f.get("file_url") or "").lower().endswith(ext)

        csvs = [f for f in files if is_ext(f, ".csv")]
        pdfs = [f for f in files if is_ext(f, ".pdf")]
        if not csvs:
            frappe.throw(_("No CSV part lists among the uploaded files."))

        used_pdf, out = set(), []
        for f in csvs:
            cs = stem(f)
            views = None
            for p in pdfs:
                if p.get("file_url") in used_pdf:
                    continue
                ps = stem(p)
                base = ps.replace("views", "").replace("view", "").strip(" _-")
                if ps.startswith(cs) or cs.startswith(base) or (base and base.startswith(cs)):
                    views = p
                    used_pdf.add(p.get("file_url"))
                    break
            article = stem(f).replace("_", " ").replace("-", " ").strip().title() or stem(f)
            sku = frappe.new_doc("Estimate SKU")
            sku.article_name = article
            if sku.meta.has_field("estimation_mode"):
                sku.estimation_mode = "CSV-Nest"
            if self.get("project"):
                sku.project = self.project
            if self.get("customer") and sku.meta.has_field("customer"):
                sku.customer = self.customer
            if room:
                sku.room = room
            sku.parts_csv = f.get("file_url")
            if views:
                sku.views_pdf = views.get("file_url")
            sku.insert()
            _reattach_file(f.get("file_url"), sku.name, "parts_csv")
            if views:
                _reattach_file(views.get("file_url"), sku.name, "views_pdf")
            self.append("skus", {"estimate_sku": sku.name})
            drivers = {}
            try:
                drivers = json.loads(sku.import_drivers or "{}") or {}
            except Exception:
                pass
            nest = drivers.get("__nest__") or {}
            out.append({
                "sku": sku.name, "article": sku.article_name, "views": bool(views),
                "sheets": sum(float(v.get("sheets") or 0) for v in nest.values()),
                "issues": len(drivers.get("__issues__") or []),
                "unpriced": sku.get("unpriced_materials") or "",
                "client_total": sku.get("client_total"),
            })
        self.save()
        return out

    def _apply_consolidation(self, loaded, nest_inputs):
        """Nest every CSV-Nest SKU's parts TOGETHER per material and re-price
        each SKU at its allocated share: parts area is paid directly, offcut
        waste splits pro-rata by part-area share per material (decision
        2026-08-07). Sheet-count operations follow the allocated sheets, and
        batch-efficiency tiers on the Operation master scale minutes/unit at
        the estimate's combined quantities. Everything happens on the loaded
        in-memory docs — nothing is saved back to the SKUs."""
        from mallet_estimator import consolidate as cons
        from mallet_estimator.estimator import op_phase

        result = cons.consolidate(nest_inputs)
        mats = result["materials"]
        by_name = {s.name: s for (_r, s) in loaded}
        standalone = {n: float(by_name[n].client_total or 0) for n in nest_inputs}

        sheet_ops = ("Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting")
        for name in nest_inputs:
            s = by_name[name]
            for m in s.materials or []:
                if m.get("is_manual"):
                    continue
                key = f"{m.material}@{float(m.thickness or 0):g}"
                info = mats.get(key) or mats.get(str(m.material or ""))
                if info and name in info["alloc"]:
                    m.qty = info["alloc"][name]
                    m.line_cost = float(m.qty or 0) * float(m.unit_cost or 0)
            ratio = result["sheet_ratio"].get(name, 1.0)
            for row in s.labor or []:
                if op_phase(row) in sheet_ops:
                    row.qty = round(float(row.qty or 0) * ratio, 2)

        self._apply_batch_tiers([by_name[n] for n in nest_inputs])

        per_sku = []
        for name in nest_inputs:
            s = by_name[name]
            try:
                s.derive_joinery()   # Fevicol/Abrotape follow the shrunk lamination qty
                s.compute_costs()
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"consolidated reprice {name}")
            per_sku.append({
                "sku": name, "article": s.article_name,
                "standalone": standalone[name],
                "combined": float(s.client_total or 0),
                "saving": standalone[name] - float(s.client_total or 0),
            })
        self._consolidation = {
            "materials": {
                k: {kk: v[kk] for kk in ("kind", "combined", "standalone", "util")}
                for k, v in mats.items()
            },
            "per_sku": per_sku,
            "standalone_client": sum(p["standalone"] for p in per_sku),
            "combined_client": sum(p["combined"] for p in per_sku),
        }
        self._consolidation["savings"] = (self._consolidation["standalone_client"]
                                          - self._consolidation["combined_client"])
        sheets_saved = sum(
            (v["standalone"] - v["combined"])
            for v in mats.values() if v["kind"] in ("sheet", "laminate"))
        frappe.msgprint(
            _("Consolidated nesting across {0} SKUs: {1:g} sheet(s) saved vs "
              "standalone; client total ₹{2:,.0f} vs ₹{3:,.0f} alone "
              "(₹{4:,.0f} saved).").format(
                len(per_sku), sheets_saved,
                self._consolidation["combined_client"],
                self._consolidation["standalone_client"],
                self._consolidation["savings"]),
            title=_("CSV-Nest consolidation"), indicator="green")

    def _apply_batch_tiers(self, sku_docs):
        """Batch-efficiency: an Operation's mallet_batch_tiers rows say 'from
        this combined qty, minutes/unit scale by this factor' (bulk sheets
        paste/cut faster; more SKUs shipped and installed take less transport
        and installation time per SKU). Tiers are keyed on the ESTIMATE-wide
        qty of that operation across the consolidated SKUs."""
        from mallet_estimator import consolidate as cons
        from mallet_estimator.estimator import op_phase

        if not frappe.db.exists("DocType", "Mallet Operation Batch Tier"):
            return
        totals = {}
        for s in sku_docs:
            for row in s.labor or []:
                op = op_phase(row)
                if op:
                    totals[op] = totals.get(op, 0.0) + float(row.qty or 0)
        for op, total in totals.items():
            tiers = [(t.from_qty, t.factor) for t in frappe.get_all(
                "Mallet Operation Batch Tier", filters={"parent": op, "parenttype": "Operation"},
                fields=["from_qty", "factor"])]
            factor = cons.batch_factor([(t[0], t[1]) for t in tiers], total)
            if factor == 1.0:
                continue
            for s in sku_docs:
                for row in s.labor or []:
                    if op_phase(row) == op:
                        row.carp_min = round(float(row.carp_min or 0) * factor, 2)

    def print_payload(self, kind="client"):
        """Everything the two print formats render, computed once server-side.
        LEAK-SAFE BY CONSTRUCTION: only client-shared numbers enter this dict —
        internal cost, margins and profit never do, so either print can leak
        without exposing pricing. The provisional-allowance total is spread
        proportionally into the printed SKU prices (the itemised rows stay in
        the ERP for the final true-up)."""
        from mallet_estimator import inventory
        CHOOSE = ("Laminate External", "Edge Banding External", "Client Hardware")
        skus, total_client = [], 0.0
        for r in self.skus or []:
            if r.estimate_sku and frappe.db.exists("Estimate SKU", r.estimate_sku):
                s = frappe.get_doc("Estimate SKU", r.estimate_sku)
                skus.append(s)
                total_client += float(s.client_total or 0)
        allowance = float(self.total_allowance or 0)
        spread = (1 + allowance / total_client) if total_client else 1.0
        rooms, order, gallery, rate_rows = {}, [], [], {}
        for s in skus:
            blk = s.facial_sqft_block() or {}
            sqft = float(blk.get("sqft") or 0)
            price = float(s.client_total or 0) * spread
            rn = "Multiple Rooms" if s.get("multi_room") else (s.room or "Unassigned")
            if s.get("multi_room") and s.get("rooms_covered"):
                rn = f"Multiple Rooms ({s.rooms_covered})"
            if rn not in rooms:
                rooms[rn] = {"room": rn, "rows": [], "subtotal": 0.0, "sqft": 0.0, "days": 0.0}
                order.append(rn)
            from mallet_estimator.estimator import dims_ftin
            rooms[rn]["rows"].append({
                "sku": s.sku_code or s.name, "article": s.article_name or "",
                "item": s.item or "", "dims": "%d x %d x %d" % (s.outer_w or 0, s.outer_d or 0, s.outer_h or 0),
                "dims_ftin": dims_ftin(s.outer_w, s.outer_d, s.outer_h),
                "price": price, "sqft": sqft,
                "per_sqft": (price / sqft) if sqft else 0,
                "days": float(s.get("est_days") or 0),
            })
            rooms[rn]["subtotal"] += price
            rooms[rn]["sqft"] += sqft
            rooms[rn]["days"] += float(s.get("est_days") or 0)
            choose_rows, internal_rows = [], []
            for m in s.materials or []:
                bucket = inventory.material_bucket(m.item, m.material)
                row = {"item": m.item, "bucket": bucket, "qty": float(m.qty or 0),
                       "uom": m.uom or "", "budget": float(m.qty or 0) * float(m.unit_cost or 0)}
                if bucket in CHOOSE:
                    choose_rows.append(row)
                    rate_rows.setdefault(m.item, {
                        "item": m.item, "bucket": bucket, "uom": m.uom or "",
                        "rate": float(m.unit_cost or 0),
                    })
                elif kind == "execution":
                    internal_rows.append(row)
            # views extracted ON THE FLY from the 7Views PDF (no stored PNG
            # clutter on the SKU) — embedded as data URIs, print-only
            views = []
            if kind == "execution" and s.get("views_pdf"):
                try:
                    import base64
                    from mallet_estimator import views_pdf as _vp
                    from mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku import _file_content
                    for label, ext, data in _vp.extract_view_images(_file_content(s.views_pdf)):
                        mime = "image/png" if ext == "png" else "image/jpeg"
                        views.append((label, f"data:{mime};base64," + base64.b64encode(data).decode()))
                except Exception:
                    frappe.log_error(frappe.get_traceback(), f"print views {s.name}")
            from mallet_estimator.estimator import dims_ftin as _dftin
            gallery.append({
                "sku": s.sku_code or s.name, "article": s.article_name or "", "room": rn,
                "iso": s.article_image, "views": views, "price": price, "sqft": sqft,
                "dims": "%d x %d x %d mm" % (s.outer_w or 0, s.outer_d or 0, s.outer_h or 0),
                "dims_ftin": _dftin(s.outer_w, s.outer_d, s.outer_h),
                "choose": choose_rows, "internal": internal_rows,
            })
        room_list = []
        for rn in order:
            g = rooms[rn]
            g["per_sqft"] = (g["subtotal"] / g["sqft"]) if g["sqft"] else 0
            room_list.append(g)
        transport = float(self.total_transport or 0)
        subtotal = total_client * spread
        gst_pct = float(self.gst_pct if self.gst_pct is not None else 18)
        gst = (subtotal + transport) * gst_pct / 100.0
        status = {0: "DRAFT", 1: "APPROVED", 2: "CANCELLED"}.get(self.docstatus, "DRAFT")
        if self.quotation:
            status += f" · Quotation {self.quotation}"
        return {
            "kind": kind, "status": status, "is_draft": self.docstatus == 0,
            "rooms": room_list, "total_sqft": sum(g["sqft"] for g in room_list),
            "total_days": sum(g.get("days") or 0 for g in room_list),
            "per_sqft_total": (subtotal / sum(g["sqft"] for g in room_list))
                              if sum(g["sqft"] for g in room_list) else 0,
            "subtotal": subtotal, "transport": transport,
            "gst_pct": gst_pct, "gst": gst, "grand_total": subtotal + transport + gst,
            "assumed_rates": sorted(rate_rows.values(), key=lambda x: (x["bucket"], x["item"])),
            "gallery": gallery,
        }

    @frappe.whitelist()
    def compare_with(self, other):
        """Compare this estimate with another (e.g. per-SKU PDFs vs the whole
        project modelled as ONE SketchUp file) — bucket by bucket, with the
        scale saving in amount and %. Both estimates should carry the same SKUs;
        the numbers tell how much material + operation time the single-file
        design saves."""
        if not other or other == self.name:
            frappe.throw(_("Pick a DIFFERENT estimate to compare with."))
        b_doc = frappe.get_doc("Estimate", other)
        # Comparison is only meaningful within the same job: same project AND
        # same client (per-SKU-PDFs vs one-file design of the SAME scope).
        if (b_doc.project or "") != (self.project or "") or (b_doc.customer or "") != (self.customer or ""):
            frappe.throw(_("Compare estimates of the SAME project and client — {0} belongs to {1} / {2}.")
                         .format(other, b_doc.project or "?", b_doc.customer or "?"))

        def parts(doc):
            d = json.loads(doc.cost_breakup or "{}")
            bif = d.get("bifurcation") or {}
            return (
                {r["label"]: r["amount"] for r in bif.get("rows", [])},
                bif, d.get("sqft"),
            )

        a_rows, a_bif, a_sq = parts(self)
        b_rows, b_bif, b_sq = parts(b_doc)
        if not a_bif or not b_bif:
            frappe.throw(_("Both estimates need a saved cost bifurcation — open and save each once."))
        labels = list(a_rows) + [l for l in b_rows if l not in a_rows]
        rows = []
        for label in labels:
            a, b = float(a_rows.get(label) or 0), float(b_rows.get(label) or 0)
            rows.append({"label": label, "a": a, "b": b, "delta": b - a,
                         "pct": ((b - a) / a * 100.0) if a else 0})
        for label, a, b in (
            (_("Total before taxes"), a_bif.get("pre_tax") or 0, b_bif.get("pre_tax") or 0),
            (_("Taxes"), a_bif.get("taxes") or 0, b_bif.get("taxes") or 0),
            (_("Grand Total incl. GST"), a_bif.get("grand_total") or 0, b_bif.get("grand_total") or 0),
        ):
            rows.append({"label": label, "a": a, "b": b, "delta": b - a,
                         "pct": ((b - a) / a * 100.0) if a else 0, "bold": 1})
        if a_sq and b_sq:
            rows.append({"label": _("Rate / sq ft (pre-tax)"), "a": a_sq.get("total_per_sqft") or 0,
                         "b": b_sq.get("total_per_sqft") or 0,
                         "delta": (b_sq.get("total_per_sqft") or 0) - (a_sq.get("total_per_sqft") or 0),
                         "pct": 0})
        return {"a": self.name, "b": b_doc.name, "rows": rows}

    def build_cost_breakup(self):
        """The same Material / Labor / Design / Overhead / Transport / Taxes
        bifurcation as on each SKU, aggregated for the whole estimate (client
        side; transport = this estimate's consolidated trips), plus per-sqft on
        the summed facial area. Draft only — a submitted estimate keeps its
        frozen JSON."""
        if self.docstatus != 0 or not self.meta.has_field("cost_breakup"):
            return
        from mallet_estimator.mallet_estimator.doctype.estimate_sku.estimate_sku import build_bifurcation
        buckets = getattr(self, "_client_buckets", None) or dict(material=0, labor=0, design=0, overhead=0)
        amounts = {
            "client_material": buckets["material"],
            "client_labor": buckets["labor"],
            "client_design": buckets["design"],
            "client_overhead": buckets["overhead"],
            "transport": float(self.total_transport or 0),
        }
        gst_pct = self.gst_pct if self.gst_pct is not None else 18
        bif = build_bifurcation(amounts, float(gst_pct or 18))
        sq = None
        sqft = float(getattr(self, "_sqft_sum", 0) or 0)
        if sqft:
            labor_side = amounts["client_labor"] + amounts["client_design"] + amounts["client_overhead"]
            sq = {
                "sqft": sqft,
                "material_per_sqft": amounts["client_material"] / sqft,
                "labor_per_sqft": labor_side / sqft,
                "total_per_sqft": (amounts["client_material"] + labor_side) / sqft,
            }
        rooms = []
        for rn in getattr(self, "_room_order", []) or []:
            g = self._room_groups[rn]
            g["per_sqft"] = (g["subtotal"] / g["sqft"]) if g["sqft"] else 0
            rooms.append(g)
        payload = {"bifurcation": bif, "sqft": sq, "rooms": rooms}
        # CSV-Nest consolidation story (prompt 219): what each SKU would cost
        # alone vs combined — the client-visible saving of estimating together.
        if getattr(self, "_consolidation", None):
            payload["consolidation"] = self._consolidation
        self.cost_breakup = json.dumps(payload)

    @frappe.whitelist()
    def create_quotation(self):
        if self.quotation and frappe.db.exists("Quotation", self.quotation):
            frappe.throw(_("Quotation {0} already exists for this estimate.").format(self.quotation))
        if not self.skus:
            frappe.throw(_("Add at least one SKU (link SKUs to this Project) before creating a Quotation."))

        quo = frappe.new_doc("Quotation")
        quo.quotation_to = "Customer"
        quo.party_name = self.customer
        quo.order_type = "Sales"
        if self.project:
            quo.project = self.project
        for row in self.skus:
            s = frappe.get_doc("Estimate SKU", row.estimate_sku)
            if not s.item:
                frappe.throw(_("SKU {0} has no linked Item. Open it, tick 'Create Item' and save.").format(s.name))
            quo.append("items", {
                "item_code": s.item,
                "qty": 1,
                "rate": s.client_total,
                "description": s.description or s.article_name,
            })
        # Native output-GST template on the quotation when seeded.
        from mallet_estimator.install import GST_SALES_TEMPLATE_TITLE
        st = frappe.db.get_value("Sales Taxes and Charges Template",
                                 {"title": GST_SALES_TEMPLATE_TITLE}, "name")
        if st:
            quo.taxes_and_charges = st
            quo.run_method("set_taxes")
        quo.insert(ignore_permissions=True)
        self.db_set("quotation", quo.name)
        return quo.name

    @frappe.whitelist()
    def build_boms(self):
        """Create a submitted BOM per SKU (materials + operations) so ERPNext can
        drive Work Orders and Job Cards. Native Sales Order -> Work Order takes it
        from here (it handles warehouses). Per-SKU errors are collected, not fatal."""
        company = _default_company()
        made, errors = [], []
        for row in self.skus:
            try:
                s = frappe.get_doc("Estimate SKU", row.estimate_sku)
                if not s.item:
                    errors.append(f"{s.name}: no linked Item")
                    continue
                made.append(_build_sku_bom(s, company))
            except Exception as exc:
                errors.append(f"{row.estimate_sku}: {exc}")
        return {"boms": made, "errors": errors}

    @frappe.whitelist()
    def create_work_orders(self):
        """Create a draft native Work Order per SKU from its BOM, linked to this
        Project (so material + labour actuals roll up to the Project Margin report).
        Submitting each Work Order — native ERPNext — generates the Job Cards, one
        per phase at its workstation. Per-SKU errors are collected, not fatal."""
        company = _default_company()
        abbr = frappe.db.get_value("Company", company, "abbr")

        def leaf_wh(name):
            full = f"{name} - {abbr}"
            return full if frappe.db.exists("Warehouse", full) else None

        wip = leaf_wh("Assembly Area")          # in-process stock
        fg = leaf_wh("Packed / Dispatch")       # finished good
        # Sales Order created from our Quotation (native), if any — links the WO to it.
        so = frappe.db.get_value("Sales Order Item", {"prevdoc_docname": self.quotation}, "parent") \
            if self.quotation else None

        made, errors = [], []
        for row in self.skus:
            try:
                s = frappe.get_doc("Estimate SKU", row.estimate_sku)
                if not s.item:
                    errors.append(f"{s.name}: no linked Item")
                    continue
                bom = frappe.db.get_value("BOM", {"item": s.item, "is_active": 1, "is_default": 1}, "name") \
                    or frappe.db.get_value("BOM", {"item": s.item, "is_active": 1}, "name")
                if not bom:
                    errors.append(f"{s.name}: no active BOM — click Build BOMs first")
                    continue
                wo = frappe.new_doc("Work Order")
                wo.production_item = s.item
                wo.bom_no = bom
                wo.qty = 1
                wo.company = company
                if self.project:
                    wo.project = self.project      # <- carries actuals to Project Margin
                if so:
                    wo.sales_order = so
                if wip:
                    wo.wip_warehouse = wip
                if fg:
                    wo.fg_warehouse = fg
                wo.insert(ignore_permissions=True)  # draft — user reviews + submits
                made.append(wo.name)
            except Exception as exc:
                errors.append(f"{row.estimate_sku}: {exc}")
        return {"work_orders": made, "errors": errors}


def _default_company():
    c = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")
    if not c:
        names = frappe.get_all("Company", pluck="name", limit=1)
        c = names[0] if names else None
    if not c:
        frappe.throw(_("No Company found. Create a Company first."))
    return c


def _ensure_operation(op_row):
    name = "Miscellaneous - extra" if getattr(op_row, "is_misc", 0) else (
        getattr(op_row, "operation", None) or getattr(op_row, "phase", None) or "")
    name = name.replace(" / ", " - ").replace("/", "-").strip()
    if name and not frappe.db.exists("Operation", name):
        o = frappe.new_doc("Operation")
        o.name = name
        o.workstation = op_row.workstation
        o.insert(ignore_permissions=True, set_name=name)
    return name


def _build_sku_bom(s, company):
    bom = frappe.new_doc("BOM")
    bom.item = s.item
    bom.company = company
    bom.quantity = 1
    bom.with_operations = 1
    bom.rm_cost_as_per = "Valuation Rate"
    # V3 — once an execution design exists, build the BOM from the CHOSEN actual
    # items (so Work Orders consume the real materials and Project margin reflects
    # actual cost). Before that, fall back to the estimate's generic materials.
    exec_rows = [r for r in (s.get("execution_materials") or []) if r.chosen_item]
    if exec_rows:
        for r in exec_rows:
            bom.append("items", {"item_code": r.chosen_item, "qty": r.actual_qty or 1, "rate": r.actual_rate or 0})
    else:
        for m in s.materials:
            if not m.item:
                continue
            bom.append("items", {"item_code": m.item, "qty": m.qty or 1, "rate": m.unit_cost or 0})
    if not bom.items:
        frappe.throw(_("SKU {0} has no priced material Items to put in a BOM.").format(s.name))
    for op in s.labor:
        if getattr(op, "is_misc", 0) and not s.include_misc:
            continue
        crew_min = (op.qty or 0) * (op.carp_min or 0)
        if crew_min <= 0:
            continue
        op_name = _ensure_operation(op)
        if op_name and op.workstation:
            bom.append("operations", {"operation": op_name, "workstation": op.workstation, "time_in_mins": crew_min})
    bom.insert(ignore_permissions=True)
    bom.submit()
    # make it the article's default BOM so native Work-Order creation finds it
    frappe.db.set_value("Item", s.item, "default_bom", bom.name, update_modified=False)
    return bom.name
