# S9 — décor slot mapping: the OCL placeholder codes (SG_LAM_V1_16mm_b_a,
# EB_PVC_EX_c …) stay the small fixed SketchUp palette; the REAL laminate /
# edge-band décor per slot is written in the OCL material Description using the
# B-C-N standard ("say it like you order it"):
#
#     b = Merino 1834 Moonlit Gray          (Name optional: "b = Merino 1834")
#     b = Merino 6534; c = RT 6575          (multi-slot, ';' or newline)
#
# The description flows into the estimate/part-list PDFs as a sub-line under the
# material row; import parses it, auto-creates the décor Item (LAMD_* for
# laminates, EBD_* for edge banding — structure only, rate keyed once on the
# price list) and fills the SKU's slot map. Legacy freeform descriptions still
# map (slug item, no manufacturer).

import re

_SLOT_DEF_RE = re.compile(r"\b([b-z])\s*=\s*([^;\n]+)")
_PLACEHOLDER_RE = re.compile(r"^\s*(SG_LAM_[A-Za-z0-9_]+|SG_PLY_[A-Za-z0-9_]+|EB_[A-Za-z0-9_]+)")
_CODE_TOKEN_RE = re.compile(r"^[0-9][0-9A-Za-z\-]*$")

# Manufacturer aliases (case-insensitive) -> ERPNext Manufacturer name.
BRAND_ALIASES = {
    "merino": "Merino",
    "royal touch": "Royal Touch",
    "royaltouch": "Royal Touch",
    "rt": "Royal Touch",
}


def _slug(text, maxlen=60):
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return s[:maxlen]


def parse_slot_value(value):
    """Parse one slot's décor spec per B-C-N (Brand, Code, Name — name optional).
    Returns {brand, catalogue, name, raw}; brand None when unrecognised (legacy
    freeform keeps working via the raw text)."""
    raw = (value or "").strip()
    tokens = raw.split()
    if not tokens:
        return None
    # brand may be one token ("Merino", "RT") or two ("Royal Touch")
    brand = None
    rest = tokens
    for take in (2, 1):
        cand = " ".join(tokens[:take]).lower()
        if cand in BRAND_ALIASES:
            brand = BRAND_ALIASES[cand]
            rest = tokens[take:]
            break
    catalogue = None
    if rest and _CODE_TOKEN_RE.match(rest[0]):
        catalogue = rest[0]
        rest = rest[1:]
    return {"brand": brand, "catalogue": catalogue, "name": " ".join(rest).strip(), "raw": raw}


def material_slots(code):
    """The placeholder letters a material code carries (non-'a'): SG_PLY_V2_b_c →
    ['b','c']; SG_LAM_V1_16mm_b_a → ['b']; EB_PVC_EX_c → ['c']."""
    tokens = str(code or "").split("_")
    return [t for t in tokens if len(t) == 1 and t.isalpha() and t.islower() and t != "a"]


def parse_description(desc, placeholder_code):
    """Slot definitions from one material's description text. Explicit 'b=…'
    entries win; prefixless legacy text maps to the material's FIRST slot."""
    out = {}
    for slot, value in _SLOT_DEF_RE.findall(desc or ""):
        parsed = parse_slot_value(value)
        if parsed:
            out[slot] = parsed
    if not out and (desc or "").strip():
        slots = material_slots(placeholder_code)
        if slots:
            parsed = parse_slot_value(desc)
            if parsed:
                out[slots[0]] = parsed
    return out


def extract_slot_map(pdf_text):
    """Walk a PDF's text: a line starting with a placeholder material code opens a
    row; following non-numeric, non-material lines are its description. Returns
    [{placeholder, slot, brand, catalogue, name, raw}] in reading order."""
    results, seen = [], set()
    current, desc_lines = None, []

    def flush():
        if not current:
            return
        for slot, parsed in parse_description("\n".join(desc_lines), current).items():
            key = (current, slot)
            if key in seen:
                continue
            seen.add(key)
            results.append({"placeholder": current, "slot": slot, **parsed})

    NOISE = re.compile(r"m\u00b2|m\u00b3|\bRs\b|\bQty\b|Designation|\d+\s*mm\b|^No\.|^Total|^\d[\d\s.,]*$", re.I)
    for line in pdf_text.splitlines():
        line = re.sub(r"[\x00-\x1f\xa0]", " ", line).strip()
        if not line:
            continue
        m = _PLACEHOLDER_RE.match(line)
        if m:
            flush()
            current, desc_lines = m.group(1), []
            # description may share the row line after the spec ("... b=...")
            tail = line[m.end():]
            if "=" in tail:
                eq = tail.find("=")
                desc_lines.append(tail[max(0, eq - 2):])
            continue
        if current is None:
            continue
        # table/summary debris ends the block; a description is at most the two
        # lines right under the material row
        if NOISE.search(line) or len(desc_lines) >= 2:
            flush()
            current, desc_lines = None, []
            continue
        desc_lines.append(line)
    flush()
    return results


def decor_item_code(placeholder, brand, catalogue, raw):
    """LAMD_/EBD_ + BRAND + catalogue (or a slug of the raw text). One item per
    real-world décor — reused across projects/SKUs."""
    prefix = "EBD" if str(placeholder or "").upper().startswith("EB") else "LAMD"
    if brand and catalogue:
        return f"{prefix}_{_slug(brand).upper()}_{catalogue}"
    return f"{prefix}_{_slug(raw)[:50]}"
