"""Turn the stored character sheet and possessions into a display model for the UI.

The Storyteller is asked to save sheets in the layout the game's set_sheet tool
describes, but older chronicles and the odd improvisation produce other shapes.
This module is deliberately tolerant: it maps what it recognises onto the game's
sheet layout (a GameProfile.sheet) and passes everything else through under
"extras", so nothing the Storyteller recorded is hidden from the player.
"""
from __future__ import annotations

import re
from typing import Any

from .games import GameProfile, SheetLayout

ATTRIBUTES = [
    ("Physical", ["Strength", "Dexterity", "Stamina"]),
    ("Social", ["Charisma", "Manipulation", "Appearance"]),
    ("Mental", ["Perception", "Intelligence", "Wits"]),
]
HEALTH_LEVELS = [
    ("Bruised", 0), ("Hurt", -1), ("Injured", -1), ("Wounded", -2),
    ("Mauled", -2), ("Crippled", -5), ("Incapacitated", None),
]
# generation: (max trait rating, max blood pool, blood per turn) — V20 generation chart
LOCATIONS = [
    ("held", "In hand"), ("worn", "Worn"), ("pockets", "Pockets"), ("bag", "Bag"),
    ("haven", "Haven"), ("vehicle", "Vehicle"), ("elsewhere", "Elsewhere"),
]
LOCATION_KEYS = [k for k, _ in LOCATIONS]
LOCATION_LABELS = dict(LOCATIONS)
CARRIED = ("held", "worn", "pockets", "bag")
RATING_KEYS = ("rating", "dots", "value", "level", "score", "permanent", "current", "points")
NOTE_KEYS = ("specialty", "specialties", "speciality", "note", "notes", "powers", "description", "desc", "detail", "details")
NAME_KEYS = ("name", "trait", "title", "label")


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


_NOTE_NORM = {norm(k) for k in NOTE_KEYS}
_RATING_NORM = [norm(k) for k in RATING_KEYS]


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if s and set(s) <= set("●•○◦ "):
            return s.count("●") + s.count("•")
    return None


def _field(d: dict, *names: str):
    """Look up a key in d by normalised name. Returns (value, actual_key) or (None, None)."""
    wanted = [norm(n) for n in names]
    for w in wanted:
        for k, v in d.items():
            if norm(k) == w:
                return v, k
    return None, None


def _rating(v: Any) -> int | None:
    r = _as_int(v)
    if r is not None:
        return r
    if isinstance(v, dict):
        for rk in _RATING_NORM:
            for k, val in v.items():
                if norm(k) == rk:
                    r = _as_int(val)
                    if r is not None:
                        return r
    return None


def text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(t for t in (text(x) for x in v) if t)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {text(x)}" for k, x in v.items() if text(x))
    return str(v)


def _note(v: Any) -> str | None:
    if isinstance(v, dict):
        parts = [text(val) for k, val in v.items() if norm(k) in _NOTE_NORM]
        return "; ".join(p for p in parts if p) or None
    if isinstance(v, list):
        return text(v) or None
    if isinstance(v, str) and _as_int(v) is None:
        return v.strip() or None
    return None


def _is_trait_dict(v: Any) -> bool:
    return isinstance(v, dict) and any(norm(k) in _RATING_NORM or norm(k) in _NOTE_NORM for k in v)


_PAIR = [
    re.compile(r"^(?P<name>.+?)\s*(?:\((?P<note>[^)]*)\))?\s*[:=\-–]?\s*(?P<rating>\d+)\s*(?:dots?|pts?|points?)?\s*$", re.I),
    re.compile(r"^(?P<name>.+?)\s+(?P<rating>\d+)\s*\((?P<note>[^)]*)\)\s*$"),
]
_PAREN = re.compile(r"^(?P<name>[^(]+?)\s*\((?P<note>[^)]*)\)\s*$")


def _parse_str(s: str):
    s = s.strip().lstrip("-•*").strip()
    if not s:
        return None
    for rx in _PAIR:
        m = rx.match(s)
        if m:
            return m.group("name").strip(" :-"), int(m.group("rating")), (m.group("note") or None)
    m = _PAREN.match(s)
    if m:
        return m.group("name").strip(), None, m.group("note").strip()
    return s, None, None


