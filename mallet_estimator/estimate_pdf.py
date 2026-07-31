# ---------------------------------------------------------------------------
# OpenCutList "Estimate" PDF parser.
#
# The estimate PDF is OpenCutList's authoritative material report (accurate
# sheet counts from its real nesting, plus hardware counts). We extract the
# per-material QUANTITIES from it; prices come from the ERPNext Item rate card /
# inventory, not from the PDF. Sections: Sheet goods, Veneer (laminate), Edge
# banding, Hardware. Rows are complete on one line, or wrap after a material
# description note — both are handled.
# ---------------------------------------------------------------------------

import re
from io import BytesIO

SECTION_KIND = {
    "Sheet goods": "sheet",
    "Veneer": "laminate",
    "Edge banding": "edge",
    "Hardware": "hardware",
    "Solid wood": "solidwood",
    "Dimensional": "dimensional",
}
_MAT = re.compile(r"^(SG_|HWD_|EB_|DL_|SW_)\S")
_SPEC = re.compile(r"\s*\d+(?:\.\d+)?\s*mm(?:\s*x\s*\d+(?:\.\d+)?\s*mm)?")


def read_pdf_text(content):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(content))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def parse_estimate_pdf(text):
    """Return a list of {name, thickness, qty, kind} from the estimate PDF text."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    section = None
    out = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i]
        if s in SECTION_KIND:
            section = SECTION_KIND[s]
            i += 1
            continue
        if section and _MAT.match(s) and not s.startswith("Total"):
            if " / " in s:
                name = s.split(" / ", 1)[0].strip()
                rest = s.split(" / ", 1)[1]
                sm = _SPEC.match(rest)
                thickness = float(re.search(r"\d+(?:\.\d+)?", rest).group()) if sm else 0
                tail = rest[sm.end():] if sm else rest
            else:
                name = s.split()[0]
                thickness = 0
                tail = s[len(name):]
            # Append following DATA lines (start with a digit); skip description notes.
            j = i + 1
            while j < n:
                nx = lines[j]
                if _MAT.match(nx) or nx.startswith("Total") or nx in SECTION_KIND:
                    break
                if re.match(r"^\d", nx):
                    tail += " " + nx
                j += 1
            qm = re.search(r"\d+", tail)
            if qm:
                out.append({"name": name, "thickness": thickness, "qty": int(qm.group()), "kind": section})
            i = j
            continue
        i += 1
    return out


def _hw(materials):
    """Sum hardware quantities by distinctive substring of the material name."""
    def total(sub):
        return sum(m["qty"] for m in materials if m["kind"] == "hardware" and sub in m["name"].lower())
    return {
        "minifix": total("minifix"),
        "screws": total("screw"),
        "hinges": total("hinge"),
        "rails": total("rail"),
        "handles": total("handle"),
        "tower": total("tower"),
        "shelf": total("shelf"),
    }


def sheet_goods_sheets(materials):
    """Full plywood sheets from the 'Sheet goods' table only (excludes veneer/
    laminate). This is what gets cut/taped/laminated."""
    return sum(m["qty"] for m in materials if m["kind"] == "sheet")


def operation_quantities(materials, part_count):
    """Compute each operation's Qty from the estimate PDF quantities + the parts
    count. Follows the fixed mapping (locked ops 1-4; the rest are editable
    defaults). Returns {operation_name: qty}."""
    hw = _hw(materials)
    sg = sheet_goods_sheets(materials)
    assembly = 1 + hw["rails"]
    q = {
        "Sheet Lamination": sg,
        "Sheet Tape Removal": sg,
        "Sheet Cutting": sg,
        "Edge Banding": part_count,
        "Minifix Boring": hw["minifix"],
        "Drilling": hw["screws"] + hw["hinges"],
        "Grooving": 2,
        "Assembly": assembly,
        "Install Hardware": hw["hinges"] + hw["rails"] + hw["handles"] + hw["tower"] + hw["shelf"],
        "Disassembly": assembly,
        "Miscellaneous - extra": 0,
    }
    q["Packing"] = q["Disassembly"]
    q["Loading"] = q["Packing"]
    q["Transport"] = q["Loading"]
    q["Unloading"] = q["Transport"]
    q["Assembly (on-site)"] = assembly
    q["Installation"] = q["Assembly (on-site)"]
    return q


# Operations whose Qty is fully computed and must not be edited.
LOCKED_OPERATIONS = {"Sheet Lamination", "Sheet Tape Removal", "Sheet Cutting", "Edge Banding"}
