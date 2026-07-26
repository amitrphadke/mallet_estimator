// Estimate SKU form: OpenCutList CSV import + convenience buttons.
frappe.ui.form.on("Estimate SKU", {
  refresh(frm) {
    frm.add_custom_button(__("Import OpenCutList CSV"), () => open_csv_dialog(frm), __("Material"));

    frm.add_custom_button(__("Re-seed standard steps"), () => {
      frappe.confirm(__("Replace the labor rows with the standard 16 + 1 steps?"), () => {
        frm.clear_table("labor");
        frm.refresh_field("labor");
        frm.save(); // server seeds the standard steps when labor is empty
      });
    }, __("Labor"));

    if (frm.doc.item) {
      frm.add_custom_button(__("Open Item"), () => frappe.set_route("Form", "Item", frm.doc.item));
    }
  },
});

// --- CSV parsing (mirrors the React app's csv.js) --------------------------
const FIELDS = [
  ["description", "Description / Part name"],
  ["material", "Material"],
  ["qty", "Quantity / Count"],
  ["length", "Length"],
  ["width", "Width"],
  ["thickness", "Thickness"],
  ["unit_cost", "Unit price"],
  ["line_cost", "Total price / cost"],
];
const HINTS = {
  description: ["description", "name", "part", "designation", "label", "item"],
  material: ["material", "matiere", "matière", "werkstoff"],
  qty: ["count", "quantity", "qty", "nombre", "anzahl", "pieces", "instances"],
  length: ["length", "longueur", "länge", "cutting length"],
  width: ["width", "largeur", "breite"],
  thickness: ["thickness", "epaisseur", "épaisseur", "dicke", "thick"],
  unit_cost: ["unit price", "unit cost", "prix unitaire", "unit"],
  line_cost: ["total cost", "total price", "cost", "price", "prix", "amount", "total"],
};

function detect_delim(line) {
  const counts = { ",": 0, ";": 0, "\t": 0 };
  let q = false;
  for (const c of line) {
    if (c === '"') q = !q;
    else if (!q && counts[c] !== undefined) counts[c]++;
  }
  const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  return best[1] > 0 ? best[0] : ",";
}

function parse_csv(text) {
  const clean = text.replace(/^﻿/, "");
  const delim = detect_delim((clean.split(/\r?\n/)[0]) || "");
  const rows = [];
  let field = "", row = [], inQ = false;
  for (let i = 0; i < clean.length; i++) {
    const c = clean[i];
    if (inQ) {
      if (c === '"') { if (clean[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === delim) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && clean[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((v) => v.trim() !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); if (row.some((v) => v.trim() !== "")) rows.push(row); }
  return rows;
}

function auto_map(headers) {
  const used = new Set();
  return headers.map((h) => {
    const low = String(h).toLowerCase().trim();
    let best = null, score = 0;
    for (const [key, words] of Object.entries(HINTS)) {
      if (used.has(key)) continue;
      for (const w of words) {
        if (low === w && 100 > score) { best = key; score = 100; }
        else if (low.includes(w) && w.length > score) { best = key; score = w.length; }
      }
    }
    if (best) used.add(best);
    return best;
  });
}

function to_num(v) {
  if (v == null) return 0;
  let s = String(v).replace(/[^\d.,-]/g, "");
  if (s.includes(",") && s.includes(".")) {
    s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
  } else if (s.includes(",")) {
    s = /,\d{1,2}$/.test(s) ? s.replace(",", ".") : s.replace(/,/g, "");
  }
  const n = parseFloat(s);
  return isFinite(n) ? n : 0;
}

function open_csv_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __("Import OpenCutList CSV"),
    fields: [
      { fieldname: "file", fieldtype: "Attach", label: __("CSV file") },
      { fieldname: "or", fieldtype: "HTML", options: `<div class="text-muted small">${__("…or paste the CSV text below")}</div>` },
      { fieldname: "text", fieldtype: "Code", label: __("CSV text") },
      { fieldname: "replace", fieldtype: "Check", label: __("Replace existing material lines"), default: 1 },
    ],
    primary_action_label: __("Import"),
    primary_action(values) {
      const done = (text) => { import_text(frm, text, values.replace); d.hide(); };
      if (values.text) return done(values.text);
      if (values.file) {
        fetch(values.file).then((r) => r.text()).then(done).catch(() => frappe.msgprint(__("Could not read the file.")));
      } else {
        frappe.msgprint(__("Attach a file or paste CSV text."));
      }
    },
  });
  d.show();
}

function import_text(frm, text, replace) {
  const rows = parse_csv(text);
  if (!rows.length) return frappe.msgprint(__("That file looks empty."));
  const mapping = auto_map(rows[0]);
  const idx = {};
  mapping.forEach((f, i) => { if (f) idx[f] = i; });
  if (replace) frm.clear_table("materials");
  let n = 0;
  rows.slice(1).forEach((r) => {
    const get = (k) => (idx[k] != null ? r[idx[k]] : "");
    const qty = idx.qty != null ? to_num(get("qty")) : 1;
    const unit = idx.unit_cost != null ? to_num(get("unit_cost")) : 0;
    let line = idx.line_cost != null ? to_num(get("line_cost")) : 0;
    if (!line && unit) line = unit * (qty || 1);
    const desc = String(get("description") || "").trim();
    const material = String(get("material") || "").trim();
    if (!desc && !material && !line) return;
    const row = frm.add_child("materials");
    row.description = desc;
    row.material = material;
    row.qty = qty || 1;
    row.length = idx.length != null ? to_num(get("length")) : 0;
    row.width = idx.width != null ? to_num(get("width")) : 0;
    row.thickness = idx.thickness != null ? to_num(get("thickness")) : 0;
    row.unit_cost = unit;
    row.line_cost = line;
    n++;
  });
  frm.refresh_field("materials");
  frappe.show_alert({ message: __("Imported {0} material lines", [n]), indicator: "green" });
  frm.dirty();
}
