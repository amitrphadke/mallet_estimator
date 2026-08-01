# Estimation-phase PDF inputs beyond the Material Estimate:
#
#   • Part List PDF  — identifies hardware CORRECTLY: the estimate PDF only knows
#     the hardware GROUP (HWD_Hinge); the part list carries the real stock item
#     designation (HWD_AH_SC_0) with per-designation quantities.
#   • 7 Views PDF    — the SketchUp export whose page headed "IsoView" holds the
#     isometric render shown to the client on the estimate. The other six views
#     document the SKU's outer boundaries (execution reference).
#
# Both are parsed with pypdf (declared dep) + Pillow (core Frappe dep).

import io
import re

import frappe

# A hardware group heading is a lone HWD_ name on its own line (no row number).
_GROUP_RE = re.compile(r"^\s*\xa0?\s*(HWD_[A-Za-z0-9_]+)\s*$")
# A designation row: "<row no><designation>[#N] [qty]" — qty may wrap to a later line.
_ROW_RE = re.compile(r"^(\d+)\s*(HWD_[A-Za-z0-9_]+?)(#\d+)?\s*(\d+)?\s*$")
_QTY_RE = re.compile(r"^\s*(\d+)\s*$")


def _pdf_text(content):
    from pypdf import PdfReader
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_partlist_text(text):
    """Parse the hardware section of an OpenCutList Parts List PDF's text into
    [{code, qty, category}] — code is the canonical designation (no #N suffix),
    qty summed across instance rows, category the HWD_ group heading above it."""
    agg, order = {}, []
    category = None
    pending = None  # designation seen, qty wrapped onto a later line
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        g = _GROUP_RE.match(line)
        if g and not line[0].isdigit():
            category = g.group(1)
            continue
        m = _ROW_RE.match(line)
        if m:
            _no, code, _suffix, qty = m.groups()
            if qty is None:
                pending = code
                continue
            pending = None
            key = code
            if key not in agg:
                agg[key] = {"code": key, "qty": 0, "category": category or key}
                order.append(key)
            agg[key]["qty"] += int(qty)
            continue
        if pending:
            q = _QTY_RE.match(line)
            if q:
                key = pending
                if key not in agg:
                    agg[key] = {"code": key, "qty": 0, "category": category or key}
                    order.append(key)
                agg[key]["qty"] += int(q.group(1))
                pending = None
            # else: wrapped description noise between designation and qty — keep waiting
    return [agg[k] for k in order]


def parse_partlist_hardware(content):
    """Hardware designations from a Parts List PDF (bytes)."""
    return parse_partlist_text(_pdf_text(content))


def extract_iso_image(content):
    """Return (filename, png_bytes) of the isometric render: the largest embedded
    image on the 7-Views page whose text contains 'IsoView'. None if not found."""
    from pypdf import PdfReader
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    reader = PdfReader(io.BytesIO(content))
    for page in reader.pages:
        text = page.extract_text() or ""
        if "IsoView" not in text:
            continue
        best = None
        for img in page.images:
            if best is None or len(img.data) > len(best.data):
                best = img
        if best is None:
            return None
        ext = "png" if best.name.lower().endswith("png") else "jpg"
        return f"iso_view.{ext}", best.data
    return None


def attach_iso_image(doc, views_pdf_content):
    """Extract the IsoView render and attach it to the document as a File,
    returning the file_url (or None). Used to fill Estimate SKU.article_image."""
    result = extract_iso_image(views_pdf_content)
    if not result:
        return None
    filename, data = result
    f = frappe.get_doc({
        "doctype": "File",
        "file_name": f"{doc.name}_{filename}",
        "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name,
        "is_private": 0,
        "content": data,
    })
    f.save(ignore_permissions=True)
    return f.file_url
