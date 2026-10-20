"""Turn the stored character sheet and possessions into a display model for the UI.

The Storyteller is asked to save sheets in the standard layout described in the
set_sheet tool, but older chronicles and the odd improvisation produce other
shapes. This module is deliberately tolerant: it maps what it recognises onto
the V20 sheet and passes everything else through under "extras", so nothing the
Storyteller recorded is hidden from the player.
"""
from __future__ import annotations

import re
from typing import Any

ATTRIBUTES = [
    ("Physical", ["Strength", "Dexterity", "Stamina"]),
    ("Social", ["Charisma", "Manipulation", "Appearance"]),
    ("Mental", ["Perception", "Intelligence", "Wits"]),
]
ABILITIES = [
    ("Talents", ["Alertness", "Athletics", "Awareness", "Brawl", "Empathy", "Expression", "Intimidation", "Leadership", "Streetwise", "Subterfuge"]),
    ("Skills", ["Animal Ken", "Crafts", "Drive", "Etiquette", "Firearms", "Larceny", "Melee", "Performance", "Stealth", "Survival"]),
    ("Knowledges", ["Academics", "Computer", "Finance", "Investigation", "Law", "Medicine", "Occult", "Politics", "Science", "Technology"]),
]
VIRTUE_ORDER = ["Conscience", "Conviction", "Self-Control", "Instinct", "Courage"]
HEALTH_LEVELS = [
    ("Bruised", 0), ("Hurt", -1), ("Injured", -1), ("Wounded", -2),
    ("Mauled", -2), ("Crippled", -5), ("Incapacitated", None),
]
# generation: (max trait rating, max blood pool, blood per turn) — V20 generation chart
GENERATION_TABLE = {
    4: (9, 50, 10), 5: (8, 40, 8), 6: (7, 30, 6), 7: (6, 20, 4), 8: (5, 15, 3),
    9: (5, 14, 2), 10: (5, 13, 1), 11: (5, 12, 1), 12: (5, 11, 1), 13: (5, 10, 1),
    14: (5, 10, 1), 15: (5, 10, 1),
}
LOCATIONS = [
    ("held", "In hand"), ("worn", "Worn"), ("pockets", "Pockets"), ("bag", "Bag"),
    ("haven", "Haven"), ("vehicle", "Vehicle"), ("elsewhere", "Elsewhere"),
]
LOCATION_KEYS = [k for k, _ in LOCATIONS]
LOCATION_LABELS = dict(LOCATIONS)
CARRIED = ("held", "worn", "pockets", "bag")
IDENTITY = [
    ("concept", "Concept", ()), ("clan", "Clan", ()), ("generation", "Generation", ()),
    ("sire", "Sire", ()), ("nature", "Nature", ()), ("demeanor", "Demeanor", ("demeanour",)),
    ("sect", "Sect", ()), ("embraced", "Embraced", ("embrace", "embrace_date", "embrace_year")),
    ("apparent_age", "Apparent age", ("age",)), ("haven", "Haven", ()),
    ("player", "Player", ()), ("chronicle", "Chronicle", ()),
]
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


