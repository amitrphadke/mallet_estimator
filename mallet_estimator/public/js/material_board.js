// ---------------------------------------------------------------------------
// The material board: an SKU's material lines, grouped by what they are, with
// the whole money chain on ONE row and the décor sitting on the line it
// belongs to. Rendered identically on the Estimate SKU form and inside the
// Estimate screen — one implementation, so the two can never drift.
//
// Why not the desk grid: its columns are the same for every row, so a ply
// sheet and a hinge shared a header and the money chain scrolled sideways or
// hid inside a row editor. Here each group carries only the columns that mean
// something for it, and nothing is behind a click.
//
// Editing: typing recomputes the row, its group and the footer immediately —
// you watch the totals move. The change is then written through
// save_material_edits, which SAVES the SKU and returns the board the save
// produced, so the optimistic numbers are replaced by stored ones a moment
// later. The server is always the authority; the local math only buys the
// half-second.
// ---------------------------------------------------------------------------
frappe.provide("mallet");

mallet.MaterialBoard = class MaterialBoard {
  constructor(opts) {
    this.$wrapper = opts.wrapper;
    this.sku = opts.sku;
    this.editable = opts.editable !== false;
    // Called after a save so the host form can refresh its own totals.
    this.on_change = opts.on_change || (() => {});
    this.pending = new Map(); // row -> {field: value} not yet written
    this.saving = false;
  }

  load() {
    this.$wrapper.html(`<div class="text-muted">${__("Loading…")}</div>`);
    return frappe
      .call({ method: "mallet_estimator.api.material_board", args: { sku: this.sku } })
      .then((r) => {
        this.data = (r && r.message) || null;
        this.render();
      });
  }

  // --- money, mirrored from price_material_lines ---------------------------
  // Kept deliberately small and in one place: if it ever disagrees with the
  // server the next save corrects it on screen, so this is a preview, not a
  // second implementation of the pricing rules.
  recompute(line) {
    const qty = flt(line.qty);
    const rate = flt(line.unit_cost);
    const disc = Math.min(100, Math.max(0, flt(line.discount_pct)));
    line.net_rate = rate * (1 - disc / 100);
    line.discount_amount = qty * rate * (disc / 100);
    line.line_cost = qty * line.net_rate;
    const policy = flt(line.tax_rate_policy);
    const applied = line.tax_rate === "" || line.tax_rate === null || line.tax_rate === undefined
      ? policy
      : flt(line.tax_rate);
    line.tax_discount_pct = policy - applied;
    line.tax_amount = line.line_cost * (applied / 100);
    line.tax_saved = line.line_cost * ((policy - applied) / 100);
    line.amount_with_tax = line.line_cost + line.tax_amount;
  }

  retotal() {
    const t = { taxable: 0, tax: 0, landed: 0, discount: 0, tax_saved: 0, client_supplied: 0 };
    (this.data.groups || []).forEach((g) => {
      Object.keys(t).forEach((k) => (g[k] = 0));
      (g.lines || []).forEach((line) => {
        this.recompute(line);
        if (line.customer_supplied) {
          g.client_supplied += line.amount_with_tax;
        } else {
          g.taxable += line.line_cost;
          g.tax += line.tax_amount;
          g.landed += line.amount_with_tax;
          g.discount += line.discount_amount;
          g.tax_saved += line.tax_saved;
        }
      });
      Object.keys(t).forEach((k) => (t[k] += g[k]));
    });
    Object.assign(this.data.totals, t);
  }

  // --- rendering -----------------------------------------------------------
  render() {
    const d = this.data;
    if (!d) {
      this.$wrapper.html(`<div class="text-muted">${esc(__("Nothing to show."))}</div>`);
      return;
    }
    if (!(d.groups || []).length) {
      this.$wrapper.html(
        `<div class="text-muted">${esc(
          __("No material lines yet — attach this SKU's Part List CSV (or Material Estimate PDF)."))}</div>`);
      return;
    }
    this.$wrapper.html(
      this.header() +
      `<div class="mallet-board">${d.groups.map((g) => this.group(g)).join("")}</div>` +
      this.footer()
    );
    this.bind();
  }

  header() {
    const d = this.data;
    const flags =
      (d.frozen ? ` <span class="badge">${esc(__("frozen"))}</span>` : "") +
      (d.unpriced
        ? ` <span class="badge" style="background:#e24c4c;color:#fff">${esc(__("unpriced"))}</span>`
        : "");
    const unmapped = d.unmapped
      ? `<span class="text-danger">${esc(__("{0} line(s) still generic", [d.unmapped]))}</span>`
      : `<span class="text-muted">${esc(__("all décor mapped"))}</span>`;
    return `<div class="mallet-board-head" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px">
      <b>${esc(d.article)}</b>
      <span class="text-muted small">${esc(d.code)} · ${esc(d.room)} · ${esc(d.mode)}</span>${flags}
      <span style="flex:1"></span>
      ${unmapped}
      ${this.editable && !d.frozen
        ? `<button class="btn btn-xs btn-default mallet-apply-decor">${esc(__("Apply décor"))}</button>`
        : ""}
      <a class="small" href="/app/estimate-sku/${encodeURIComponent(d.sku)}">${esc(__("open SKU"))}</a>
      <span class="mallet-board-status text-muted small"></span>
    </div>`;
  }

  group(g) {
    const shape = g.shape || {};
    const cols = [];
    cols.push(`<th style="min-width:220px">${esc(__("Code / description"))}</th>`);
    cols.push(`<th>${esc(__("Item"))}</th>`);
    if (shape.dims) cols.push(`<th class="text-right">${esc(__("Thk"))}</th>`);
    if (shape.decor) {
      cols.push(`<th style="min-width:180px">${esc(shape.decor === 2 ? __("Décor — internal") : __("Décor"))}</th>`);
      if (shape.decor === 2) cols.push(`<th style="min-width:180px">${esc(__("Décor — external"))}</th>`);
    }
    cols.push(`<th class="text-right">${esc(__("Qty"))}</th>`);
    cols.push(`<th>${esc(__("UOM"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("MRP"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Disc %"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Net"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Taxable"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Std %"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Applied %"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Tax"))}</th>`);
    cols.push(`<th class="text-right">${esc(__("Landed"))}</th>`);
    cols.push(`<th class="text-center" title="${esc(__("Client-supplied — priced, but never our cost"))}">${esc(__("Client"))}</th>`);
    const span = cols.length - 4;
    return `<table class="table table-bordered mallet-board-table" data-group="${esc(g.group)}"
                   style="font-size:12px;margin:0 0 10px 0;width:100%;table-layout:auto">
      <thead>
        <tr style="background:#f4f5f6"><th colspan="${cols.length}" style="font-size:12px">
          <b>${esc(g.group)}</b>
          <span class="text-muted"> · ${g.lines.length} ${esc(__("line(s)"))}</span>
        </th></tr>
        <tr>${cols.join("")}</tr>
      </thead>
      <tbody>${g.lines.map((line) => this.row(line, shape)).join("")}</tbody>
      <tfoot><tr class="mallet-group-foot" data-group="${esc(g.group)}">
        <td colspan="${span}" class="text-right"><b>${esc(__("Group total"))}</b></td>
        <td class="text-right"><b class="g-taxable">${format_currency(g.taxable)}</b></td>
        <td colspan="2"></td>
        <td class="text-right g-tax">${format_currency(g.tax)}</td>
        <td class="text-right"><b class="g-landed">${format_currency(g.landed)}</b></td>
        <td class="text-right g-client small text-muted">${
          g.client_supplied ? format_currency(g.client_supplied) : ""}</td>
      </tr></tfoot>
    </table>`;
  }

  decor_cell(line, field, domain) {
    const options = (this.data.decor_options || {})[domain] || [];
    const current = line[field] || "";
    // No masters to pick from — an empty dropdown reads as a broken feature.
    // Point at the slot table, which is the path that always works.
    if (!options.length && !current) {
      return `<td class="small text-muted" title="${esc(
        __("No Mallet Decor master of this kind exists yet. Use the Décor Slots table below, or create a Mallet Decor record."))}">${
        esc(__("— use Décor Slots —"))}</td>`;
    }
    if (!this.editable || this.data.frozen) {
      const hit = options.find((o) => o.value === current);
      return `<td>${esc(hit ? hit.label : current)}</td>`;
    }
    const opts = [`<option value="">${esc(__("— none —"))}</option>`]
      .concat(options.map((o) =>
        `<option value="${esc(o.value)}"${o.value === current ? " selected" : ""}>${esc(o.label)}</option>`))
      .join("");
    return `<td><select class="form-control input-xs mallet-decor" data-row="${esc(line.row)}"
              data-field="${esc(field)}" style="height:24px;padding:1px 4px;font-size:11px">${opts}</select></td>`;
  }

  num_cell(line, field, cls) {
    if (!this.editable || this.data.frozen) {
      return `<td class="text-right">${format_number(flt(line[field]), null, 2)}</td>`;
    }
    const value = line[field] === null || line[field] === undefined ? "" : line[field];
    return `<td class="text-right"><input type="number" step="0.01" class="form-control input-xs mallet-num ${cls}"
      data-row="${esc(line.row)}" data-field="${esc(field)}" value="${esc(value)}"
      style="height:24px;padding:1px 4px;font-size:11px;text-align:right"></td>`;
  }

  row(line, shape) {
    const cells = [];
    cells.push(`<td><code>${esc(line.material)}</code>${
      line.manual ? ` <span class="badge">${esc(__("manual"))}</span>` : ""}
      <div class="small text-muted">${esc(line.description)}</div></td>`);
    cells.push(`<td class="small">${esc(line.item)}</td>`);
    if (shape.dims) cells.push(`<td class="text-right">${line.thickness ? line.thickness : ""}</td>`);
    if (shape.decor) {
      const domain = /^EB_/i.test(line.material) ? "Edge Band" : "Laminate";
      cells.push(this.decor_cell(line, "decor", domain));
      if (shape.decor === 2) cells.push(this.decor_cell(line, "decor_ext", "Laminate"));
    }
    cells.push(`<td class="text-right">${format_number(line.qty, null, 2)}</td>`);
    cells.push(`<td class="small">${esc(line.uom)}</td>`);
    cells.push(`<td class="text-right">${format_currency(line.unit_cost)}</td>`);
    cells.push(this.num_cell(line, "discount_pct", "f-disc"));
    cells.push(`<td class="text-right c-net">${format_currency(line.net_rate)}</td>`);
    cells.push(`<td class="text-right c-taxable"><b>${format_currency(line.line_cost)}</b></td>`);
    // The policy rate is never editable — it is what the item says. Overriding
    // happens in the next column, which is exactly why both are on screen.
    cells.push(`<td class="text-right text-muted" title="${esc(
      __("The item's standard rate (Item.mallet_gst_pct, else the house GST%)"))}">${
      format_number(line.tax_rate_policy, null, 2)}%</td>`);
    cells.push(this.num_cell(line, "tax_rate", "f-tax"));
    cells.push(`<td class="text-right c-tax">${format_currency(line.tax_amount)}
      <div class="small text-muted c-saved">${
        line.tax_saved ? "−" + format_currency(line.tax_saved) : ""}</div></td>`);
    cells.push(`<td class="text-right c-landed"><b>${format_currency(line.amount_with_tax)}</b></td>`);
    cells.push(`<td class="text-center"><input type="checkbox" class="mallet-client"
      data-row="${esc(line.row)}" data-field="customer_supplied"${
      line.customer_supplied ? " checked" : ""}${
      this.editable && !this.data.frozen ? "" : " disabled"}></td>`);
    return `<tr data-row="${esc(line.row)}"${
      line.customer_supplied ? ' style="background:#fbfbf4"' : ""}>${cells.join("")}</tr>`;
  }

  footer() {
    const t = this.data.totals || {};
    const cell = (label, value, strong) =>
      `<div style="min-width:150px"><div class="text-muted small">${esc(label)}</div>
       <div${strong ? ' style="font-weight:600"' : ""}>${format_currency(value)}</div></div>`;
    return `<div class="mallet-board-foot" style="display:flex;gap:18px;flex-wrap:wrap;
              padding:8px 10px;border:1px solid #d1d8dd;background:#fafbfc">
      ${cell(__("Taxable"), t.taxable, true)}
      ${cell(__("Discount"), t.discount)}
      ${cell(__("Tax"), t.tax)}
      ${cell(__("Tax saved"), t.tax_saved)}
      ${cell(__("Landed"), t.landed, true)}
      ${cell(__("Client-supplied (not our cost)"), t.client_supplied)}
      <div style="min-width:150px"><div class="text-muted small">${esc(__("Man-days"))}</div>
        <div>${format_number(flt(t.est_days), null, 2)}</div></div>
    </div>`;
  }

  // --- editing -------------------------------------------------------------
  find(row) {
    for (const g of this.data.groups || []) {
      const hit = (g.lines || []).find((l) => l.row === row);
      if (hit) return { line: hit, group: g };
    }
    return {};
  }

  paint(row) {
    const { line, group } = this.find(row);
    if (!line) return;
    const $tr = this.$wrapper.find(`tr[data-row="${row}"]`);
    $tr.find(".c-net").text(format_currency(line.net_rate));
    $tr.find(".c-taxable").html(`<b>${format_currency(line.line_cost)}</b>`);
    $tr.find(".c-tax").contents().first().replaceWith(format_currency(line.tax_amount) + " ");
    $tr.find(".c-saved").text(line.tax_saved ? "−" + format_currency(line.tax_saved) : "");
    $tr.find(".c-landed").html(`<b>${format_currency(line.amount_with_tax)}</b>`);
    $tr.css("background", line.customer_supplied ? "#fbfbf4" : "");
    const $foot = this.$wrapper.find(`.mallet-group-foot[data-group="${group.group}"]`);
    $foot.find(".g-taxable").text(format_currency(group.taxable));
    $foot.find(".g-tax").text(format_currency(group.tax));
    $foot.find(".g-landed").text(format_currency(group.landed));
    $foot.find(".g-client").text(group.client_supplied ? format_currency(group.client_supplied) : "");
    this.$wrapper.find(".mallet-board-foot").replaceWith(this.footer());
  }

  edit(row, field, value) {
    const { line } = this.find(row);
    if (!line) return;
    line[field] = value;
    this.retotal();
    this.paint(row);
    const queued = this.pending.get(row) || { row };
    queued[field] = value;
    this.pending.set(row, queued);
    this.flush_soon();
  }

  flush_soon() {
    clearTimeout(this._timer);
    this.status(__("editing…"));
    // Long enough that typing a two-digit percentage is one save, short
    // enough that you never wonder whether it took.
    this._timer = setTimeout(() => this.flush(), 900);
  }

  status(text, colour) {
    this.$wrapper.find(".mallet-board-status")
      .text(text || "")
      .css("color", colour || "");
  }

  flush() {
    if (!this.pending.size || this.saving) return;
    const changes = Array.from(this.pending.values());
    this.pending.clear();
    this.saving = true;
    this.status(__("saving…"));
    frappe
      .call({
        method: "mallet_estimator.api.save_material_edits",
        args: { sku: this.sku, changes: JSON.stringify(changes) },
      })
      .then((r) => {
        this.saving = false;
        if (r && r.message) {
          this.data = r.message;
          this.render();
          this.status(__("saved"), "#1f7aec");
          setTimeout(() => this.status(""), 2000);
          this.on_change(this.data);
        }
        if (this.pending.size) this.flush();
      })
      .catch(() => {
        this.saving = false;
        // The save failed and the screen is showing optimistic numbers — say
        // so and put the stored ones back rather than leaving a quiet lie.
        this.status(__("not saved — reloading"), "#e24c4c");
        this.load();
      });
  }

  bind() {
    const self = this;
    this.$wrapper.find(".mallet-num").on("change", function () {
      const v = $(this).val();
      self.edit($(this).data("row"), $(this).data("field"), v === "" ? null : flt(v));
    });
    this.$wrapper.find(".mallet-decor").on("change", function () {
      self.edit($(this).data("row"), $(this).data("field"), $(this).val() || null);
      // A décor change only shows up on the line once it is re-pointed, which
      // the save does — so push it through immediately instead of waiting.
      self.flush();
    });
    this.$wrapper.find(".mallet-client").on("change", function () {
      self.edit($(this).data("row"), "customer_supplied", this.checked ? 1 : 0);
    });
    this.$wrapper.find(".mallet-apply-decor").on("click", () => {
      this.status(__("applying…"));
      frappe
        .call({ method: "mallet_estimator.api.apply_decor", args: { sku: this.sku } })
        .then((r) => {
          if (r && r.message) {
            this.data = r.message;
            this.render();
            frappe.show_alert({
              message: this.data.unmapped
                ? __("Applied — {0} line(s) still generic", [this.data.unmapped])
                : __("Applied — every line now carries a real item"),
              indicator: this.data.unmapped ? "orange" : "green",
            });
            this.on_change(this.data);
          }
        });
    });
  }
};

function esc(s) {
  const v = s === null || s === undefined ? "" : String(s);
  return frappe.utils.escape_html ? frappe.utils.escape_html(v) : v;
}