def collect(obj: Any, category: str | None = None) -> list[dict]:
    """Flatten dicts/lists/strings of traits into [{category, name, rating, note}]."""
    out: list[dict] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and not _is_trait_dict(v):
                out.extend(collect(v, str(k)))
            elif isinstance(v, list) and v and all(isinstance(x, str) for x in v) and any(
                (_parse_str(x) or (None, None))[1] is not None for x in v
            ):
                out.extend(collect(v, str(k)))
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                out.extend(collect(v, str(k)))
            else:
                out.append({"category": category, "name": str(k), "rating": _rating(v), "note": _note(v)})
    elif isinstance(obj, list):
        for x in obj:
            if isinstance(x, str):
                p = _parse_str(x)
                if p:
                    out.append({"category": category, "name": p[0], "rating": p[1], "note": p[2]})
            elif isinstance(x, dict):
                name, _ = _field(x, *NAME_KEYS)
                if name:
                    cat, _ = _field(x, "category", "type", "kind")
                    out.append({"category": str(cat) if cat else category, "name": str(name), "rating": _rating(x), "note": _note(x)})
                else:
                    out.extend(collect(x, category))
    elif isinstance(obj, str):
        for part in re.split(r"[,;\n]", obj):
            p = _parse_str(part)
            if p:
                out.append({"category": category, "name": p[0], "rating": p[1], "note": p[2]})
    return out


def _title(key: str) -> str:
    s = str(key).replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else s


def _generation(v: Any) -> int | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v)
        if m:
            return int(m.group())
    return None


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _health(v: Any) -> dict:
    """Health as 7 boxes, marks '*' aggravated, 'X' lethal, '/' bashing, '•' unspecified."""
    agg = leth = bash = generic = 0
    note = None
    if isinstance(v, dict):
        agg = _as_int(_field(v, "aggravated", "agg")[0]) or 0
        leth = _as_int(_field(v, "lethal")[0]) or 0
        bash = _as_int(_field(v, "bashing")[0]) or 0
        if not (agg or leth or bash):
            lvl, _ = _field(v, "level", "status", "current")
            if isinstance(lvl, str):
                return _health(lvl)
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        generic = int(v)
    elif isinstance(v, list):
        marks = [str(x).strip() for x in v]
        agg = sum(1 for m in marks if m in ("*", "A", "agg", "aggravated"))
        leth = sum(1 for m in marks if m.upper() in ("X", "L") or m == "lethal")
        bash = sum(1 for m in marks if m in ("/", "B", "bashing"))
    elif isinstance(v, str) and v.strip():
        note = v.strip()
        low = v.lower()
        if "healthy" in low or "unhurt" in low or "fine" == low:
            generic = 0
        else:
            for i, (label, _) in enumerate(HEALTH_LEVELS):
                if label.lower() in low:
                    generic = i + 1
                    break
    marks = ["*"] * agg + ["X"] * leth + ["/"] * bash + ["•"] * generic
    total = len(marks)
    boxes = []
    for i, (label, pen) in enumerate(HEALTH_LEVELS):
        boxes.append({"label": label, "penalty": pen, "mark": marks[i] if i < total else ""})
    if total == 0:
        status, pen = "Healthy", 0
    elif total <= len(HEALTH_LEVELS):
        status, pen = HEALTH_LEVELS[total - 1]
    else:
        status, pen = "Beyond Incapacitated", None
    return {
        "boxes": boxes, "status": status, "penalty": pen, "note": note,
        "aggravated": agg, "lethal": leth, "bashing": bash, "overflow": max(0, total - len(HEALTH_LEVELS)),
    }


def _merit_entry(e: dict) -> dict:
    blob = " ".join(x for x in (e["name"], e.get("note") or "", e.get("category") or "") if x)
    low = blob.lower()
    kind = "flaw" if "flaw" in low else "merit" if "merit" in low else None
    pts = e.get("rating")
    if pts is None:
        m = re.search(r"(-?\d+)\s*(?:pt|point)?", blob)
        pts = int(m.group(1)) if m else None
    if pts is not None and pts < 0:
        kind = kind or "flaw"
        pts = -pts
    name = re.sub(r"\s*\((?:-?\d+\s*(?:pts?|points?)?\s*)?(?:merit|flaw)?\)\s*$", "", e["name"], flags=re.I).strip()
    note = e.get("note")
    if note and re.fullmatch(r"\s*-?\d+\s*(?:pts?|points?)?\s*(?:merit|flaw)?\s*", note, re.I):
        note = None
    return {"name": name or e["name"], "points": pts, "kind": kind, "note": note}


