"""Tool definitions (JSON schema) and their implementations.

Every tool the model can call is declared in TOOL_SPECS and dispatched by
ToolContext.run(). Results are returned as strings (JSON where structured) so
they can go straight into a tool_result block.
"""
from __future__ import annotations

import json
from typing import Any

from . import dice, games
from .db import DB
from .games import GameProfile
from .sheet import LOCATION_KEYS, LOCATION_LABELS, _location, as_int as _as_int
from .vault import Vault


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def tool_specs(profile: GameProfile) -> list[dict]:
    """The tool list for one game: the shared tools with that game's wording, plus its own tools."""
    specs = [json.loads(json.dumps(t)) for t in _BASE_SPECS]  # deep copy
    for t in specs:
        if t["name"] == "roll_dice":
            t["description"] = t["description"].replace("{DICE_DOC}", profile.dice_doc)
        elif t["name"] == "set_sheet":
            t["description"] = t["description"].replace("{SHEET_DOC}", profile.sheet_doc)
        elif t["name"] == "update_trait":
            t["description"] = t["description"].replace("{TRAIT_DOC}", profile.trait_doc).replace("{CAP_DOC}", profile.cap_doc)
        elif t["name"] == "list_notes":
            t["description"] = t["description"].replace("{LIST_DOC}", profile.list_doc)
    specs.extend(json.loads(json.dumps(t)) for t in profile.extra_tools)
    for t in specs:
        t["eager_input_streaming"] = True
    return specs


