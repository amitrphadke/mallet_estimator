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
# Summary edge-banding row: "EB_<code> / 1 mm x 22 mm <parts> <len> m<area> m²…"
# (numbers may run together after the unit). The spec sub-line ("b=YS_… Laminate")
# can push the numbers onto a FOLLOWING line.
_EDGE_ROW_RE = re.compile(r"^\s*\xa0?\s*(EB_[A-Za-z0-9_]+)\s*/\s*(.*)$")
_EDGE_NUMS_RE = re.compile(r"(\d+)\s+([0-9.]+)\s*m")


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


def parse_partlist_edges_text(text):
    """Edge banding from the Parts List PDF's Summary: [{code, parts, meters}].
    The 'Σ Rough Length' meters are the REAL banding need (the estimate PDF's
    edge section can be wrong/missing); parts = banded-edge count."""
    out, pending = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _EDGE_ROW_RE.match(line)
        if m:
            code, rest = m.group(1), m.group(2)
            nums = _EDGE_NUMS_RE.search(rest)
            if nums:
                out.append({"code": code, "parts": int(nums.group(1)), "meters": float(nums.group(2))})
                pending = None
            else:
                pending = code  # numbers wrapped below (spec sub-line in between)
            continue
        if pending:
            nums = _EDGE_NUMS_RE.search(line)
            if nums:
                out.append({"code": pending, "parts": int(nums.group(1)), "meters": float(nums.group(2))})
                pending = None
    return out


def parse_partlist_edges(content):
    """Edge banding rows from a Parts List PDF (bytes)."""
    return parse_partlist_edges_text(_pdf_text(content))


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


_MM_RE = re.compile(r"(\d{2,5})\s*mm")


def extract_outer_dims(content):
    """Outer W/D/H (mm) from the 7-Views dimension callouts:
      W = largest dim on the TopView (plan = width x depth),
      H = largest dim on the LeftView (elevation = height),
      D = second-largest dim on the LeftView (depth).
    Validated on YS_MB_WAR (1524 x 598 x 2060). Returns {w,d,h} with None for
    anything that can't be read — the user keys those in manually."""
    from pypdf import PdfReader
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    reader = PdfReader(io.BytesIO(content))
    views = {}
    for page in reader.pages:
        text = page.extract_text() or ""
        for v in ("TopView", "LeftView", "RightView", "FrontView"):
            if v in text:
                views[v] = sorted({int(n) for n in _MM_RE.findall(text)}, reverse=True)
    w = views.get("TopView", [None])[0] if views.get("TopView") else None
    side = views.get("LeftView") or views.get("RightView") or []
    h = side[0] if side else None
    d = side[1] if len(side) > 1 else None
    return {"w": w, "d": d, "h": h}


def attach_iso_image(doc, views_pdf_content, dims=None):
    """Extract the IsoView render and attach it as a File — the PLAIN image, no
    annotation (user 2026-08-02: the stamped dims were wrong; outer W/D/H live in
    their own fields instead). Returns the file_url (or None)."""
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
