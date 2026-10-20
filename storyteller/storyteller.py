"""The turn loop: build the cached system prompt, stream a turn, run tools, persist."""
from __future__ import annotations

import glob
import hashlib
import json
import logging
from pathlib import Path
from typing import Iterator

import anthropic

from . import config
from .db import DB
from .sheet import sheet_view
from .tools import TOOL_SPECS, ToolContext, _item_line
from .vault import Vault, _read_text

Event = dict
log = logging.getLogger("storyteller")

# Transcript events are saved and replayed when a chronicle is reopened.
# "state" is live-only: it tells the browser to refresh the sheet and scene.
LIVE_ONLY = {"state"}


def _load(path: Path) -> str:
    return _read_text(path).strip()


def build_system(campaign: str, vault_root: Path | None = None) -> list[dict]:
    """System prompt as a list of text blocks. The whole prefix is stable, so the
    last block carries the cache breakpoint and every turn after the first reads
    it from cache."""
    camp = config.CAMPAIGNS[campaign]
    root = Path(vault_root or config.VAULT_DIR)
    parts = [_load(config.APP_DIR / "prompts" / "system.md")]
    docs = []
    for pattern in camp["core"]:
        for p in sorted(glob.glob(str(root / pattern))):
            p = Path(p)
            rel = p.relative_to(root).as_posix()
            docs.append(f"<document path=\"{rel}\">\n{_load(p)}\n</document>")
    parts.append("# The chronicle bible\n\n" + "\n\n".join(docs))
    parts.append(_load(Path(camp["premise"])))
    blocks = [{"type": "text", "text": t} for t in parts]
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def prefix_hash(system: list[dict], tools: list[dict]) -> str:
    blob = json.dumps({"system": system, "tools": tools}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def public_state(ch: dict) -> dict:
    """What the browser is allowed to see: never the Storyteller's private notes."""
    return {"scene": ch.get("scene") or {}, "view": sheet_view(ch.get("sheet"), ch.get("inventory"))}


def _state_message(ch: dict) -> str:
    """Volatile state goes in a mid-conversation system message, after the cached prefix."""
    scene = ch.get("scene") or {}
    sheet = ch.get("sheet") or {}
    items = (ch.get("inventory") or {}).get("items", [])
    keys = ("name", "clan", "generation", "blood", "blood_max", "willpower", "willpower_max", "humanity", "path", "health")
    brief = {
        "scene": scene,
        "sheet_summary": {k: sheet.get(k) for k in keys if k in sheet} if sheet else "no sheet yet",
        "possessions": [_item_line(it) for it in items] or "none recorded",
        "private_note_keys": sorted((ch.get("notes") or {}).keys()),
    }
    text = "Storyteller screen (current state, not visible to the player): " + json.dumps(brief, ensure_ascii=False)
    if sheet and not items:
        text += (
            "\nThe character's possessions are not recorded yet. At the next natural pause, record what they "
            "carry and own, based on the story so far, with set_inventory."
        )
    return text


def _is_thinking_binding_error(e: anthropic.APIStatusError) -> bool:
    if e.status_code != 400:
        return False
    msg = str(e)
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    msg += " " + str(err.get("message", ""))
    return "thinking" in msg and "signature" in msg


class Storyteller:
    def __init__(self, db: DB | None = None, vault: Vault | None = None, client: anthropic.Anthropic | None = None):
        self.db = db or DB()
        self.vault = vault or Vault()
        self.client = client or anthropic.Anthropic()
        self._system_cache: dict[str, list[dict]] = {}

    def system_for(self, campaign: str) -> list[dict]:
        if campaign not in self._system_cache:
            self._system_cache[campaign] = build_system(campaign, self.vault.root)
        return self._system_cache[campaign]

    def _request_kwargs(self, campaign: str, messages: list[dict]) -> dict:
        kw: dict = dict(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=self.system_for(campaign),
            tools=TOOL_SPECS,
            messages=messages,
            output_config={"effort": config.EFFORT},
        )
        betas = []
        if config.ENABLE_COMPACTION:
            betas.append("compact-2026-01-12")
            kw["context_management"] = {"edits": [{"type": "compact_20260112"}]}
        if config.ENABLE_FALLBACKS:
            betas.append("server-side-fallback-2026-07-01")
            kw["fallbacks"] = "default"
        if betas:
            kw["betas"] = betas
        return kw

    def _sync_prefix(self, ch: dict) -> None:
        """Thinking blocks are bound to the exact system prompt and tool list that produced
        them. If either changed since this chronicle's last turn (an app update, or an edit to
        a note in the cached prompt), drop the old blocks once so the API keeps accepting the
        history."""
        h = prefix_hash(self.system_for(ch["campaign"]), TOOL_SPECS)
        if ch.get("prefix_hash") == h:
            return
        if self.db.has_messages(ch["id"]):
            n = self.db.strip_thinking(ch["id"])
            log.info("chronicle %s: Storyteller instructions changed; cleared %d old thinking blocks", ch["id"], n)
        self.db.set_prefix_hash(ch["id"], h)

    def turn(self, chronicle_id: str, player_text: str | None, operator_note: str | None = None) -> Iterator[Event]:
        """Run one player turn. Yields UI events as they happen.

        player_text: what the player typed (None for a Storyteller-initiated turn).
        operator_note: optional extra system instruction for this turn (e.g. session end).
        """
        ch = self.db.get_chronicle(chronicle_id)
        if not ch:
            yield {"kind": "error", "message": "no such chronicle"}
            return
        campaign = ch["campaign"]
        self._sync_prefix(ch)
        pending: list[Event] = []

        def emit(kind: str, payload: dict):
            if kind in LIVE_ONLY:
                pending.append({"kind": "state", **public_state(self.db.get_chronicle(chronicle_id))})
            else:
                pending.append(self.db.append_event(chronicle_id, kind, payload))

        ctx = ToolContext(self.db, self.vault, chronicle_id, emit)

        player_event = None
        if player_text is not None:
            self.db.append_message(chronicle_id, "user", [{"type": "text", "text": player_text}])
            player_event = self.db.append_event(chronicle_id, "player", {"text": player_text})
            yield player_event
        else:
            self.db.append_message(chronicle_id, "user", [{"type": "text", "text": "[The table is ready. Begin.]"}])
        state = _state_message(ch)
        if operator_note:
            state += "\n\n" + operator_note
        self.db.append_message(chronicle_id, "system", state)

        rounds = 0
        healed = False
        answered = False  # has any response been saved for this turn?
        while True:
            rounds += 1
            if rounds > 12:
                yield {"kind": "error", "message": "too many tool rounds in one turn; stopping"}
                break
            messages = self.db.get_messages(chronicle_id)
            text_buf: list[str] = []
            try:
                with self.client.beta.messages.stream(**self._request_kwargs(campaign, messages)) as stream:
                    for event in stream:
                        if event.type == "text":
                            text_buf.append(event.text)
                            yield {"kind": "delta", "text": event.text}
                        elif event.type == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
                            yield {"kind": "working", "name": event.content_block.name}
                    response = stream.get_final_message()
            except (ValueError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                if isinstance(e, anthropic.APIStatusError) and not healed and _is_thinking_binding_error(e):
                    # Something before an old thinking block changed. Drop the blocks once and retry.
                    healed = True
                    n = self.db.strip_thinking(chronicle_id)
                    log.warning("chronicle %s: API rejected old thinking blocks; cleared %d and retrying", chronicle_id, n)
                    rounds -= 1
                    continue
                if isinstance(e, anthropic.APIStatusError):
                    msg = f"API error {e.status_code}: {e.message}"
                elif isinstance(e, anthropic.APIConnectionError):
                    msg = f"connection error: {e}"
                else:  # unparseable tool JSON from the stream
                    msg = f"malformed tool call from the model: {e}"
                err = {"kind": "error", "message": msg}
                if not answered:
                    # Nothing was answered, so unwind what this turn added and keep the history
                    # valid (a system message may never be followed by a user message).
                    self.db.pop_last_messages(chronicle_id, 2)
                    if player_event:
                        self.db.delete_event(chronicle_id, player_event["seq"])
                        err["restore_text"] = player_text
                        err["unsent_seq"] = player_event["seq"]
                yield err
                break

            answered = True
            content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
            self.db.append_message(chronicle_id, "assistant", content)
            narration = "".join(text_buf).strip()
            if narration:
                yield self.db.append_event(chronicle_id, "narration", {"text": narration})

            for b in response.content:
                if b.type == "fallback":
                    yield self.db.append_event(chronicle_id, "system", {"text": f"Rerouted: {b.from_.model} declined this turn, {b.to.model} continued."})

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "refusal":
                yield self.db.append_event(chronicle_id, "system", {"text": "The model declined to continue this turn. Rephrase and try again."})
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    yield self.db.append_event(chronicle_id, "system", {"text": "Output hit the token limit; say 'continue' to go on."})
                break
            if response.stop_reason == "max_tokens":
                yield {"kind": "error", "message": "tool input was cut off by the token limit; try again"}
                # Answer the dangling tool_use so the history stays valid.
                self.db.append_message(chronicle_id, "user", [
                    {"type": "tool_result", "tool_use_id": t.id, "is_error": True, "content": "truncated"} for t in tool_uses
                ])
                break

            results = []
            for t in tool_uses:
                try:
                    out = ctx.run(t.name, t.input)
                    results.append({"type": "tool_result", "tool_use_id": t.id, "content": out})
                except Exception as e:  # tool errors go back to the model, not to the player
                    results.append({"type": "tool_result", "tool_use_id": t.id, "is_error": True, "content": f"{type(e).__name__}: {e}"})
                while pending:
                    yield pending.pop(0)
            self.db.append_message(chronicle_id, "user", results)

        yield {"kind": "done", **public_state(self.db.get_chronicle(chronicle_id))}