def _location(loc: Any) -> str:
    low = str(loc or "").lower()
    if low in LOCATION_LABELS:
        return low
    for key, words in (
        ("held", ("hand", "held", "holding")),
        ("worn", ("worn", "wear", "cloth", "body")),
        ("pockets", ("pocket", "wallet")),
        ("bag", ("bag", "backpack", "purse", "briefcase", "satchel", "case")),
        ("haven", ("haven", "home", "apartment", "flat", "lair")),
        ("vehicle", ("car", "vehicle", "trunk", "van", "bike", "glovebox")),
    ):
        if any(w in low for w in words):
            return key
    return "elsewhere"


def inventory_view(inventory: dict | list | None) -> dict:
    items = inventory.get("items", []) if isinstance(inventory, dict) else (inventory or [])
    groups: dict[str, list] = {k: [] for k in LOCATION_KEYS}
    for it in items:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        key = _location(it.get("location"))
        groups[key].append({
            "id": it.get("id"), "name": it["name"], "qty": it.get("qty", 1),
            "detail": it.get("detail") or "", "notes": it.get("notes") or "",
        })
    def pack(keys):
        return [{"key": k, "label": LOCATION_LABELS[k], "items": groups[k]} for k in keys if groups[k]]
    return {
        "carried": pack(CARRIED),
        "stashed": pack([k for k in LOCATION_KEYS if k not in CARRIED]),
        "count": sum(len(v) for v in groups.values()),
    }



# Public names the game profiles use.
as_int = _as_int
field_of = _field
rating_of = _rating
text_of = text
generation_of = _generation


def _trait_rows(entries: list[dict], fixed: tuple[str, ...] = (), order: tuple[str, ...] = (), mark: str | None = None) -> list[dict]:
    """Rows for a dotted-trait group.

    `fixed` names are always shown (zero if absent) in that order; `order` names are
    canonicalised and sorted in that order but only shown when present; everything
    else follows in the order the sheet listed it. A trait's own note is kept as is.
    """
    by_key: dict[str, dict] = {}
    for e in entries:
        by_key.setdefault(norm(e["name"]), e)
    rows = []
    for name in fixed:
        e = by_key.pop(norm(name), {})
        rows.append({"name": name, "rating": e.get("rating") or 0, "note": e.get("note"), "mark": norm(name) == norm(mark) if mark else False})
    canon = {norm(n): (i, n) for i, n in enumerate(order)}
    rest = []
    for key, e in by_key.items():
        nm = canon[key][1] if key in canon else (_title(e["name"]) if e["name"].islower() else e["name"])
        rest.append((canon[key][0] if key in canon else len(order), {"name": nm, "rating": e.get("rating"), "note": e.get("note"), "mark": key == norm(mark) if mark else False}))
    rest.sort(key=lambda r: r[0])
    rows.extend(r for _, r in rest)
    return rows


