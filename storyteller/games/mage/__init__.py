"""Mage: The Ascension, 20th Anniversary Edition."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import GameProfile, Group, Pool, SheetLayout, register

HERE = Path(__file__).resolve().parent

SPHERES = ("Correspondence", "Entropy", "Forces", "Life", "Matter", "Mind", "Prime", "Spirit", "Time")
WHEEL = 20  # Quintessence and Paradox share a 20-point wheel

# Magick difficulty: highest Sphere used + this, by how the Effect reads to witnesses (M20 p. 536).
DIFFICULTY_BASE = {"coincidental": 3, "vulgar_unwitnessed": 4, "vulgar_witnessed": 5}
DIFFICULTY_FLOOR = 3
# M20's default ceiling is 10; each point a modifier would push past it becomes one more success
# needed. The optional "Thresholds" rule caps at 9 instead (ST_MAGE_THRESHOLDS=1).
DIFFICULTY_CEILING = 9 if os.environ.get("ST_MAGE_THRESHOLDS", "0") == "1" else 10
MAX_QUINTESSENCE_PER_ROLL = 3  # each point spent lowers the difficulty by one, at most three

# Paradox points gained, by kind and outcome, as a function of the highest Sphere's rating
# (M20 pp. 546-547, as checked in the vault's Magick Rules note). A plain failure earns
# nothing here; the book is silent on vulgar failures and the note records that as a ruling.
PARADOX = {
    "coincidental":       {"success": lambda s: 0, "failure": lambda s: 0, "botch": lambda s: s},
    "vulgar_unwitnessed": {"success": lambda s: 1, "failure": lambda s: 0, "botch": lambda s: 1 + s},
    "vulgar_witnessed":   {"success": lambda s: 2, "failure": lambda s: 0, "botch": lambda s: 2 + 2 * s},
}


def paradox_for(kind: str, outcome: str, sphere: int) -> int:
    return max(0, int(PARADOX[kind][outcome](int(sphere))))


def magick_difficulty(highest_sphere: int, kind: str, quintessence_spent: int = 0) -> tuple[int, int]:
    """Returns (difficulty, extra successes needed because the difficulty passed the ceiling)."""
    if kind not in DIFFICULTY_BASE:
        raise ValueError(f"kind must be one of {sorted(DIFFICULTY_BASE)}, not {kind!r}")
    spent = max(0, min(int(quintessence_spent), MAX_QUINTESSENCE_PER_ROLL))
    raw = int(highest_sphere) + DIFFICULTY_BASE[kind] - spent
    extra = max(0, raw - DIFFICULTY_CEILING)
    return max(DIFFICULTY_FLOOR, min(raw, DIFFICULTY_CEILING)), extra


def _num(sheet: dict, key: str) -> int:
    """A numeric sheet value, tolerating strings and {current: n} dicts; missing counts as 0."""
    from ...sheet import rating_of
    return rating_of(sheet.get(key)) or 0


def reconcile_wheel(sheet: dict) -> list[str]:
    """Keep Quintessence + Paradox within the wheel: incoming Paradox pushes Quintessence out."""
    if "paradox" not in sheet and "quintessence" not in sheet:
        return []
    q, p = _num(sheet, "quintessence"), _num(sheet, "paradox")
    changes = []
    if q + p > WHEEL:
        new_q = max(0, WHEEL - p)
        if new_q != q:
            sheet["quintessence"] = new_q
            changes.append(f"Paradox pushed {q - new_q} Quintessence out of the wheel ({q} → {new_q})")
    if p > WHEEL:
        changes.append(f"Paradox {p} exceeds the {WHEEL}-point wheel: backlash now")
    return changes


ROLL_MAGICK_SPEC = {
    "name": "roll_magick",
    "description": (
        "Roll a magickal Effect (M20). Pool = the caster's Arete. Difficulty = highest Sphere used + 3 if coincidental, "
        "+ 4 if vulgar without Sleeper witnesses, + 5 if vulgar with witnesses; each point of Quintessence spent "
        "(up to 3) lowers it by one; a difficulty above the ceiling becomes the ceiling plus one more success "
        "needed per point. The tool rolls, compares successes with successes_needed, and works out any Paradox "
        "gained from the M20 table. For the player's own magick it also applies the Quintessence spent and the "
        "Paradox gained to the sheet (pushing Quintessence out of the wheel if Paradox overlaps it). With "
        "hidden=true (an NPC's magick) the player's sheet is left alone and only the result is returned. On a "
        "botch, narrate the backlash from the Magick Rules note. Always ask what the Effect is and how the "
        "caster's paradigm, practice and instruments produce it before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "arete": {"type": "integer", "minimum": 1, "maximum": 10},
            "highest_sphere": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Rating of the highest Sphere in the Effect."},
            "kind": {"type": "string", "enum": ["coincidental", "vulgar_unwitnessed", "vulgar_witnessed"]},
            "successes_needed": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Successes the Effect needs (duration, targets, damage...)."},
            "label": {"type": "string", "description": "What the mage is doing, e.g. 'Forces 2 / Prime 2: light the gas with a snap of her lighter'."},
            "quintessence_spent": {"type": "integer", "minimum": 0, "maximum": 3},
            "willpower": {"type": "boolean"},
            "hidden": {"type": "boolean", "description": "True for an NPC's magick: dice stay off the player's screen and the player's sheet is not touched."},
        },
        "required": ["arete", "highest_sphere", "kind", "successes_needed", "label"],
        "additionalProperties": False,
    },
    "eager_input_streaming": True,
}


def roll_magick(ctx, arete, highest_sphere, kind, successes_needed, label, quintessence_spent=0, willpower=False, hidden=False):
    from ... import dice

    asked = max(0, min(int(quintessence_spent), MAX_QUINTESSENCE_PER_ROLL))
    sheet = ctx.db.get_chronicle(ctx.cid)["sheet"]
    spent = asked
    reduced = None
    if not hidden:
        have_q = _num(sheet, "quintessence")
        if spent > have_q:
            reduced = f"the mage has only {have_q} Quintessence, so {have_q} was spent instead of {asked}"
            spent = have_q
    difficulty, extra = magick_difficulty(highest_sphere, kind, spent)
    needed = int(successes_needed) + extra
    r = dice.roll(arete, difficulty, specialty=False, willpower=willpower)
    outcome = "botch" if r.botch else ("success" if r.successes >= needed else "failure")
    gained = paradox_for(kind, outcome, highest_sphere)

    changes = []
    if not hidden:  # the player's own magick: the sheet carries the cost
        if spent:
            q = _num(sheet, "quintessence")
            sheet["quintessence"] = max(0, q - spent)
            changes.append(f"Quintessence {q} → {sheet['quintessence']}")
        if gained:
            p = _num(sheet, "paradox")
            sheet["paradox"] = p + gained
            changes.append(f"Paradox +{gained} → {sheet['paradox']}")
        changes.extend(reconcile_wheel(sheet))
        if changes:
            ctx.db.set_sheet(ctx.cid, sheet)
            ctx.emit("trait", {"path": "", "old": None, "new": None, "reason": "; ".join(changes)})  # no path: the reason says it all
            ctx.emit("state", {})

    d = r.to_dict()
    d.update(label=label, hidden=bool(hidden), magick_kind=kind, needed=needed, outcome=outcome,
             paradox_gained=gained, quintessence_spent=spent, magick=True)
    d["summary"] = {
        "botch": f"BOTCH: the Effect fails and Paradox strikes ({gained} point{'s' if gained != 1 else ''})",
        "failure": f"fails ({r.successes} of {needed} successes)",
        "success": f"succeeds ({r.successes} of {needed} successes)" + (f"; {gained} Paradox" if gained else ""),
    }[outcome]
    shown = {"label": label, "hidden": True, "summary": "rolled behind the screen", "magick": True} if hidden else d
    ctx.emit("dice", shown)
    out = {k: v for k, v in d.items() if k not in ("hidden",)}
    out["note"] = {
        "botch": "Roll Paradox backlash per the Magick Rules note (points released, Flaw severity), then narrate it.",
        "failure": "No Paradox gained on a plain failure. The mage may try again next turn.",
        "success": "Narrate the Effect as the paradigm explains it; vulgar effects seen by Sleepers invite disbelief.",
    }[outcome]
    if hidden:
        out["sheet"] = "unchanged: an NPC's roll. Track that NPC's Quintessence and Paradox in your private notes."
    else:
        out["sheet"] = "; ".join(changes) if changes else "unchanged"
        out["quintessence"], out["paradox"] = _num(sheet, "quintessence"), _num(sheet, "paradox")
    if reduced:
        out["quintessence_reduced"] = reduced
    return json.dumps(out)


SHEET = SheetLayout(
    identity=(
        ("concept", "Concept", ()), ("faction", "Tradition / Convention", ("tradition", "convention", "craft", "affiliation")),
        ("essence", "Essence", ()), ("nature", "Nature", ()), ("demeanor", "Demeanor", ("demeanour",)),
        ("paradigm", "Paradigm", ()), ("practice", "Practice", ("practices",)),
        ("mentor", "Mentor", ()), ("cabal", "Cabal", ()), ("chantry", "Chantry", ("sanctum",)),
        ("awakened", "Awakened", ("awakening",)), ("apparent_age", "Apparent age", ("age",)),
        ("player", "Player", ()), ("chronicle", "Chronicle", ()),
    ),
    abilities=(
        ("Talents", ("Alertness", "Art", "Athletics", "Awareness", "Brawl", "Empathy", "Expression", "Intimidation", "Leadership", "Streetwise", "Subterfuge")),
        ("Skills", ("Crafts", "Drive", "Etiquette", "Firearms", "Martial Arts", "Meditation", "Melee", "Research", "Stealth", "Survival", "Technology")),
        ("Knowledges", ("Academics", "Computer", "Cosmology", "Enigmas", "Esoterica", "Investigation", "Law", "Medicine", "Occult", "Politics", "Science")),
    ),
    groups=(
        Group("Spheres", "spheres", ("sphere",), fixed=SPHERES, note_key="affinity"),
        Group("Backgrounds", "backgrounds", ("background",)),
        Group("Resonance", "resonance", (), max_dots=5),
    ),
    pools=(
        Pool("willpower", "Willpower", "willpower_max", "will"),
    ),
    subtitle=("faction", "essence", "concept"),
    track_label="Arete",
    track_keys=("arete", "enlightenment"),
    wheel=("quintessence", "paradox", WHEEL),
    texts=(("instruments", "Instruments", ("foci", "focus")), ("avatar_description", "Avatar", ("avatar_desc",))),
    panel=("{track}", "Willpower", "Quintessence", "Paradox", "Health"),
)

SHEET_DOC = (
    '{"name": "...", "concept": "...", "faction": "Order of Hermes", "essence": "Pattern", "nature": "...", "demeanor": "...", '
    '"paradigm": "...", "practice": "...", "instruments": ["...", "...", "..."], "avatar_description": "...", '
    '"attributes": {"physical": {"strength": 2, "dexterity": 2, "stamina": 2}, '
    '"social": {"charisma": 3, "manipulation": 3, "appearance": 2}, '
    '"mental": {"perception": 3, "intelligence": 4, "wits": 3}}, '
    '"abilities": {"talents": {"alertness": 2, "awareness": 2}, "skills": {"meditation": 2, "research": 3}, '
    '"knowledges": {"academics": 3, "occult": 3, "esoterica": 2}}, '
    '"specialties": {"occult": "Hermetic lore"}, '
    '"spheres": {"forces": 2, "prime": 1, "correspondence": 1, "affinity": "forces"}, '
    '"arete": 2, "willpower": 5, "willpower_max": 5, "quintessence": 3, "paradox": 0, '
    '"backgrounds": {"avatar": 3, "mentor": 2, "library": 2}, '
    '"resonance": {"dynamic": 1}, '
    '"merits_flaws": [{"name": "Sphere Natural (Forces)", "points": 5, "type": "merit"}], '
    '"health": {"bashing": 0, "lethal": 0, "aggravated": 0}, '
    '"experience": {"unspent": 0, "total": 0}, "notes": "..."}\n'
    'Technocrats use the same keys ("arete" holds Enlightenment; "faction" the Convention). '
    'Spheres not listed count as 0; "affinity" names the affinity Sphere.'
)

PROFILE = register(GameProfile(
    id="mage",
    label="Mage: The Ascension (M20)",
    short="M20",
    prompts_dir=HERE / "prompts",
    campaigns={
        "default": {
            "label": "The Ascension War, present day",
            "premise": "premise_default.md",
            "core": [
                "Storyteller Reference/*.md",
                "Setting/*.md",
            ],
        },
    },
    default_campaign="default",
    sheet=SHEET,
    sheet_doc=SHEET_DOC,
    trait_doc=(
        "e.g. 'quintessence', 'paradox', 'willpower', 'arete', 'spheres.forces', 'health.lethal', 'experience.unspent', "
        "'abilities.knowledges.occult'."
    ),
    cap_doc=(
        ". Quintessence and Paradox share a 20-point wheel: quintessence never exceeds 20 minus paradox, and Paradox "
        "that overlaps stored Quintessence pushes it out. roll_magick applies Quintessence spent and Paradox gained "
        "by itself; use update_trait for everything else (Tass absorbed, Paradox released by backlash, Arete after a Seeking)."
    ),
    dice_doc=(
        "Roll an M20 dice pool for anything that is not a magickal Effect (use roll_magick for those). "
        "Set hidden=true for rolls whose dice the player must not see (NPC rolls, secret Perception checks, etc.). "
        "Describe the pool to the player before rolling."
    ),
    list_doc="e.g. 'MTA/npc', 'MTA/tradition', 'MTA/plot'",
    state_keys=("name", "faction", "arete", "willpower", "willpower_max", "quintessence", "paradox", "health", "spheres"),
    caps={"willpower": "willpower_max", "quintessence": ("wheel", WHEEL, "paradox")},
    extra_tools=(ROLL_MAGICK_SPEC,),
    extra_impls={"roll_magick": roll_magick},
    reconcile=reconcile_wheel,
))
