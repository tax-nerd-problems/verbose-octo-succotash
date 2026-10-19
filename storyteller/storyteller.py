"""The turn loop: build the cached system prompt, stream a turn, run tools, persist."""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Callable, Iterator

import anthropic

from . import config
from .db import DB
from .tools import TOOL_SPECS, ToolContext
from .vault import Vault, _read_text

Event = dict


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


def _state_message(ch: dict) -> dict:
    """Volatile state goes in a mid-conversation system message, after the cached prefix."""
    scene = ch.get("scene") or {}
    sheet = ch.get("sheet") or {}
    brief = {
        "scene": scene,
        "sheet_summary": {k: sheet.get(k) for k in ("name", "clan", "generation", "blood", "blood_max", "willpower", "willpower_max", "humanity", "health") if k in sheet} if sheet else "no sheet yet",
        "private_note_keys": sorted((ch.get("notes") or {}).keys()),
    }
    return {
        "role": "system",
        "content": "Storyteller screen (current state, not visible to the player): " + json.dumps(brief, ensure_ascii=False),
    }


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
        pending: list[Event] = []

        def emit(kind: str, payload: dict):
            pending.append(self.db.append_event(chronicle_id, kind, payload))

        ctx = ToolContext(self.db, self.vault, chronicle_id, emit)

        if player_text is not None:
            self.db.append_message(chronicle_id, "user", [{"type": "text", "text": player_text}])
            yield self.db.append_event(chronicle_id, "player", {"text": player_text})
        else:
            self.db.append_message(chronicle_id, "user", [{"type": "text", "text": "[The table is ready. Begin.]"}])
        state = _state_message(ch)
        if operator_note:
            state["content"] += "\n\n" + operator_note
        self.db.append_message(chronicle_id, "system", state["content"])

        rounds = 0
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
                if isinstance(e, anthropic.APIStatusError):
                    msg = f"API error {e.status_code}: {e.message}"
                elif isinstance(e, anthropic.APIConnectionError):
                    msg = f"connection error: {e}"
                else:  # unparseable tool JSON from the stream
                    msg = f"malformed tool call from the model: {e}"
                # Nothing was answered, so unwind what this round added and keep the
                # history valid (a system message may never be followed by a user message).
                if rounds == 1:
                    self.db.pop_last_messages(chronicle_id, 2 if player_text is not None else 2)
                yield {"kind": "error", "message": msg}
                break

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

        yield {"kind": "done", "state": self.db.get_chronicle(chronicle_id)}
