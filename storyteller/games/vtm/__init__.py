"""Vampire: The Masquerade, 20th Anniversary Edition."""
from __future__ import annotations

from pathlib import Path

from .. import GameProfile, Group, Pool, SheetLayout, register

HERE = Path(__file__).resolve().parent

# generation: (max trait rating, max blood pool, blood per turn) — V20 generation chart
GENERATION_TABLE = {
    4: (9, 50, 10), 5: (8, 40, 8), 6: (7, 30, 6), 7: (6, 20, 4), 8: (5, 15, 3),
    9: (5, 14, 2), 10: (5, 13, 1), 11: (5, 12, 1), 12: (5, 11, 1), 13: (5, 10, 1),
    14: (5, 10, 1), 15: (5, 10, 1),
}


def _generation(sheet: dict) -> int | None:
    from ...sheet import field_of, generation_of
    return generation_of(field_of(sheet, "generation")[0])


def _max_dots(sheet: dict, take) -> int:
    g = _generation(sheet)
    return GENERATION_TABLE.get(g, (5,))[0] if g else 5


def _blood_max(sheet: dict, take) -> int | None:
    g = _generation(sheet)
    return GENERATION_TABLE[g][1] if g in GENERATION_TABLE else None


def _blood_note(sheet: dict, take) -> str | None:
    from ...sheet import as_int
    pt = as_int(take("blood_per_turn", "blood_per_round"))
    if pt is None:
        g = _generation(sheet)
        pt = GENERATION_TABLE[g][2] if g in GENERATION_TABLE else None
    return f"Spend up to {pt} per turn" if pt else None


def _morality(sheet: dict, take) -> dict | None:
    from ...sheet import rating_of, text_of, field_of
    path_v = take("path_of_enlightenment", "road", "morality")
    hum_v = take("humanity")
    if path_v is not None and not isinstance(path_v, (int, float)):
        pname = field_of(path_v, "name")[0] if isinstance(path_v, dict) else str(path_v)
        return {"label": text_of(pname) or "Path", "rating": rating_of(path_v)}
    if path_v is not None:
        return {"label": "Path", "rating": rating_of(path_v)}
    if hum_v is not None:
        return {"label": "Humanity", "rating": rating_of(hum_v)}
    p = sheet.get("path")
    if isinstance(p, dict) or isinstance(p, (int, float)):
        take("path")
        pname = field_of(p, "name")[0] if isinstance(p, dict) else None
        return {"label": text_of(pname) or "Path", "rating": rating_of(p)}
    return None


SHEET = SheetLayout(
    identity=(
        ("concept", "Concept", ()), ("clan", "Clan", ()), ("generation", "Generation", ()),
        ("sire", "Sire", ()), ("nature", "Nature", ()), ("demeanor", "Demeanor", ("demeanour",)),
        ("sect", "Sect", ()), ("embraced", "Embraced", ("embrace", "embrace_date", "embrace_year")),
        ("apparent_age", "Apparent age", ("age",)), ("haven", "Haven", ()),
        ("player", "Player", ()), ("chronicle", "Chronicle", ()),
    ),
    abilities=(
        ("Talents", ("Alertness", "Athletics", "Awareness", "Brawl", "Empathy", "Expression", "Intimidation", "Leadership", "Streetwise", "Subterfuge")),
        ("Skills", ("Animal Ken", "Crafts", "Drive", "Etiquette", "Firearms", "Larceny", "Melee", "Performance", "Stealth", "Survival")),
        ("Knowledges", ("Academics", "Computer", "Finance", "Investigation", "Law", "Medicine", "Occult", "Politics", "Science", "Technology")),
    ),
    groups=(
        Group("Disciplines", "disciplines", ("discipline",), max_dots=None),
        Group("Paths", "paths", ("thaumaturgy_paths", "thaumaturgy"), max_dots=None),
        Group("Rituals", "rituals", (), max_dots=0),
        Group("Backgrounds", "backgrounds", ("background",)),
        Group("Virtues", "virtues", ("virtue",), order=("Conscience", "Conviction", "Self-Control", "Instinct", "Courage")),
    ),
    pools=(
        Pool("willpower", "Willpower", "willpower_max", "will",
             max_aliases=("willpower_permanent", "max_willpower", "permanent_willpower")),
        Pool("blood", "Blood pool", "blood_max", "blood", aliases=("blood_pool", "bloodpool", "vitae"),
             max_aliases=("blood_pool_max", "max_blood", "max_blood_pool"), max_default=_blood_max, note=_blood_note),
    ),
    subtitle=("clan", "generation", "concept"),
    track_label=None,  # morality comes from the hook below (Humanity or a Path)
    texts=(("weakness", "Weakness", ("clan_weakness", "clan_flaw")),),
    max_dots=_max_dots,
    panel=("Blood pool", "Willpower", "{track}", "Health"),
)

