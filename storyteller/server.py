"""Local web server for the AI Storyteller. Run: uvicorn storyteller.server:app --reload"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, games
from .storyteller import Storyteller, public_state

app = FastAPI(title="AI Storyteller")
_st: Storyteller | None = None

# One turn at a time per chronicle. Different chronicles run side by side; a second
# request for the same chronicle while a turn is streaming is refused rather than
# letting two turns interleave in its history.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(cid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(cid, threading.Lock())


def exclusive(cid: str, gen: Iterator) -> Iterator:
    """Hold the chronicle's lock for as long as the event stream runs."""
    lock = _lock_for(cid)
    if not lock.acquire(blocking=False):
        gen.close()
        raise HTTPException(409, "This chronicle is busy with another turn. Wait for it to finish.")

    def run():
        try:
            yield from gen
        finally:
            lock.release()

    return run()


def st() -> Storyteller:
    global _st
    if _st is None:
        _st = Storyteller()
    return _st


class NewChronicle(BaseModel):
    name: str
    game: str = ""
    campaign: str = ""


class Turn(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/config")
def get_config():
    vaults = {g: str(v.root) for g, v in st().vaults.items()}
    return {
        "model": config.MODEL,
        "effort": config.EFFORT,
        "cache_ttl": config.CACHE_TTL,
        "compaction": {"enabled": config.ENABLE_COMPACTION, "at": config.COMPACT_AT, "session_end": config.COMPACT_AT_SESSION_END, "keep_turns": config.KEEP_RECENT_TURNS},
        "games": {
            gid: {
                "label": prof.label,
                "short": prof.short,
                "campaigns": {k: v["label"] for k, v in prof.campaigns.items()},
                "default_campaign": prof.default_campaign,
                "vault": vaults.get(gid),
            }
            for gid, prof in games.all_games().items()
        },
        "default_game": default_game(),
        "app_dir": str(config.APP_ROOT),
    }


def default_game() -> str:
    """ST_GAME when its vault was found; else Vampire when its vault was found; else the first game with a vault."""
    vaults = st().vaults
    if config.DEFAULT_GAME in vaults:
        return config.DEFAULT_GAME
    if "vtm" in vaults:
        return "vtm"
    return next(iter(vaults), "vtm")


@app.get("/api/chronicles")
def list_chronicles():
    return st().db.list_chronicles()


@app.post("/api/chronicles")
def create_chronicle(body: NewChronicle):
    game = body.game or default_game()
    try:
        prof = games.get(game)
    except KeyError:
        raise HTTPException(400, f"unknown game {game!r}")
    if game not in st().vaults:
        raise HTTPException(400, f"no vault is available for {prof.label}; see the README on vaults")
    campaign = body.campaign or prof.default_campaign
    if campaign not in prof.campaigns:
        raise HTTPException(400, "unknown campaign")
    ch = st().db.create_chronicle(body.name.strip() or "Untitled chronicle", campaign, game)
    ch.pop("notes", None)
    return ch


@app.delete("/api/chronicles/{cid}")
def delete_chronicle(cid: str):
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    lock = _lock_for(cid)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "This chronicle is busy with a turn. Wait for it to finish, then delete it.")
    try:
        st().db.delete_chronicle(cid)
    finally:
        lock.release()
    return {"deleted": cid}


@app.get("/api/chronicles/{cid}")
def get_chronicle(cid: str):
    ch = st().db.get_chronicle(cid)
    if not ch:
        raise HTTPException(404)
    events = [e for e in st().db.get_events(cid) if e["kind"] not in ("sheet", "scene")]  # legacy live events
    return {
        "id": ch["id"], "name": ch["name"], "campaign": ch["campaign"], "created": ch["created"],
        **public_state(ch),  # scene + rendered sheet; private notes never leave the server
        "events": events,
    }


def _sse(gen):
    for ev in gen:
        yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/chronicles/{cid}/begin")
def begin(cid: str):
    """Storyteller-initiated opening turn (character creation and the first scene)."""
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    return StreamingResponse(_sse(exclusive(cid, st().turn(cid, None))), media_type="text/event-stream")


@app.post("/api/chronicles/{cid}/turn")
def turn(cid: str, body: Turn):
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty")
    return StreamingResponse(_sse(exclusive(cid, st().turn(cid, text))), media_type="text/event-stream")


@app.post("/api/chronicles/{cid}/end_session")
def end_session(cid: str):
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    note = (
        "The player is ending the session now. Bring the current scene to a resting point in a short "
        "paragraph, then: call write_session_log with a full log of this session; update your 'recap' "
        "private note with what the next session needs; record durable NPC changes with "
        "append_to_vault_note or set_character_state; make sure the character's sheet and possessions are "
        "up to date. Finish with one or two lines to the player about "
        "where things stand and what their character might be thinking about until next time."
    )
    return StreamingResponse(
        _sse(exclusive(cid, st().turn(cid, "[Session end]", operator_note=note, compact_after=config.COMPACT_AT_SESSION_END))),
        media_type="text/event-stream",
    )


@app.post("/api/chronicles/{cid}/compact")
def compact(cid: str):
    """Condense the chronicle's memory now (the player asked, from the side panel)."""
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    return StreamingResponse(_sse(exclusive(cid, st().compact(cid, reason="requested"))), media_type="text/event-stream")