_BASE_SPECS: list[dict] = [
    {
        "name": "roll_dice",
        "description": "{DICE_DOC}",
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
            "Replace the player's character sheet. Call once character creation is agreed, or for a large "
            "revision. Keep every value you already track. Use this layout (lowercase keys, snake_case for "
            "multi-word trait names; include only abilities with dots; secondary abilities go in the matching group):\n"
            "{SHEET_DOC} "
            "Possessions are not part of the sheet: track them with set_inventory and update_inventory."
        ),
        "input_schema": _schema({"sheet": {"type": "object", "description": "The full character sheet."}}, ["sheet"]),
    },
    {
        "name": "update_trait",
        "description": (
            "Adjust one value on the player's sheet. Use dotted paths into the sheet layout, {TRAIT_DOC} "
            "Give delta (numeric change) or value (replacement, any JSON type). Numbers never go below 0{CAP_DOC} "
            "Damage: add to health.bashing, health.lethal or health.aggravated after soak; healing subtracts."
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
        "name": "set_inventory",
        "description": (
            "Replace the character's whole list of possessions. Use at character creation, or to reconcile "
            "the list with the story. Location is one of: held (in hand), worn, pockets, bag (anything carried "
            "in a bag, case, or purse), haven, vehicle, elsewhere. Put exactly where in detail (e.g. 'inside "
            "jacket pocket', 'glovebox of the Civic', 'safe-deposit box, Chase on 5th'). Use notes for condition, "
            "weapon stats, provenance, or anything the player should remember about the item."
        ),
        "input_schema": _schema(
            {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "location": {"type": "string", "enum": LOCATION_KEYS},
                            "qty": {"type": "integer", "minimum": 1},
                            "detail": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["name", "location"],
                        "additionalProperties": False,
                    },
                },
                "reason": {"type": "string", "description": "Optional line for the player, e.g. 'Starting possessions.'"},
            },
            ["items"],
        ),
    },
    {
        "name": "update_inventory",
        "description": (
            "Change one possession, in the same turn the story changes it. Actions: add (gained, bought, "
            "stolen, handed over; adds to an identical stack), remove (used up, lost, given away, destroyed; "
            "qty removes part of a stack), move (put down, stashed, picked up, changed pocket; qty moves part "
            "of a stack), update (change qty, notes or detail, or rename). Refer to an existing item by the id "
            "on your screen (e.g. 'i4') or its exact name. Always include a short reason the player will see."
        ),
        "input_schema": _schema(
            {
                "action": {"type": "string", "enum": ["add", "remove", "move", "update"]},
                "item": {"type": "string", "description": "For add, the new item's name; otherwise an id like 'i4' or the item's name."},
                "location": {"type": "string", "enum": LOCATION_KEYS, "description": "Required for add and move."},
                "detail": {"type": "string"},
                "qty": {"type": "integer", "minimum": 0},
                "notes": {"type": "string"},
                "rename": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["action", "item", "reason"],
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
        "description": "List notes by tag ({LIST_DOC}) or by folder. Returns titles and one-line summaries.",
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

# The VTM list, for callers that do not pass a profile.
TOOL_SPECS: list[dict] = tool_specs(games.get("vtm"))


def _item_line(it: dict) -> str:
    qty = f" ×{it['qty']}" if it.get("qty", 1) != 1 else ""
    where = it["location"] + (f": {it['detail']}" if it.get("detail") else "")
    return f"{it['id']} {it['name']}{qty} [{where}]"


class ToolContext:
    """Binds tool implementations to one chronicle. Emits UI events via `emit`."""

    def __init__(self, db: DB, vault: Vault, chronicle_id: str, emit, chronicle_name: str | None = None,
                 profile: GameProfile | None = None):
        self.db = db
        self.vault = vault
        self.cid = chronicle_id
        self.emit = emit  # callable(kind: str, payload: dict)
        ch = (db.get_chronicle(chronicle_id) or {}) if db is not None else {}
        if chronicle_name is None:
            chronicle_name = ch.get("name", "")
        self.name = chronicle_name or ""  # vault reads and writes are scoped to this chronicle
        self.profile = profile or games.get(ch.get("game") or "vtm")
        self.specs = tool_specs(self.profile)

    def validate(self, name: str, args: Any) -> str | None:
        spec = next((s for s in self.specs if s["name"] == name), None)
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
            if "enum" in prop and v not in prop["enum"]:
                return f"field {k!r} must be one of {prop['enum']}"
        return None

    def run(self, name: str, args: dict) -> str:
        err = self.validate(name, args)
        if err:
            raise ValueError(err)
        impl = self.profile.extra_impls.get(name)
        if impl is not None:
            return impl(self, **args)
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
        sheet = ch["sheet"] or {"empty": True, "hint": "No sheet yet. Run character creation and call set_sheet."}
        lines = [json.dumps(sheet, ensure_ascii=False), "", "Possessions (tracked separately; change with update_inventory):"]
        items = ch["inventory"]["items"]
        lines += [f"- {_item_line(it)}" + (f" — {it['notes']}" if it.get("notes") else "") for it in items] or ["- none recorded"]
        return "\n".join(lines)

    def t_set_sheet(self, sheet):
        dropped = [k for k in list(sheet) if k.lower() in ("inventory", "possessions")]
        for k in dropped:
            sheet.pop(k)
        self.db.set_sheet(self.cid, sheet)
        self.emit("state", {})
        if dropped:
            return f"sheet saved; the {', '.join(dropped)} key was not saved: record possessions with set_inventory"
        return "sheet saved"

    def t_update_trait(self, path, reason, delta=None, value=None):
        ch = self.db.get_chronicle(self.cid)
        sheet = ch["sheet"]
        parts = [p for p in path.split(".") if p]
        if not parts:
            raise ValueError("empty path")
        node = sheet
        walked = []
        for p in parts[:-1]:
            walked.append(p)
            if p not in node:
                node[p] = {}
            node = node[p]
            if not isinstance(node, dict):
                where = ".".join(walked)
                raise ValueError(
                    f"'{where}' is currently {json.dumps(node)}, not an object. Replace it first, e.g. "
                    f"update_trait(path='{where}', value={{...}}), then retry."
                )
        leaf = parts[-1]
        old = node.get(leaf)
        clamped = False
        if delta is not None:
            if old is not None and not isinstance(old, (int, float)):
                raise ValueError(f"'{path}' is {json.dumps(old)}, not a number; use value instead of delta")
            new = (old or 0) + delta
            new = int(new) if float(new).is_integer() else new
        elif value is not None:
            new = value
        else:
            raise ValueError("provide delta or value")
        if isinstance(new, (int, float)) and not isinstance(new, bool):
            if new < 0:
                new, clamped = 0, True
            cap = self.profile.caps.get(leaf) if len(parts) == 1 else None
            if isinstance(cap, str):
                limit = _as_int(sheet.get(cap))
            elif isinstance(cap, tuple) and cap[0] == "wheel":
                limit = cap[1] - (_as_int(sheet.get(cap[2])) or 0)
            else:
                limit = None
            if limit is not None and new > limit:
                new, clamped = limit, True
        node[leaf] = new
        also = self.profile.reconcile(sheet) if self.profile.reconcile else []
        self.db.set_sheet(self.cid, sheet)
        self.emit("trait", {"path": path, "old": old, "new": new, "reason": reason + (" (" + "; ".join(also) + ")" if also else "")})
        self.emit("state", {})
        out = {"path": path, "old": old, "new": new}
        if clamped:
            out["clamped"] = True
        if also:
            out["also"] = also
        return json.dumps(out)

    # ---------- possessions ----------
    def _clean_item(self, raw: dict, seq: int) -> dict:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
            raise ValueError(f"each item needs a name: {json.dumps(raw)}")
        qty = raw.get("qty", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            qty = 1
        loc_in = raw.get("location")
        loc = _location(loc_in)
        detail = (raw.get("detail") or "").strip()
        if loc == "elsewhere" and loc_in and str(loc_in).lower() != "elsewhere" and not detail:
            detail = str(loc_in)
        return {"id": f"i{seq}", "name": raw["name"].strip(), "location": loc, "qty": qty,
                "detail": detail, "notes": (raw.get("notes") or "").strip()}

    def t_set_inventory(self, items, reason=""):
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        inv = self.db.get_chronicle(self.cid)["inventory"]
        seq = inv["seq"]
        new_items = []
        for raw in items:
            seq += 1
            new_items.append(self._clean_item(raw, seq))
        self.db.set_inventory(self.cid, {"seq": seq, "items": new_items})
        self.emit("item", {"op": "set", "count": len(new_items), "reason": reason or "Possessions recorded"})
        self.emit("state", {})
        return "possessions saved:\n" + "\n".join(f"- {_item_line(it)}" for it in new_items)

    @staticmethod
    def _find(items: list[dict], ref: str) -> dict:
        ref_l = ref.strip().lower()
        for it in items:
            if it["id"].lower() == ref_l:
                return it
        exact = [it for it in items if it["name"].lower() == ref_l]
        if len(exact) == 1:
            return exact[0]
        partial = exact or [it for it in items if ref_l in it["name"].lower()]
        if len(partial) == 1:
            return partial[0]
        listing = "; ".join(_item_line(it) for it in (partial or items)) or "nothing recorded"
        if partial:
            raise ValueError(f"'{ref}' matches several items, use an id: {listing}")
        raise ValueError(f"no possession matches '{ref}'. Current possessions: {listing}")

    def t_update_inventory(self, action, item, reason, location=None, detail=None, qty=None, notes=None, rename=None):
        inv = self.db.get_chronicle(self.cid)["inventory"]
        items = inv["items"]
        ev = {"op": action, "reason": reason}
        if action == "add":
            if not location:
                raise ValueError("add needs a location")
            loc = _location(location)
            n = qty if isinstance(qty, int) and qty > 0 else 1
            same = next((it for it in items if it["name"].lower() == item.strip().lower()
                         and it["location"] == loc and (it.get("detail") or "") == (detail or "").strip()), None)
            if same:
                same["qty"] += n
                if notes:
                    same["notes"] = notes.strip()
                target = same
            else:
                inv["seq"] += 1
                target = self._clean_item({"name": item, "location": loc, "qty": n, "detail": detail, "notes": notes}, inv["seq"])
                items.append(target)
            ev.update(name=target["name"], qty=n, to=LOCATION_LABELS[target["location"]], detail=target["detail"])
        elif action == "remove":
            target = self._find(items, item)
            if isinstance(qty, int) and 0 < qty < target["qty"]:
                target["qty"] -= qty
                ev.update(name=target["name"], qty=qty, left=target["qty"])
            else:
                items.remove(target)
                ev.update(name=target["name"], qty=target["qty"])
        elif action == "move":
            if not location:
                raise ValueError("move needs a location")
            target = self._find(items, item)
            loc = _location(location)
            from_label = LOCATION_LABELS[target["location"]]
            if isinstance(qty, int) and 0 < qty < target["qty"]:
                target["qty"] -= qty
                inv["seq"] += 1
                moved = dict(target, id=f"i{inv['seq']}", qty=qty, location=loc, detail=(detail or "").strip())
                items.append(moved)
                target = moved
            else:
                target["location"] = loc
                if detail is not None:
                    target["detail"] = detail.strip()
            ev.update(name=target["name"], qty=qty, **{"from": from_label}, to=LOCATION_LABELS[loc], detail=target["detail"])
        elif action == "update":
            target = self._find(items, item)
            changes = []
            if rename:
                changes.append(f"renamed from {target['name']}")
                target["name"] = rename.strip()
            if isinstance(qty, int):
                if qty <= 0:
                    items.remove(target)
                    changes.append("none left")
                else:
                    changes.append(f"qty {target['qty']} → {qty}")
                    target["qty"] = qty
            if detail is not None:
                target["detail"] = detail.strip()
                changes.append("location detail")
            if notes is not None:
                target["notes"] = notes.strip()
                changes.append("notes")
            ev.update(name=target["name"], changes=", ".join(changes))
        else:
            raise ValueError(f"unknown action {action!r}")
        self.db.set_inventory(self.cid, inv)
        self.emit("item", ev)
        self.emit("state", {})
        return json.dumps({"ok": True, "possessions": [_item_line(it) for it in items]}, ensure_ascii=False)

    def t_set_scene(self, location, time, present=None, tension="calm", note=""):
        scene = {"location": location, "time": time, "present": present or [], "tension": tension, "note": note}
        self.db.set_scene(self.cid, scene)
        self.emit("state", {})
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
        hits = self.vault.search(query, limit=limit, folder=folder, chronicle=self.name)
        self.emit("tool", {"name": "search_vault", "detail": f"searched the vault for “{query}”"})
        if not hits:
            return "no matches"
        return "\n".join(f"- [[{n.title}]] ({n.folder or 'root'}; tags: {', '.join(n.tags[:4])}) — {snip}" for n, _, snip in hits)

    def t_read_note(self, name, section=None):
        note = self.vault.resolve(name, self.name or None)
        if not note or not self.vault.belongs(note, self.name):
            hits = self.vault.search(name, limit=5, chronicle=self.name)
            sugg = ", ".join(f"[[{n.title}]]" for n, _, _ in hits) or "none"
            return f"no note named {name!r}. Closest: {sugg}"
        self.emit("tool", {"name": "read_note", "detail": f"consulted [[{note.title}]]" + (f" § {section}" if section else "")})
        meta = self.vault.meta_for(note, self.name)
        head = f"# {note.title}\n(path: {note.rel}; tags: {', '.join(note.tags)}; {json.dumps(meta, default=str)})\n\n"
        if section:
            sec = note.section(section)
            if sec is None:
                return head + f"(no section {section!r}; headings: {note.headings()})"
            return head + sec
        body = self.vault.body_for(note, self.name)
        if len(body) > 24000:
            body = body[:24000] + f"\n\n[truncated; {len(body) - 24000} more characters. Headings: {note.headings()}; ask for a section]"
        return head + body

    def t_list_notes(self, tag=None, folder=None):
        if tag:
            notes = self.vault.list_by_tag(tag, chronicle=self.name)
        elif folder:
            notes = self.vault.list_folder(folder, chronicle=self.name)
        else:
            return "provide tag or folder"
        self.emit("tool", {"name": "list_notes", "detail": f"listed notes ({tag or folder})"})
        if not notes:
            return "no notes"
        return "\n".join(f"- [[{n.title}]] — {n.summary_line}" for n in sorted(notes, key=lambda n: n.title))

    # ---------- vault writes ----------
    def t_write_session_log(self, title, content):
        rel = self.vault.write_session_log(title, content, chronicle=self.name)
        self.emit("tool", {"name": "write_session_log", "detail": f"wrote {rel} to the vault"})
        return f"written to {rel}"

    def t_append_to_vault_note(self, name, text):
        rel = self.vault.append_to_note(name, text, chronicle=self.name)
        self.emit("tool", {"name": "append_to_vault_note", "detail": f"added a chronicle note to {rel}"})
        return f"appended to {rel}"

    def t_set_character_state(self, name, state):
        rel = self.vault.set_character_state(name, state, chronicle=self.name)
        self.emit("tool", {"name": "set_character_state", "detail": f"{rel}: char state → {state}"})
        return f"{rel}: char state = {state}"