SHEET_DOC = (
    '{"name": "...", "concept": "...", "clan": "...", "generation": 11, "sire": "...", "nature": "...", '
    '"demeanor": "...", "sect": "Camarilla", "embraced": "1999", "apparent_age": "mid-20s", '
    '"attributes": {"physical": {"strength": 2, "dexterity": 3, "stamina": 2}, '
    '"social": {"charisma": 3, "manipulation": 2, "appearance": 4}, '
    '"mental": {"perception": 3, "intelligence": 2, "wits": 3}}, '
    '"abilities": {"talents": {"alertness": 1, "subterfuge": 3}, "skills": {"etiquette": 2, "animal_ken": 1}, '
    '"knowledges": {"academics": 1}}, '
    '"specialties": {"etiquette": "Elysium"}, '
    '"disciplines": {"auspex": 2, "presence": 1}, '
    '"backgrounds": {"resources": 2, "contacts": 1}, '
    '"virtues": {"conscience": 3, "self_control": 3, "courage": 2}, '
    '"merits_flaws": [{"name": "Eat Food", "points": 1, "type": "merit"}, {"name": "Haunted", "points": 3, "type": "flaw"}], '
    '"humanity": 6, "willpower": 3, "willpower_max": 3, "blood": 10, "blood_max": 12, '
    '"health": {"bashing": 0, "lethal": 0, "aggravated": 0}, '
    '"experience": {"unspent": 0, "total": 0}, "weakness": "...", "rituals": [], "notes": "..."}\n'
    'A character on a Path uses "path": {"name": "...", "rating": 5} instead of humanity.'
)  # byte-identical to the pre-profile set_sheet text: changing it resets every VTM chronicle's stored reasoning once

PROFILE = register(GameProfile(
    id="vtm",
    label="Vampire: The Masquerade (V20)",
    short="V20",
    prompts_dir=HERE / "prompts",
    campaigns={
        "campaign1": {
            "label": "Campaign 1 — New York, 2001",
            "premise": "campaign1_premise.md",
            "core": [
                "Storyteller Reference/*.md",
                "Campaign 1 - Storyteller Overview.md",
                "Campaign 1 Blurb.md",
                "Setting/Campaign 1 Locations.md",
                "Setting/NYC Current State.md",
                "Setting/NYC Geography and Domains.md",
                "Setting/NYC Kindred History.md",
                "Setting/Telling the Tale.md",
            ],
        },
    },
    default_campaign="campaign1",
    sheet=SHEET,
    sheet_doc=SHEET_DOC,
    trait_doc=(
        "e.g. 'blood', 'willpower', 'humanity', 'health.lethal', 'health.aggravated', 'experience.unspent', "
        "'abilities.skills.stealth', 'disciplines.auspex'."
    ),
    cap_doc=", and blood and willpower never exceed blood_max and willpower_max.",
    dice_doc=(
        "Roll a V20 dice pool. Use for every roll in the game, including NPC rolls. "
        "Set hidden=true for rolls whose dice the player must not see (NPC Obfuscate, "
        "secret Perception checks, etc.). Describe the pool to the player before rolling."
    ),
    list_doc="e.g. 'VTM/npc', 'VTM/malkavian', 'VTM/plot'",
    state_keys=("name", "clan", "generation", "blood", "blood_max", "willpower", "willpower_max", "humanity", "path", "health"),
    caps={"blood": "blood_max", "willpower": "willpower_max"},
    morality=_morality,
))
