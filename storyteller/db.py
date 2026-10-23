"""SQLite persistence: chronicles, API message history, UI transcript, state."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Iterator
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS chronicles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    campaign TEXT NOT NULL,
    created TEXT NOT NULL,
    sheet TEXT NOT NULL DEFAULT '{}',
    scene TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    chronicle_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (chronicle_id, seq)
);
CREATE TABLE IF NOT EXISTS events (
    chronicle_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (chronicle_id, seq)
);
"""


# Columns added after the first release; created on startup for older databases.
MIGRATIONS = {
    "inventory": "ALTER TABLE chronicles ADD COLUMN inventory TEXT NOT NULL DEFAULT '{}'",
    "prefix_hash": "ALTER TABLE chronicles ADD COLUMN prefix_hash TEXT NOT NULL DEFAULT ''",
    "usage": "ALTER TABLE chronicles ADD COLUMN usage TEXT NOT NULL DEFAULT '{}'",
    "recap_due": "ALTER TABLE chronicles ADD COLUMN recap_due INTEGER NOT NULL DEFAULT 0",
}

THINKING_TYPES = {"thinking", "redacted_thinking"}


class DB:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or config.DB_PATH)
        with self._conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(chronicles)")}
            for col, sql in MIGRATIONS.items():
                if col not in have:
                    c.execute(sql)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation: commit on success, always close."""
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    # ---------- chronicles ----------
    def create_chronicle(self, name: str, campaign: str) -> dict:
        cid = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO chronicles (id, name, campaign, created) VALUES (?, ?, ?, ?)",
                (cid, name, campaign, datetime.now().isoformat(timespec="seconds")),
            )
        return self.get_chronicle(cid)

    def delete_chronicle(self, cid: str) -> None:
        """Remove a chronicle and everything the app stored for it (the vault is untouched)."""
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE chronicle_id = ?", (cid,))
            c.execute("DELETE FROM events WHERE chronicle_id = ?", (cid,))
            c.execute("DELETE FROM chronicles WHERE id = ?", (cid,))

    def list_chronicles(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id, name, campaign, created FROM chronicles ORDER BY created DESC").fetchall()
        return [dict(r) for r in rows]

    def get_chronicle(self, cid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM chronicles WHERE id = ?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("sheet", "scene", "notes", "inventory", "usage"):
            d[k] = json.loads(d.get(k) or "{}")
        if isinstance(d["inventory"], list):  # tolerate a bare list
            d["inventory"] = {"seq": len(d["inventory"]), "items": d["inventory"]}
        d["inventory"].setdefault("seq", 0)
        d["inventory"].setdefault("items", [])
        return d

    def _set_json(self, cid: str, column: str, value: dict) -> None:
        assert column in {"sheet", "scene", "notes", "inventory", "usage"}
        with self._conn() as c:
            c.execute(f"UPDATE chronicles SET {column} = ? WHERE id = ?", (json.dumps(value, ensure_ascii=False), cid))

    def set_sheet(self, cid: str, sheet: dict) -> None:
        self._set_json(cid, "sheet", sheet)

    def set_scene(self, cid: str, scene: dict) -> None:
        self._set_json(cid, "scene", scene)

    def set_notes(self, cid: str, notes: dict) -> None:
        self._set_json(cid, "notes", notes)

    def set_inventory(self, cid: str, inventory: dict) -> None:
        self._set_json(cid, "inventory", inventory)

    def set_prefix_hash(self, cid: str, value: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE chronicles SET prefix_hash = ? WHERE id = ?", (value, cid))

    def set_usage(self, cid: str, usage: dict) -> None:
        self._set_json(cid, "usage", usage)

    def set_recap_due(self, cid: str, due: bool) -> None:
        with self._conn() as c:
            c.execute("UPDATE chronicles SET recap_due = ? WHERE id = ?", (1 if due else 0, cid))

    # ---------- API history ----------
    def get_messages(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content FROM messages WHERE chronicle_id = ? ORDER BY seq", (cid,)
            ).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def append_message(self, cid: str, role: str, content) -> None:
        with self._conn() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE chronicle_id = ?", (cid,)).fetchone()[0]
            c.execute(
                "INSERT INTO messages (chronicle_id, seq, role, content) VALUES (?, ?, ?, ?)",
                (cid, seq, role, json.dumps(content, ensure_ascii=False)),
            )

    def strip_thinking(self, cid: str) -> int:
        """Remove every thinking block from the stored history. Returns how many were removed.

        A thinking block is only valid in the exact conversation that produced it,
        including the system prompt and tool list. When those change (an app update,
        or an edit to a vault note in the cached prompt), the API rejects old blocks on
        accounts where the check is enforced. Dropping them once keeps the chronicle
        playable; the narration, tool calls and results all stay.
        """
        removed = 0
        with self._conn() as c:
            rows = c.execute(
                "SELECT seq, content FROM messages WHERE chronicle_id = ? AND role = 'assistant'", (cid,)
            ).fetchall()
            for r in rows:
                content = json.loads(r["content"])
                if not isinstance(content, list):
                    continue
                kept = [b for b in content if not (isinstance(b, dict) and b.get("type") in THINKING_TYPES)]
                if len(kept) != len(content):
                    removed += len(content) - len(kept)
                    if not kept:
                        kept = [{"type": "text", "text": "…"}]
                    c.execute(
                        "UPDATE messages SET content = ? WHERE chronicle_id = ? AND seq = ?",
                        (json.dumps(kept, ensure_ascii=False), cid, r["seq"]),
                    )
        return removed

    def replace_messages(self, cid: str, messages: list[dict]) -> None:
        """Replace the whole API history (used after the API condenses it into a summary block)."""
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE chronicle_id = ?", (cid,))
            for seq, m in enumerate(messages, 1):
                c.execute(
                    "INSERT INTO messages (chronicle_id, seq, role, content) VALUES (?, ?, ?, ?)",
                    (cid, seq, m["role"], json.dumps(m["content"], ensure_ascii=False)),
                )

    def convert_legacy_summary(self, cid: str) -> int:
        """Rewrite a history that still carries a summary block made by the first release.

        The first release let the API summarize at a size threshold; the summary came
        back as a `compaction` block inside an assistant message, mid-history, and the
        API only recognised it under that release's beta header. Later releases
        condense differently and no longer send that header, so the old block is
        rejected ("Input tag 'compaction' ... does not match"). This turns the last
        such summary into plain text at the front of the history, drops the messages
        it summarized (the API was already discarding them), and removes any failed
        summary blocks. Returns the number of blocks handled (0 = nothing to do).
        """
        messages = self.get_messages(cid)
        hits = []
        for i, m in enumerate(messages):
            if not isinstance(m["content"], list):
                continue
            for j, b in enumerate(m["content"]):
                if isinstance(b, dict) and b.get("type") == "compaction" and not (i == 0 and j == 0):
                    hits.append((i, j, b))
        if not hits:
            return 0
        real = [(i, j, b) for i, j, b in hits if b.get("content")]
        if real:
            i, j, block = real[-1]  # the last summary covers everything before it
            rest = [b for k, b in enumerate(messages[i]["content"]) if k != j and not (isinstance(b, dict) and b.get("type") in THINKING_TYPES)]
            summary_text = (
                "[Chronicle so far, condensed by the Storyteller's memory before this point:]\n\n"
                + str(block["content"]).strip()
            )
            new = [{"role": "user", "content": [{"type": "text", "text": summary_text}]}]
            if rest:
                new.append({"role": "assistant", "content": rest})
            new.extend(messages[i + 1:])
        else:
            new = messages
        # Failed summaries (content null) are no-ops server-side; just drop them.
        cleaned = []
        for m in new:
            if isinstance(m["content"], list):
                kept = [b for b in m["content"] if not (isinstance(b, dict) and b.get("type") == "compaction" and not b.get("content"))]
                if not kept:
                    continue
                m = {**m, "content": kept}
            cleaned.append(m)
        self.replace_messages(cid, cleaned)
        self.strip_thinking(cid)  # the history changed, so earlier reasoning is no longer bound to it
        return len(hits)

    def has_messages(self, cid: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM messages WHERE chronicle_id = ? LIMIT 1", (cid,)).fetchone() is not None

    def pop_last_messages(self, cid: str, n: int) -> None:
        """Remove the last n history entries (used to unwind a turn that never got a response)."""
        with self._conn() as c:
            c.execute(
                "DELETE FROM messages WHERE chronicle_id = ? AND seq > (SELECT COALESCE(MAX(seq), 0) - ? FROM messages WHERE chronicle_id = ?)",
                (cid, n, cid),
            )

    # ---------- UI transcript ----------
    def append_event(self, cid: str, kind: str, payload: dict) -> dict:
        with self._conn() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE chronicle_id = ?", (cid,)).fetchone()[0]
            ts = datetime.now().isoformat(timespec="seconds")
            c.execute(
                "INSERT INTO events (chronicle_id, seq, ts, kind, payload) VALUES (?, ?, ?, ?, ?)",
                (cid, seq, ts, kind, json.dumps(payload, ensure_ascii=False)),
            )
        return {"seq": seq, "ts": ts, "kind": kind, **payload}

    def delete_event(self, cid: str, seq: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM events WHERE chronicle_id = ? AND seq = ?", (cid, seq))

    def get_events(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT seq, ts, kind, payload FROM events WHERE chronicle_id = ? ORDER BY seq", (cid,)
            ).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], **json.loads(r["payload"])} for r in rows]