def sheet_view(sheet: dict | None, inventory: dict | list | None = None) -> dict:
    sheet = sheet if isinstance(sheet, dict) else {}
    inv = inventory_view(inventory)
    if not sheet:
        return {"empty": True, "inventory": inv}
    used: set[str] = set()

    def take(*names):
        v, k = _field(sheet, *names)
        if k is not None:
            used.add(k)
        return v

    name = take("name", "character", "character_name") or "Unnamed"
    identity = []
    ident_vals = {}
    for key, label, aliases in IDENTITY:
        v = take(key, *aliases)
        if v not in (None, "", []):
            ident_vals[key] = v
            if key == "generation" and _generation(v):
                v = _ordinal(_generation(v))
            identity.append({"label": label, "value": text(v)})

    gen = _generation(ident_vals.get("generation"))
    max_trait, gen_blood_max, per_turn = GENERATION_TABLE.get(gen, (5, None, None)) if gen else (5, None, None)

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
    for group, _ in ABILITIES:
        v = take(group)
        if v is not None:
            ab_entries.extend(collect(v, group))
    sec = take("secondary_abilities", "other_abilities")
    if sec is not None:
        ab_entries.extend(collect(sec, "Other"))
    canon = {norm(n): (g, n) for g, names in ABILITIES for n in names}
    found: dict[str, dict] = {}
    extra: dict[str, list] = {}
    for e in ab_entries:
        key = norm(e["name"])
        if key in canon:
            found.setdefault(key, e)
        else:
            cat = norm(e.get("category") or "")
            group = next((g for g, _ in ABILITIES if norm(g) == cat or norm(g).rstrip("s") == cat), "Other")
            extra.setdefault(group, []).append(e)
    abilities = []
    for group, names in ABILITIES:
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

    def trait_list(*names, category=None):
        v = take(*names)
        if v is None:
            return []
        return [{"name": _title(e["name"]) if e["name"].islower() else e["name"], "rating": e.get("rating"), "note": e.get("note")} for e in collect(v, category)]

    disciplines = trait_list("disciplines", "discipline")
    paths = trait_list("paths", "thaumaturgy_paths", "thaumaturgy")
    rituals = trait_list("rituals")
    backgrounds = trait_list("backgrounds", "background")
    virtues = trait_list("virtues", "virtue")
    order = {norm(v): i for i, v in enumerate(VIRTUE_ORDER)}
    for e in virtues:
        if norm(e["name"]) in order:
            e["name"] = VIRTUE_ORDER[order[norm(e["name"])]]
    virtues.sort(key=lambda e: order.get(norm(e["name"]), 99))

    # merits & flaws
    mf_entries = []
    for names, cat in ((("merits_flaws", "merits_and_flaws", "meritsflaws"), None), (("merits", "merit"), "merit"), (("flaws", "flaw"), "flaw")):
        v = take(*names)
        if v is not None:
            mf_entries.extend(collect(v, cat))
    merits_flaws = [_merit_entry(e) for e in mf_entries]

    # morality
    morality = None
    path_v = take("path_of_enlightenment", "road", "morality")
    hum_v = take("humanity")
    if path_v is not None and not isinstance(path_v, (int, float)):
        pname, _ = _field(path_v, "name") if isinstance(path_v, dict) else (str(path_v), None)
        morality = {"label": text(pname) or "Path", "rating": _rating(path_v)}
    elif path_v is not None:
        morality = {"label": "Path", "rating": _rating(path_v)}
    if morality is None and hum_v is not None:
        morality = {"label": "Humanity", "rating": _rating(hum_v)}
    if morality is None:
        p = sheet.get("path")
        if isinstance(p, dict) or isinstance(p, (int, float)):
            used.add("path")
            pname = _field(p, "name")[0] if isinstance(p, dict) else None
            morality = {"label": text(pname) or "Path", "rating": _rating(p)}

    # willpower
    wp_v = take("willpower")
    wp_max_v = take("willpower_max", "willpower_permanent", "max_willpower", "permanent_willpower")
    willpower = None
    if isinstance(wp_v, dict):
        cur = _as_int(_field(wp_v, "temporary", "current", "pool")[0])
        mx = _as_int(_field(wp_v, "permanent", "max", "rating")[0])
        willpower = {"current": cur if cur is not None else mx, "max": mx if mx is not None else cur}
    elif wp_v is not None or wp_max_v is not None:
        cur = _as_int(wp_v)
        mx = _as_int(wp_max_v)
        willpower = {"current": cur if cur is not None else mx, "max": mx if mx is not None else cur}

    # blood
    bl_v = take("blood", "blood_pool", "bloodpool", "vitae")
    bl_max_v = take("blood_max", "blood_pool_max", "max_blood", "max_blood_pool")
    blood = None
    if isinstance(bl_v, dict):
        cur = _as_int(_field(bl_v, "current", "pool", "points")[0])
        mx = _as_int(_field(bl_v, "max", "maximum")[0])
        blood = {"current": cur, "max": mx or gen_blood_max}
    elif bl_v is not None or bl_max_v is not None:
        blood = {"current": _as_int(bl_v), "max": _as_int(bl_max_v) or gen_blood_max}
    pt = _as_int(take("blood_per_turn", "blood_per_round"))
    if blood is not None:
        blood["per_turn"] = pt or per_turn

    health = _health(take("health", "health_levels", "damage", "wounds"))

    exp_v = take("experience", "xp", "exp")
    exp_total = _as_int(take("experience_total", "total_experience", "xp_total"))
    experience = None
    if isinstance(exp_v, dict):
        experience = {"unspent": _as_int(_field(exp_v, "unspent", "current", "available")[0]), "total": _as_int(_field(exp_v, "total", "earned")[0])}
    elif exp_v is not None:
        experience = {"unspent": _as_int(exp_v), "total": exp_total, "text": None if _as_int(exp_v) is not None else text(exp_v)}

    weakness = take("weakness", "clan_weakness", "clan_flaw")

    extras = []
    for k, v in sheet.items():
        if k in used or v in (None, "", [], {}):
            continue
        extras.append({"label": _title(k), "value": v})

    subtitle_bits = [text(ident_vals.get("clan")) if ident_vals.get("clan") else None,
                     f"{_ordinal(gen)} generation" if gen else None,
                     text(ident_vals.get("concept")) if ident_vals.get("concept") else None]
    return {
        "empty": False,
        "name": text(name),
        "subtitle": " · ".join(b for b in subtitle_bits if b),
        "identity": identity,
        "max_dots": max_trait,
        "attributes": attributes,
        "abilities": abilities,
        "disciplines": disciplines,
        "paths": paths,
        "rituals": rituals,
        "backgrounds": backgrounds,
        "virtues": virtues,
        "merits_flaws": merits_flaws,
        "morality": morality,
        "willpower": willpower,
        "blood": blood,
        "health": health,
        "experience": experience,
        "weakness": text(weakness) if weakness else None,
        "extras": extras,
        "inventory": inv,
    }
