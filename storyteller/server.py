"""Local web server for the AI Storyteller. Run: uvicorn storyteller.server:app --reload"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config
from .storyteller import Storyteller, public_state

app = FastAPI(title="AI Storyteller")
_st: Storyteller | None = None


def st() -> Storyteller:
    global _st
    if _st is None:
        _st = Storyteller()
    return _st


class NewChronicle(BaseModel):
    name: str
    campaign: str = config.DEFAULT_CAMPAIGN


class Turn(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "model": config.MODEL,
        "effort": config.EFFORT,
        "campaigns": {k: v["label"] for k, v in config.CAMPAIGNS.items()},
        "default_campaign": config.DEFAULT_CAMPAIGN,
        "vault": str(config.VAULT_DIR),
    }


@app.get("/api/chronicles")
def list_chronicles():
    return st().db.list_chronicles()


@app.post("/api/chronicles")
def create_chronicle(body: NewChronicle):
    if body.campaign not in config.CAMPAIGNS:
        raise HTTPException(400, "unknown campaign")
    ch = st().db.create_chronicle(body.name.strip() or "Untitled chronicle", body.campaign)
    return ch


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
    return StreamingResponse(_sse(st().turn(cid, None)), media_type="text/event-stream")


@app.post("/api/chronicles/{cid}/turn")
def turn(cid: str, body: Turn):
    if not st().db.get_chronicle(cid):
        raise HTTPException(404)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty")
    return StreamingResponse(_sse(st().turn(cid, text)), media_type="text/event-stream")


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
    return StreamingResponse(_sse(st().turn(cid, "[Session end]", operator_note=note)), media_type="text/event-stream")
