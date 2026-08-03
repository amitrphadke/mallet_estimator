# S9 — décor slot mapping: the OCL placeholder codes (SG_LAM_V1_16mm_b_a,
# EB_PVC_EX_c …) stay the small fixed SketchUp palette; the REAL laminate /
# edge-band décor per slot is written in the OCL material Description using the
# B-C-N standard ("say it like you order it"):
#
#     b = Merino 1834 Moonlit Gray          (Name optional: "b = Merino 1834")
#     b = Merino 6534; c = RT 6575          (multi-slot, ';' or newline)
#
# The description flows into the estimate/part-list PDFs as a sub-line under the
# material row; import parses it, auto-creates the décor Item (LAMINATE_* for
# laminates, EBD_* for edge banding — structure only, rate keyed once on the
# price list) and fills the SKU's slot map. Legacy freeform descriptions still
# map (slug item, no manufacturer).

import re

_SLOT_DEF_RE = re.compile(r"^\s*([a-z])\s*=\s*([^;\n]+)", re.M)
_INLINE_SLOT_RE = re.compile(r"\b([a-z])\s*=\s*([^;\n]+)")
_LABEL_RE = re.compile(r"^\s*(brand|code|name|year)\s*=\s*(.+)$", re.I)
_PLACEHOLDER_RE = re.compile(r"^\s*(SG_LAM_[A-Za-z0-9_]+|SG_PLY_[A-Za-z0-9_]+|EB_[A-Za-z0-9_]+)")
_CODE_TOKEN_RE = re.compile(r"^[0-9][0-9A-Za-z\-]*$")

# Fallback maker names when the caller can't supply the live Manufacturer list
# (pure/unit contexts). At runtime the list comes from ERPNext's Manufacturer
# master — add a new maker there ONCE and the parser knows it (full name,
# squashed name, or its initials: "RT" = Royal Touch, "VM" = Virgo Mica).
DEFAULT_BRANDS = ("Merino", "Royal Touch", "Virgo Mica")


def brand_aliases(brands=None):
    """{alias(lower) -> canonical maker name} built from the maker list:
    full name, name without spaces, and initials for multi-word names."""
    out = {}
    for b in (brands or DEFAULT_BRANDS):
        b = (b or "").strip()
        if not b:
            continue
        out[b.lower()] = b
        squashed = b.replace(" ", "").lower()
        out.setdefault(squashed, b)
        words = b.split()
        if len(words) > 1:
            out.setdefault("".join(w[0] for w in words).lower(), b)
    return out


def _slug(text, maxlen=60):
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return s[:maxlen]


def parse_slot_value(value, brands=None):
    """Parse one slot's décor spec per M-C-N (Maker, Code, Name — name optional).
    `brands` = the live Manufacturer list (falls back to DEFAULT_BRANDS). Returns
    {brand, catalogue, name, raw}; brand None when unrecognised (legacy freeform
    keeps working via the raw text)."""
    raw = (value or "").strip()
    # a leaked "b=" / "c =" prefix must never enter the identity slug
    raw = re.sub(r"^[a-z]\s*=\s*", "", raw)
    tokens = raw.split()
    if not tokens:
        return None
    aliases = brand_aliases(brands)
    # maker may span up to three tokens ("Virgo Mica", "Royal Touch") or be initials
    brand = None
    rest = tokens
    for take in (3, 2, 1):
        cand = " ".join(tokens[:take]).lower()
        if cand in aliases:
            brand = aliases[cand]
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


def parse_description(desc, placeholder_code, brands=None):
    """Slot definitions from one material's description text. Two formats:

    LABELLED BLOCK (clear titles; Year optional; also maps slot 'a'):
        b = External Laminate
        Brand = Virgo Mica
        Code = 1834
        Name = Moonlight
        Year = 2025-26

    COMPACT one-liner: 'b = Virgo Mica 1834 Moonlight' (maker must be known).
    Prefixless legacy text still maps to the material's first slot."""
    out = {}
    lines = (desc or "").splitlines()
    current_slot, current_title, block = None, None, {}

    def close_block():
        nonlocal current_slot, current_title, block
        if block.get("brand"):
            aliases = brand_aliases(brands)
            block["brand"] = aliases.get(block["brand"].lower(), block["brand"])
        if current_slot and block.get("brand") or current_slot and block.get("code"):
            name = block.get("name", "")
            year = block.get("year", "")
            out[current_slot] = {
                "brand": block.get("brand"), "catalogue": block.get("code"),
                "name": name, "year": year, "title": current_title,
                "raw": " ".join(x for x in (block.get("brand"), block.get("code"), name,
                                            f"({year})" if year else "") if x),
            }
        current_slot, current_title, block = None, None, {}

    for line in lines:
        for seg in line.split(";"):
            seg = seg.strip()
            if not seg:
                continue
            lm = _LABEL_RE.match(seg)
            if lm and current_slot:
                block[lm.group(1).lower()] = lm.group(2).strip()
                continue
            sm = _INLINE_SLOT_RE.match(seg)
            if sm and len(sm.group(1)) == 1:
                close_block()
                slot, value = sm.group(1), sm.group(2).strip()
                parsed = parse_slot_value(value, brands)
                if parsed and parsed.get("brand"):
                    parsed["title"] = None
                    parsed.setdefault("year", "")
                    out[slot] = parsed          # compact form, maker recognised
                else:
                    current_slot, current_title = slot, value  # block header
                continue
    close_block()

    if not out and (desc or "").strip():
        slots = material_slots(placeholder_code)
        if slots:
            parsed = parse_slot_value(desc, brands)
            if parsed:
                parsed.setdefault("year", "")
                parsed.setdefault("title", None)
                out[slots[0]] = parsed
    return out


def extract_slot_map(pdf_text, brands=None):
    """Walk a PDF's text: a line starting with a placeholder material code opens a
    row; following non-numeric, non-material lines are its description. Returns
    [{placeholder, slot, brand, catalogue, name, raw}] in reading order."""
    results, seen = [], set()
    current, desc_lines = None, []

    def flush():
        if not current:
            return
        for slot, parsed in parse_description("\n".join(desc_lines), current, brands).items():
            key = (current, slot)
            if key in seen:
                continue
            seen.add(key)
            parsed.setdefault("year", "")
            parsed.setdefault("title", None)
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
        # labelled block / slot lines always belong to the description
        if _LABEL_RE.match(line) or _INLINE_SLOT_RE.match(line):
            desc_lines.append(line)
            continue
        # table/summary debris ends the block; unlabelled description text is at
        # most the two lines right under the material row
        if NOISE.search(line) or len([l for l in desc_lines if not _LABEL_RE.match(l)]) >= 2:
            flush()
            current, desc_lines = None, []
            continue
        desc_lines.append(line)
    flush()
    return results


def decor_item_code(placeholder, brand, catalogue, raw):
    """LAMINATE_/EBD_ + BRAND + catalogue (or a slug of the raw text). One item per
    real-world décor — reused across projects/SKUs."""
    prefix = "EBD" if str(placeholder or "").upper().startswith("EB") else "LAMINATE"
    if brand and catalogue:
        return f"{prefix}_{_slug(brand).upper()}_{catalogue}"
    return f"{prefix}_{_slug(raw)[:50]}"
