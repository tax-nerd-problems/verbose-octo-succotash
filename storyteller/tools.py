"""Tool definitions (JSON schema) and their implementations.

Every tool the model can call is declared in TOOL_SPECS and dispatched by
ToolContext.run(). Results are returned as strings (JSON where structured) so
they can go straight into a tool_result block.
"""
from __future__ import annotations

import json
from typing import Any

from . import config, dice
from .db import DB
from .vault import Vault


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_SPECS: list[dict] = [
    {
        "name": "roll_dice",
        "description": (
            "Roll a V20 dice pool. Use for every roll in the game, including NPC rolls. "
            "Set hidden=true for rolls whose dice the player must not see (NPC Obfuscate, "
            "secret Perception checks, etc.). Describe the pool to the player before rolling."
        ),
        "input_schema": _schema(
            {
                "pool": {"type": "integer", "minimum": 0, "maximum": 30, "description": "Dice to roll (Attribute + Ability, after wound penalties)."},
                "difficulty": {"type": "integer", "minimum": 2, "maximum": 10, "description": "Target number. Default 6."},
                "label": {"type": "string", "description": "What is being rolled, e.g. 'Anja: Charisma + Etiquette vs Rene'."},
                "specialty": {"type": "boolean", "description": "True if a specialty applies (10s count double)."},
                "willpower": {"type": "boolean", "description": "True if a Willpower point is spent for an automatic success."},
                "hidden": {"type": "boolean", "description": "True to keep the dice off the player's screen."},
            },
            ["pool", "difficulty", "label"],
        ),
    },
    {
        "name": "read_sheet",
        "description": "Return the player's current character sheet as JSON.",
        "input_schema": _schema({}, []),
    },
    {
        "name": "set_sheet",
        "description": (
            "Replace the player's character sheet. Call once character creation is agreed, or when a "
            "large revision is needed. The sheet is free-form JSON but should include: name, clan, "
            "generation, sire, nature, demeanor, concept, attributes, abilities, disciplines, "
            "backgrounds, virtues, humanity, willpower, willpower_max, blood, blood_max, health "
            "(current level, e.g. 'Healthy'), experience, merits_flaws, notes."
        ),
        "input_schema": _schema({"sheet": {"type": "object", "description": "The full character sheet."}}, ["sheet"]),
    },
    {
        "name": "update_trait",
        "description": (
            "Adjust one value on the player's sheet. Use dotted paths for nested values "
            "(e.g. 'blood', 'willpower', 'humanity', 'experience', 'attributes.physical.strength'). "
            "Provide either delta (numeric change) or value (replacement, any JSON type)."
        ),
        "input_schema": _schema(
            {
                "path": {"type": "string"},
                "delta": {"type": "number", "description": "Amount to add; negative to spend or lose."},
                "value": {"description": "New value to set instead of a delta."},
                "reason": {"type": "string", "description": "One line the player will see, e.g. 'Spent 1 blood to heal.'"},
            },
            ["path", "reason"],
        ),
    },
    {
        "name": "set_scene",
        "description": "Update the table display: where and when the scene is, who is present, and the mood.",
        "input_schema": _schema(
            {
                "location": {"type": "string"},
                "time": {"type": "string", "description": "In-fiction date/time, e.g. 'Friday 14 September 2001, 11:40 PM'."},
                "present": {"type": "array", "items": {"type": "string"}, "description": "Named NPCs on screen."},
                "tension": {"type": "string", "enum": ["calm", "tense", "danger", "frenzy"]},
                "note": {"type": "string", "description": "A short player-visible line, e.g. 'Elysium: no violence.'"},
            },
            ["location", "time"],
        ),
    },
    {
        "name": "write_st_note",
        "description": (
            "Store or replace a private Storyteller note under a key. Keys you should maintain: "
            "'recap' (what the next session needs to know), 'npc_agendas', 'clocks', 'secrets', "
            "'threads'. Notes are private; the player never sees them."
        ),
        "input_schema": _schema({"key": {"type": "string"}, "text": {"type": "string"}}, ["key", "text"]),
    },
    {
        "name": "read_st_notes",
        "description": "Return all private Storyteller notes, or one by key.",
        "input_schema": _schema({"key": {"type": "string"}}, []),
    },
    {
        "name": "search_vault",
        "description": (
            "Search the campaign vault (NPCs, factions, plots, setting, sourcebooks). Returns the best "
            "matching notes with a snippet. Follow up with read_note."
        ),
        "input_schema": _schema(
            {
                "query": {"type": "string"},
                "folder": {"type": "string", "description": "Optional folder to restrict to, e.g. 'Characters', 'Clanbook Tremere (Revised)'."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            ["query"],
        ),
    },
    {
        "name": "read_note",
        "description": (
            "Read a vault note by title, alias, or [[wikilink]]. Optionally only one section by heading. "
            "Use this before an NPC appears, and for rules you need to adjudicate precisely."
        ),
        "input_schema": _schema(
            {"name": {"type": "string"}, "section": {"type": "string", "description": "Heading text to return only that section."}},
            ["name"],
        ),
    },
    {
        "name": "list_notes",
        "description": "List notes by tag (e.g. 'VTM/npc', 'VTM/malkavian', 'VTM/plot') or by folder. Returns titles and one-line summaries.",
        "input_schema": _schema({"tag": {"type": "string"}, "folder": {"type": "string"}}, []),
    },
    {
        "name": "write_session_log",
        "description": (
            "Write a session log into the vault's Sessions folder. Use at the end of a session: what happened, "
            "in order; NPCs met and their current disposition; rolls that mattered; threads left open; "
            "the character's state at the end. Markdown, with [[wikilinks]] to NPC notes."
        ),
        "input_schema": _schema(
            {"title": {"type": "string", "description": "Short title, e.g. 'Session 1 - The Masquerade'."}, "content": {"type": "string"}},
            ["title", "content"],
        ),
    },
    {
        "name": "append_to_vault_note",
        "description": (
            "Append a dated chronicle note to an existing vault note (usually an NPC) under a marked "
            "'Chronicle Notes (AI Storyteller)' heading. Use for durable changes: a new relationship, "
            "a debt, an injury, a change of allegiance. Never rewrites the original text."
        ),
        "input_schema": _schema({"name": {"type": "string"}, "text": {"type": "string"}}, ["name", "text"]),
    },
    {
        "name": "set_character_state",
        "description": "Set an NPC note's 'char state' frontmatter (alive, dead, torpor, missing, etc.) when it changes in play.",
        "input_schema": _schema({"name": {"type": "string"}, "state": {"type": "string"}}, ["name", "state"]),
    },
]

for _spec in TOOL_SPECS:
    _spec["eager_input_streaming"] = True


class ToolContext:
    """Binds tool implementations to one chronicle. Emits UI events via `emit`."""

    def __init__(self, db: DB, vault: Vault, chronicle_id: str, emit):
        self.db = db
        self.vault = vault
        self.cid = chronicle_id
        self.emit = emit  # callable(kind: str, payload: dict)

    def validate(self, name: str, args: Any) -> str | None:
        spec = next((s for s in TOOL_SPECS if s["name"] == name), None)
        if spec is None:
            return f"unknown tool {name!r}"
        if not isinstance(args, dict):
            return "tool input must be an object"
        missing = [k for k in spec["input_schema"]["required"] if k not in args]
        if missing:
            return f"missing required fields: {missing}"
        for k, v in args.items():
            prop = spec["input_schema"]["properties"].get(k)
            if prop is None:
                return f"unexpected field {k!r}"
            t = prop.get("type")
            ok = {
                "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
                "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                "string": lambda x: isinstance(x, str),
                "boolean": lambda x: isinstance(x, bool),
                "array": lambda x: isinstance(x, list),
                "object": lambda x: isinstance(x, dict),
            }.get(t)
            if ok and not ok(v):
                return f"field {k!r} should be {t}"
        return None

    def run(self, name: str, args: dict) -> str:
        err = self.validate(name, args)
        if err:
            raise ValueError(err)
        fn = getattr(self, f"t_{name}")
        return fn(**args)

    # ---------- dice & sheet ----------
    def t_roll_dice(self, pool, difficulty=6, label="", specialty=False, willpower=False, hidden=False):
        r = dice.roll(pool, difficulty, specialty, willpower)
        d = r.to_dict()
        d["label"] = label
        d["hidden"] = bool(hidden)
        d["summary"] = r.summary()
        shown = {"label": label, "hidden": True, "summary": "rolled behind the screen"} if hidden else d
        self.emit("dice", shown)
        return json.dumps(d)

    def t_read_sheet(self):
        ch = self.db.get_chronicle(self.cid)
        return json.dumps(ch["sheet"] or {"empty": True, "hint": "No sheet yet. Run character creation and call set_sheet."})

    def t_set_sheet(self, sheet):
        self.db.set_sheet(self.cid, sheet)
        self.emit("sheet", {"sheet": sheet})
        return "sheet saved"

    def t_update_trait(self, path, reason, delta=None, value=None):
        ch = self.db.get_chronicle(self.cid)
        sheet = ch["sheet"]
        parts = path.split(".")
        node = sheet
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ValueError(f"path {path!r} runs through a non-object")
        leaf = parts[-1]
        old = node.get(leaf)
        if delta is not None:
            base = old if isinstance(old, (int, float)) else 0
            new = base + delta
            new = int(new) if float(new).is_integer() else new
        elif value is not None:
            new = value
        else:
            raise ValueError("provide delta or value")
        node[leaf] = new
        self.db.set_sheet(self.cid, sheet)
        self.emit("trait", {"path": path, "old": old, "new": new, "reason": reason})
        self.emit("sheet", {"sheet": sheet})
        return json.dumps({"path": path, "old": old, "new": new})

    def t_set_scene(self, location, time, present=None, tension="calm", note=""):
        scene = {"location": location, "time": time, "present": present or [], "tension": tension, "note": note}
        self.db.set_scene(self.cid, scene)
        self.emit("scene", {"scene": scene})
        return "scene updated"

    # ---------- private notes ----------
    def t_write_st_note(self, key, text):
        ch = self.db.get_chronicle(self.cid)
        notes = ch["notes"]
        notes[key] = text
        self.db.set_notes(self.cid, notes)
        self.emit("tool", {"name": "write_st_note", "detail": f"updated private note '{key}'"})
        return f"note '{key}' saved"

    def t_read_st_notes(self, key=None):
        notes = self.db.get_chronicle(self.cid)["notes"]
        if key:
            return notes.get(key, f"(no note under '{key}')")
        return json.dumps(notes) if notes else "(no private notes yet)"

    # ---------- vault reads ----------
    def t_search_vault(self, query, folder=None, limit=8):
        hits = self.vault.search(query, limit=limit, folder=folder)
        self.emit("tool", {"name": "search_vault", "detail": f"searched the vault for “{query}”"})
        if not hits:
            return "no matches"
        return "\n".join(f"- [[{n.title}]] ({n.folder or 'root'}; tags: {', '.join(n.tags[:4])}) — {snip}" for n, _, snip in hits)

    def t_read_note(self, name, section=None):
        note = self.vault.resolve(name)
        if not note:
            hits = self.vault.search(name, limit=5)
            sugg = ", ".join(f"[[{n.title}]]" for n, _, _ in hits) or "none"
            return f"no note named {name!r}. Closest: {sugg}"
        self.emit("tool", {"name": "read_note", "detail": f"consulted [[{note.title}]]" + (f" § {section}" if section else "")})
        meta = {k: v for k, v in note.meta.items() if k not in ("tags", "aliases")}
        head = f"# {note.title}\n(path: {note.rel}; tags: {', '.join(note.tags)}; {json.dumps(meta, default=str)})\n\n"
        if section:
            sec = note.section(section)
            if sec is None:
                return head + f"(no section {section!r}; headings: {note.headings()})"
            return head + sec
        body = note.body
        if len(body) > 24000:
            body = body[:24000] + f"\n\n[truncated; {len(note.body) - 24000} more characters. Headings: {note.headings()}; ask for a section]"
        return head + body

    def t_list_notes(self, tag=None, folder=None):
        if tag:
            notes = self.vault.list_by_tag(tag)
        elif folder:
            notes = self.vault.list_folder(folder)
        else:
            return "provide tag or folder"
        self.emit("tool", {"name": "list_notes", "detail": f"listed notes ({tag or folder})"})
        if not notes:
            return "no notes"
        return "\n".join(f"- [[{n.title}]] — {n.summary_line}" for n in sorted(notes, key=lambda n: n.title))

    # ---------- vault writes ----------
    def t_write_session_log(self, title, content):
        ch = self.db.get_chronicle(self.cid)
        rel = self.vault.write_session_log(title, content, chronicle=ch["name"])
        self.emit("tool", {"name": "write_session_log", "detail": f"wrote {rel} to the vault"})
        return f"written to {rel}"

    def t_append_to_vault_note(self, name, text):
        rel = self.vault.append_to_note(name, text)
        self.emit("tool", {"name": "append_to_vault_note", "detail": f"added a chronicle note to {rel}"})
        return f"appended to {rel}"

    def t_set_character_state(self, name, state):
        rel = self.vault.set_frontmatter(name, "char state", state)
        self.emit("tool", {"name": "set_character_state", "detail": f"{rel}: char state → {state}"})
        return f"{rel}: char state = {state}"
