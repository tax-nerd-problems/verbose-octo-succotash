"""SQLite persistence: chronicles, API message history, UI transcript, state."""
from __future__ import annotations

import json
import sqlite3
import uuid
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


class DB:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or config.DB_PATH)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    # ---------- chronicles ----------
    def create_chronicle(self, name: str, campaign: str) -> dict:
        cid = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO chronicles (id, name, campaign, created) VALUES (?, ?, ?, ?)",
                (cid, name, campaign, datetime.now().isoformat(timespec="seconds")),
            )
        return self.get_chronicle(cid)

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
        for k in ("sheet", "scene", "notes"):
            d[k] = json.loads(d[k] or "{}")
        return d

    def _set_json(self, cid: str, column: str, value: dict) -> None:
        assert column in {"sheet", "scene", "notes"}
        with self._conn() as c:
            c.execute(f"UPDATE chronicles SET {column} = ? WHERE id = ?", (json.dumps(value, ensure_ascii=False), cid))

    def set_sheet(self, cid: str, sheet: dict) -> None:
        self._set_json(cid, "sheet", sheet)

    def set_scene(self, cid: str, scene: dict) -> None:
        self._set_json(cid, "scene", scene)

    def set_notes(self, cid: str, notes: dict) -> None:
        self._set_json(cid, "notes", notes)

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

    def get_events(self, cid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT seq, ts, kind, payload FROM events WHERE chronicle_id = ? ORDER BY seq", (cid,)
            ).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], **json.loads(r["payload"])} for r in rows]
