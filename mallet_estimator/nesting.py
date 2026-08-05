# ---------------------------------------------------------------------------
# Nest Estimate — Phase 1 engine (pure module, no app imports, no side effects).
#
# Estimates how many stock sheets a set of rectangular parts needs, the same
# question OpenCutList's packer answers inside SketchUp. This is NOT a copy of
# OCL's engine (Packy/packingsolver, C++) — it is a MaxRects best-short-side-fit
# heuristic calibrated against the shop's real OCL exports; kerf/trim live in
# the caller so calibration can tune them. Deterministic on purpose.
# ---------------------------------------------------------------------------

import math

SHEET_L = 2440.0
SHEET_W = 1220.0
EDGE_ROLL_M = 50.0


def pack_sheets(parts, sheet=(SHEET_L, SHEET_W), kerf=4.0, trim=10.0, allow_rotate=True):
    """Number of sheets needed for `parts` = [(length_mm, width_mm), ...]
    (already expanded by quantity). MaxRects, best-short-side-fit, guillotine
    split. Returns {"sheets": n, "utilization": 0..1, "placed": count,
    "too_big": [parts that cannot fit a sheet at all]}.

    kerf is added to each part's footprint; trim shrinks the usable sheet on
    all sides. Both are CALIBRATION knobs — tune until per-SKU counts match
    the shop's real OCL PDFs, then trust combined predictions."""
    usable_l = sheet[0] - 2 * trim
    usable_w = sheet[1] - 2 * trim
    todo = []
    too_big = []
    for (l, w) in parts:
        pl, pw = float(l) + kerf, float(w) + kerf
        fits = (pl <= usable_l and pw <= usable_w) or \
               (allow_rotate and pw <= usable_l and pl <= usable_w)
        (todo if fits else too_big).append((pl, pw))
    # big-first gives MaxRects its best shot
    todo.sort(key=lambda p: (max(p), p[0] * p[1]), reverse=True)

    sheets = []  # each sheet = list of free rects [x, y, l, w]
    used_area = 0.0

    def try_place(free, pl, pw):
        """Best-short-side-fit across free rects; returns (idx, l, w) or None."""
        best = None
        for i, (fx, fy, fl, fw) in enumerate(free):
            for (l, w) in ((pl, pw), (pw, pl)) if allow_rotate else ((pl, pw),):
                if l <= fl and w <= fw:
                    score = min(fl - l, fw - w)
                    if best is None or score < best[0]:
                        best = (score, i, l, w)
        return best and best[1:]

    def place(free, i, l, w):
        fx, fy, fl, fw = free.pop(i)
        # guillotine split along the longer leftover axis
        right = (fx + l, fy, fl - l, w)
        top = (fx, fy + w, fl, fw - w)
        for r in (right, top):
            if r[2] > 0 and r[3] > 0:
                free.append(r)

    for (pl, pw) in todo:
        for free in sheets:
            hit = try_place(free, pl, pw)
            if hit:
                place(free, *hit)
                break
        else:
            free = [(0.0, 0.0, usable_l, usable_w)]
            hit = try_place(free, pl, pw)
            if hit:
                place(free, *hit)
                sheets.append(free)
        used_area += pl * pw
    n = len(sheets)
    cap = n * usable_l * usable_w
    return {
        "sheets": n,
        "utilization": (used_area / cap) if cap else 0.0,
        "placed": len(todo),
        "too_big": too_big,
    }


def edge_rolls(total_meters, roll_m=EDGE_ROLL_M):
    """Edge banding is length-additive: rolls = ceil(total metres / roll)."""
    return max(1, math.ceil(float(total_meters) / roll_m)) if total_meters else 0


def laminate_faces(ply_parts_by_code):
    """Laminate consumption from the ply parts themselves: every ply part face
    is laminated per its code's slots (SG_PLY_V0_a_a → both faces slot 'a';
    SG_PLY_V1_a_b → internal face 'a', external face 'b'). Returns
    {laminate_code: [(l, w), ...]} where laminate_code mirrors the shop's
    SG_LAM_V{v}_{thickness}mm_{int}_{ext} family (slot letters kept generic —
    the décor map resolves them downstream).

    ply_parts_by_code: {(ply_code, thickness_mm): [(l, w), ...]}"""
    out = {}
    for (code, th), parts in ply_parts_by_code.items():
        tokens = str(code).split("_")
        try:
            vi = next(i for i, t in enumerate(tokens) if t.upper().startswith("V") and t[1:].isdigit())
        except StopIteration:
            continue
        v = tokens[vi]
        slots = [t for t in tokens[vi + 1:] if t]
        if len(slots) < 2:
            continue
        internal, external = slots[0], slots[1]
        th_tag = f"{int(th)}mm" if th else "0mm"
        if v.upper() == "V0":
            # structure grade: both faces internal-grade laminate
            key = f"SG_LAM_V0_{th_tag}_{internal}_{internal}"
            out.setdefault(key, []).extend(parts)  # face 1
            out.setdefault(key, []).extend(parts)  # face 2
        else:
            out.setdefault(f"SG_LAM_{v.upper()}_{th_tag}_{internal}_{external}", []).extend(parts)
            out.setdefault(f"SG_LAM_{v.upper()}_{th_tag}_{external}_{internal}", []).extend(parts)
    return out