def sheet_view(sheet: dict | None, inventory: dict | list | None = None, profile: GameProfile | None = None) -> dict:
    if profile is None:
        from .games import get
        profile = get("vtm")
    layout: SheetLayout = profile.sheet
    sheet = sheet if isinstance(sheet, dict) else {}
    inv = inventory_view(inventory)
    if not sheet:
        return {"empty": True, "inventory": inv, "game": profile.id}
    used: set[str] = set()

    def take(*names):
        v, k = _field(sheet, *names)
        if k is not None:
            used.add(k)
        return v

    name = take("name", "character", "character_name") or "Unnamed"
    identity = []
    ident_vals = {}
    for key, label, aliases in layout.identity:
        v = take(key, *aliases)
        if v not in (None, "", []):
            ident_vals[key] = v
            if key == "generation" and _generation(v):
                v = _ordinal(_generation(v))
            identity.append({"label": label, "value": text(v)})
    max_trait = layout.max_dots(sheet, take) if layout.max_dots else 5

    # specialties, from a dedicated map or from trait notes
    specialties: dict[str, str] = {}
    spec_raw = take("specialties", "specialities", "specialty")
    for e in collect(spec_raw) if spec_raw is not None else []:
        if e.get("note") or e.get("rating") is None:
            specialties[norm(e["name"])] = e.get("note") or ""
    if isinstance(spec_raw, dict):
        for k, v in spec_raw.items():
            if isinstance(v, str):
                specialties[norm(k)] = v

    # attributes
    attr_raw = take("attributes", "attribute")
    attr_entries = collect(attr_raw) if attr_raw is not None else []
    if attr_raw is None:  # flat sheet: attributes at top level
        for _, names in ATTRIBUTES:
            for n in names:
                v, k = _field(sheet, n)
                if k is not None:
                    used.add(k)
                    attr_entries.append({"category": None, "name": n, "rating": _rating(v), "note": _note(v)})
    attr_found = {norm(e["name"]): e for e in attr_entries}
    attributes = []
    for group, names in ATTRIBUTES:
        traits = []
        for n in names:
            e = attr_found.get(norm(n), {})
            traits.append({"name": n, "rating": e.get("rating") or 0, "specialty": specialties.get(norm(n)) or (e.get("note") if e.get("rating") is not None else None)})
        attributes.append({"group": group, "traits": traits})

    # abilities
    ab_entries = []
    ab_raw = take("abilities", "ability")
    if ab_raw is not None:
        ab_entries.extend(collect(ab_raw))
    for group, _ in layout.abilities:
        v = take(group)
        if v is not None:
            ab_entries.extend(collect(v, group))
    sec = take("secondary_abilities", "other_abilities")
    if sec is not None:
        ab_entries.extend(collect(sec, "Other"))
    canon = {norm(n): (g, n) for g, names in layout.abilities for n in names}
    found: dict[str, dict] = {}
    extra: dict[str, list] = {}
    for e in ab_entries:
        key = norm(e["name"])
        if key in canon:
            found.setdefault(key, e)
        else:
            cat = norm(e.get("category") or "")
            group = next((g for g, _ in layout.abilities if norm(g) == cat or norm(g).rstrip("s") == cat), "Other")
            extra.setdefault(group, []).append(e)
    abilities = []
    for group, names in layout.abilities:
        traits = []
        for n in names:
            e = found.get(norm(n), {})
            traits.append({"name": n, "rating": e.get("rating") or 0, "specialty": specialties.get(norm(n)) or e.get("note")})
        for e in extra.get(group, []):
            traits.append({"name": _title(e["name"]) if e["name"].islower() else e["name"], "rating": e.get("rating") or 0, "specialty": specialties.get(norm(e["name"])) or e.get("note")})
        abilities.append({"group": group, "traits": traits})
    if extra.get("Other"):
        abilities.append({"group": "Other", "traits": [
            {"name": _title(e["name"]) if e["name"].islower() else e["name"], "rating": e.get("rating") or 0, "specialty": specialties.get(norm(e["name"])) or e.get("note")} for e in extra["Other"]
        ]})

    # dotted trait groups (Disciplines, Spheres, Backgrounds, Virtues, Resonance...)
    groups = []
    for g in layout.groups:
        raw = take(g.key, *g.aliases)
        mark = None
        entries = []
        if raw is not None:
            if isinstance(raw, dict) and g.note_key:
                mv, mk = _field(raw, g.note_key, f"{g.note_key}_sphere", f"{g.note_key}_trait")
                if mk is not None:
                    mark = str(mv)
                    raw = {k: v for k, v in raw.items() if k != mk}
            entries = collect(raw)
        if raw is None and not g.fixed:
            groups.append({"title": g.title, "traits": [], "max": g.max_dots})
            continue
        rows = _trait_rows(entries, g.fixed, g.order, mark)
        groups.append({"title": g.title, "traits": rows, "max": g.max_dots, "mark_label": g.note_key})

    # merits & flaws
    mf_entries = []
    for names, cat in ((("merits_flaws", "merits_and_flaws", "meritsflaws"), None), (("merits", "merit"), "merit"), (("flaws", "flaw"), "flaw")):
        v = take(*names)
        if v is not None:
            mf_entries.extend(collect(v, cat))
    merits_flaws = [_merit_entry(e) for e in mf_entries]

    # status: the 1-10 track (Humanity/Path or Arete), pools, the wheel, prose
    track = None
    if profile.morality:
        track = profile.morality(sheet, take)
    elif layout.track_label:
        v, used_key = _field(sheet, *layout.track_keys)
        if used_key is not None:
            used.add(used_key)
            label = "Enlightenment" if norm(used_key) == "enlightenment" else layout.track_label
            track = {"label": label, "rating": _rating(v)}
    if track:
        track["max"] = 10

    pools = []
    for pool in layout.pools:
        v = take(pool.key, *pool.aliases)
        mx_v = take(pool.max_key, *pool.max_aliases) if pool.max_key else None
        if isinstance(v, dict):
            cur = _as_int(_field(v, "temporary", "current", "pool", "points")[0])
            mx = _as_int(_field(v, "permanent", "max", "maximum", "rating")[0])
        elif v is None and mx_v is None:
            continue
        else:
            cur, mx = _as_int(v), _as_int(mx_v)
        if mx is None and pool.max_default:
            mx = pool.max_default(sheet, take)
        if cur is None:
            cur = mx
        pools.append({"key": pool.key, "label": pool.label, "current": cur, "max": mx if mx is not None else cur, "style": pool.style,
                      "note": pool.note(sheet, take) if pool.note else None})

    wheel = None
    if layout.wheel:
        lk, rk, size = layout.wheel
        lv, rv = take(lk), take(rk)
        if lv is not None or rv is not None:
            wheel = {"size": size, "left": {"key": lk, "label": _title(lk), "current": _as_int(lv) or 0},
                     "right": {"key": rk, "label": _title(rk), "current": _as_int(rv) or 0}}

    texts = []
    for key, label, aliases in layout.texts:
        v = take(key, *aliases)
        if v not in (None, "", []):
            texts.append({"label": label, "text": text(v)})

    health = _health(take("health", "health_levels", "damage", "wounds"))

    exp_v = take("experience", "xp", "exp")
    exp_total = _as_int(take("experience_total", "total_experience", "xp_total"))
    experience = None
    if isinstance(exp_v, dict):
        experience = {"unspent": _as_int(_field(exp_v, "unspent", "current", "available")[0]), "total": _as_int(_field(exp_v, "total", "earned")[0])}
    elif exp_v is not None:
        experience = {"unspent": _as_int(exp_v), "total": exp_total, "text": None if _as_int(exp_v) is not None else text(exp_v)}

    extras = []
    for k, v in sheet.items():
        if k in used or v in (None, "", [], {}):
            continue
        extras.append({"label": _title(k), "value": v})

    # the side panel's compact trackers, in the order the profile asks for
    candidates: dict[str, dict] = {}
    for pl in pools:
        candidates[pl["label"]] = {"label": pl["label"], "kind": "boxes", "current": pl["current"], "max": pl["max"], "style": pl["style"]}
    if track:
        candidates["{track}"] = {"label": track["label"], "kind": "track", "current": track["rating"], "max": 10, "style": "will"}
    if wheel:
        candidates[wheel["left"]["label"]] = {"label": wheel["left"]["label"], "kind": "bar", "current": wheel["left"]["current"], "max": wheel["size"], "style": "quint"}
        candidates[wheel["right"]["label"]] = {"label": wheel["right"]["label"], "kind": "bar", "current": wheel["right"]["current"], "max": wheel["size"], "style": "paradox"}
    dmg = sum(1 for b in health["boxes"] if b["mark"])
    candidates["Health"] = {"label": "Health", "kind": "boxes", "current": dmg, "max": 7, "style": "hurt",
                            "text": health["status"] + (f" ({health['penalty']})" if health["penalty"] else "")}
    panel = [candidates[label] for label in layout.panel if label in candidates]
    for label, c in candidates.items():  # anything the profile did not name but the sheet has
        if label not in layout.panel and label != "Health" and (c["current"] is not None):
            panel.append(c)

    subtitle_bits = []
    for key in layout.subtitle:
        v = ident_vals.get(key)
        if v in (None, ""):
            continue
        subtitle_bits.append(f"{_ordinal(_generation(v))} generation" if key == "generation" and _generation(v) else text(v))
    return {
        "empty": False,
        "game": profile.id,
        "name": text(name),
        "subtitle": " · ".join(subtitle_bits),
        "identity": identity,
        "max_dots": max_trait,
        "attributes": attributes,
        "abilities": abilities,
        "groups": groups,
        "merits_flaws": merits_flaws,
        "status": {"track": track, "pools": pools, "wheel": wheel, "texts": texts},
        "health": health,
        "experience": experience,
        "panel": panel,
        "extras": extras,
        "inventory": inv,
    }
