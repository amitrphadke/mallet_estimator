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


def annotate_dims(image_data, dims_text):
    """Stamp the outer dimensions onto the ISO render (bottom-left) so the image
    itself carries them. Returns new PNG bytes, or the original data if Pillow
    can't process it."""
    try:
        from PIL import Image, ImageDraw
        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        draw = ImageDraw.Draw(img)
        pad = max(img.width // 100, 6)
        # readable at any size: default font scaled by drawing on a strip
        strip_h = max(img.height // 18, 28)
        draw.rectangle([0, img.height - strip_h, img.width, img.height], fill=(47, 82, 51))
        try:
            from PIL import ImageFont
            font = ImageFont.load_default(size=int(strip_h * 0.55))
        except Exception:
            font = None
        draw.text((pad, img.height - strip_h + pad // 2), dims_text, fill=(255, 255, 255), font=font)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return image_data


def attach_iso_image(doc, views_pdf_content, dims=None):
    """Extract the IsoView render, stamp the outer dims on it when known, and
    attach it as a File. Returns the file_url (or None)."""
    result = extract_iso_image(views_pdf_content)
    if not result:
        return None
    filename, data = result
    if dims and all(dims.get(k) for k in ("w", "d", "h")):
        data = annotate_dims(data, f"W {dims['w']:g} x D {dims['d']:g} x H {dims['h']:g} mm")
        filename = "iso_view.png"
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
