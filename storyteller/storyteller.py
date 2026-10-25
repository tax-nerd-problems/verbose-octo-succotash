"""The turn loop: build the cached system prompt, stream a turn, run tools, persist."""
from __future__ import annotations

import glob
import hashlib
import json
import logging
from pathlib import Path
from typing import Iterator

import anthropic
import httpx2

from . import config, games, meter
from .db import DB
from .games import GameProfile
from .sheet import sheet_view
from .tools import ToolContext, _item_line, tool_specs
from .vault import Vault, _read_text

Event = dict
log = logging.getLogger("storyteller")

# Transcript events are saved and replayed when a chronicle is reopened.
# "state" is live-only: it tells the browser to refresh the sheet and scene.
LIVE_ONLY = {"state"}


def _load(path: Path) -> str:
    return _read_text(path).strip()


def build_system(profile: GameProfile | str, campaign: str, vault_root: Path | None = None) -> list[dict]:
    """System prompt as a list of text blocks. The whole prefix is stable, so the
    last block carries the cache breakpoint and every turn after the first reads
    it from cache."""
    if isinstance(profile, str):  # an old caller passing a campaign id
        profile, campaign = games.get("vtm"), profile
    camp = profile.campaigns[campaign]
    root = Path(vault_root or config.VAULT_DIR)
    parts = [_load(profile.prompts_dir / "system.md")]
    docs = []
    for pattern in camp["core"]:
        for p in sorted(glob.glob(str(root / pattern))):
            p = Path(p)
            rel = p.relative_to(root).as_posix()
            docs.append(f"<document path=\"{rel}\">\n{_load(p)}\n</document>")
    parts.append("# The chronicle bible\n\n" + "\n\n".join(docs))
    parts.append(_load(profile.prompts_dir / camp["premise"]))
    blocks = [{"type": "text", "text": t} for t in parts]
    blocks[-1]["cache_control"] = cache_marker()
    return blocks


def cache_marker() -> dict:
    """The prefix and the conversation tail use the same lifetime (longer lifetimes must come first)."""
    return {"type": "ephemeral", "ttl": "1h"} if config.CACHE_TTL == "1h" else {"type": "ephemeral"}


def prefix_hash(system: list[dict], tools: list[dict]) -> str:
    # cache_control markers are not part of what thinking blocks are bound to.
    plain = [{k: v for k, v in b.items() if k != "cache_control"} for b in system]
    blob = json.dumps({"system": plain, "tools": tools}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def profile_of(ch: dict) -> GameProfile:
    return games.get(ch.get("game") or "vtm")


def public_state(ch: dict) -> dict:
    """What the browser is allowed to see: never the Storyteller's private notes."""
    profile = profile_of(ch)
    return {
        "game": profile.id,
        "game_label": profile.label,
        "scene": ch.get("scene") or {},
        "view": sheet_view(ch.get("sheet"), ch.get("inventory"), profile),
        "meter": meter.view(ch.get("usage") or {}),
    }


def _state_message(ch: dict) -> str:
    """Volatile state goes in a mid-conversation system message, after the cached prefix."""
    scene = ch.get("scene") or {}
    sheet = ch.get("sheet") or {}
    items = (ch.get("inventory") or {}).get("items", [])
    notes = ch.get("notes") or {}
    keys = profile_of(ch).state_keys
    brief = {
        "scene": scene,
        "sheet_summary": {k: sheet.get(k) for k in keys if k in sheet} if sheet else "no sheet yet",
        "possessions": [_item_line(it) for it in items] or "none recorded",
        "private_note_keys": sorted(notes.keys()),
    }
    text = "Storyteller screen (current state, not visible to the player): " + json.dumps(brief, ensure_ascii=False)
    if sheet and not items:
        text += (
            "\nThe character's possessions are not recorded yet. At the next natural pause, record what they "
            "carry and own, based on the story so far, with set_inventory."
        )
    if ch.get("recap_due"):
        text += (
            "\n\nThe chronicle's earlier turns have been condensed into the summary at the start of this "
            "conversation. Your private notes, in full, so you can pick the thread back up:\n"
            + (json.dumps(notes, ensure_ascii=False, indent=1) if notes else "(no private notes yet)")
            + "\nReread them before you continue. Trust the summary and these notes over your memory of details."
        )
    return text


COMPACTION_BETA = "compact-2026-09-04"


def _has_summary(messages: list[dict]) -> bool:
    """True when the history starts with a compaction (summary) block."""
    if not messages:
        return False
    c = messages[0].get("content")
    return isinstance(c, list) and bool(c) and isinstance(c[0], dict) and c[0].get("type") == "compaction"


def _is_player_message(m: dict) -> bool:
    """A user message typed by the player (or the app's opening line), not tool results or a summary."""
    c = m.get("content")
    if isinstance(c, str):
        return True
    return bool(c) and all(isinstance(b, dict) and b.get("type") == "text" for b in c)


def _is_thinking_binding_error(e: anthropic.APIStatusError) -> bool:
    if e.status_code != 400:
        return False
    msg = str(e)
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    msg += " " + str(err.get("message", ""))
    return "thinking" in msg and "signature" in msg


class Storyteller:
    def __init__(self, db: DB | None = None, vault: Vault | None = None, client: anthropic.Anthropic | None = None,
                 vaults: dict[str, Vault] | None = None):
        self.db = db or DB()
        if vaults is not None:
            self.vaults = dict(vaults)
        elif vault is not None:
            self.vaults = {"vtm": vault}
        else:
            self.vaults = {g: Vault(p) for g, p in config.discover_vaults().items()}
        self.client = client or anthropic.Anthropic()
        self._system_cache: dict[tuple[str, str], list[dict]] = {}

    @property
    def vault(self) -> Vault:  # the home vault, for callers that only know one
        return self.vaults.get("vtm") or next(iter(self.vaults.values()))

    def vault_for(self, ch: dict) -> Vault | None:
        return self.vaults.get(ch.get("game") or "vtm")

    def system_for(self, profile: GameProfile, campaign: str) -> list[dict]:
        key = (profile.id, campaign)
        if key not in self._system_cache:
            vault = self.vaults[profile.id]
            self._system_cache[key] = build_system(profile, campaign, vault.root)
        return self._system_cache[key]

    def _request_kwargs(self, ch: dict, messages: list[dict]) -> dict:
        profile = profile_of(ch)
        kw: dict = dict(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=self.system_for(profile, ch["campaign"]),
            tools=tool_specs(profile),
            messages=messages,
            output_config={"effort": config.EFFORT},
            # Automatic caching of the conversation tail: the breakpoint moves forward
            # as the chronicle grows, so each request re-reads the previous one from cache.
            cache_control=cache_marker(),
        )
        betas = []
        if _has_summary(messages):
            betas.append(COMPACTION_BETA)  # required on every request that carries the signed block
        if config.ENABLE_FALLBACKS:
            betas.append("server-side-fallback-2026-07-01")
            kw["fallbacks"] = "default"
        if betas:
            kw["betas"] = betas
        return kw

    def _note_usage(self, chronicle_id: str, response_usage) -> None:
        ch = self.db.get_chronicle(chronicle_id)
        self.db.set_usage(chronicle_id, meter.record(ch.get("usage") or {}, response_usage))

    # ---------- memory condensing ----------
    @staticmethod
    def _split_for_compaction(messages: list[dict], keep_turns: int) -> tuple[list[dict], list[dict]]:
        """Split the history into (older turns to summarize, recent turns to keep word for word).

        A kept turn starts at a player message (a user message that is not a tool
        result) and runs to the end; the Storyteller-screen system message that follows
        a player message stays with it. Compaction blocks already at the front stay
        in the summarized part, so the new summary covers them too.
        """
        if keep_turns <= 0:
            return messages, []
        starts = [i for i, m in enumerate(messages) if m["role"] == "user" and _is_player_message(m)]
        if len(starts) <= keep_turns:
            return [], messages  # nothing old enough to summarize
        cut = starts[-keep_turns]
        return messages[:cut], messages[cut:]

    def compact(self, chronicle_id: str, reason: str = "requested", final: bool = True) -> Iterator[Event]:
        """Ask the API to condense the older turns into one summary block.

        The sheet, possessions, scene and private notes live outside the conversation
        and are unaffected. The next turn shows the Storyteller its notes in full.
        """
        ch = self.db.get_chronicle(chronicle_id)
        if not ch:
            yield {"kind": "error", "message": "no such chronicle"}
            return
        yield from self._repair_history(ch)
        messages = self.db.get_messages(chronicle_id)
        if messages and messages[-1]["role"] != "assistant":
            yield self.db.append_event(chronicle_id, "memory", {"text": "Memory not condensed: the last turn did not finish cleanly. Send another message first."})
            if final:
                yield {"kind": "done", **public_state(ch)}
            return
        older, recent = self._split_for_compaction(messages, config.KEEP_RECENT_TURNS)
        if not older or not any(m["role"] == "assistant" for m in older):
            yield self.db.append_event(chronicle_id, "memory", {"text": "Memory not condensed: there is nothing old enough to summarize yet."})
            if final:
                yield {"kind": "done", **public_state(ch)}
            return
        yield {"kind": "working", "name": "compaction"}
        profile = profile_of(ch)
        kw = dict(
            model=config.MODEL,
            max_tokens=16000,
            system=self.system_for(profile, ch["campaign"]),
            tools=tool_specs(profile),
            messages=older,
            compaction={"type": "summarize", "instructions": _load(profile.prompts_dir / "compaction.md")},
            betas=[COMPACTION_BETA],
        )
        try:
            # Streamed like every other request, so the tool definitions are byte-identical
            # to the ones the kept turns' thinking is bound to.
            with self.client.beta.messages.stream(**kw) as stream:
                for _ in stream:
                    pass
                response = stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            msg = f"API error {e.status_code}: {e.message}" if isinstance(e, anthropic.APIStatusError) else f"connection error: {e}"
            yield self.db.append_event(chronicle_id, "memory", {"text": f"Memory not condensed ({msg}). Play continues with the full history."})
            if final:
                yield {"kind": "done", **public_state(ch)}
            return
        usage = self.db.get_chronicle(chronicle_id).get("usage") or {}
        before = usage.get("context", 0)
        block = next((b for b in response.content if b.type == "compaction"), None)
        if block is None or not getattr(block, "content", None):
            # A failed summary is a normal 200 with empty content and a telling stop reason.
            self.db.set_usage(chronicle_id, meter.after_compaction(usage, getattr(response, "usage", None)))
            why = {
                "max_tokens": "the summary ran past its length limit",
                "model_context_window_exceeded": "the history no longer fits the model's context window; reduce ST_KEEP_RECENT_TURNS or ST_COMPACT_AT",
                "tool_use": "the model tried to call a tool instead of writing the summary",
                "refusal": "the model declined to write the summary",
            }.get(getattr(response, "stop_reason", None), f"the API returned no summary (stop reason: {getattr(response, 'stop_reason', None)})")
            yield self.db.append_event(chronicle_id, "memory", {"text": f"Memory not condensed: {why}. Play continues with the full history."})
            yield {"kind": "state", **public_state(self.db.get_chronicle(chronicle_id))}
            if final:
                yield {"kind": "done", **public_state(self.db.get_chronicle(chronicle_id))}
            return
        # The block goes back exactly as returned, first in the history, as an assistant message.
        summary_msg = {"role": "assistant", "content": [block.model_dump(mode="json", exclude_none=True)]}
        self.db.replace_messages(chronicle_id, [summary_msg] + recent)
        self.db.set_recap_due(chronicle_id, True)
        kept_chars = sum(len(json.dumps(m["content"])) for m in recent)
        estimate = int(usage.get("prefix", 0) + len(block.content) / 3.5 + kept_chars / 3.5)
        self.db.set_usage(chronicle_id, meter.after_compaction(usage, getattr(response, "usage", None), new_context=estimate))
        kept = sum(1 for m in recent if m["role"] == "user" and _is_player_message(m))
        yield self.db.append_event(chronicle_id, "memory", {
            "text": f"Memory condensed ({reason}): {len(older)} earlier messages became a summary of about "
                    f"{len(block.content.split()):,} words; the last {kept} turn{'s' if kept != 1 else ''} stay word for word. "
                    f"The Storyteller will reread its notes next turn."
        })
        yield {"kind": "state", **public_state(self.db.get_chronicle(chronicle_id))}
        log.info("chronicle %s: condensed %d messages (%s); context was ~%s", chronicle_id, len(older), reason, before)
        if final:
            yield {"kind": "done", **public_state(self.db.get_chronicle(chronicle_id))}

    def _repair_history(self, ch: dict) -> list[Event]:
        """Bring a stored history into a shape the API accepts (a history written by an older
        release, or one left broken by an interrupted turn). Returns the transcript events
        that tell the player what changed."""
        cid = ch["id"]
        events: list[Event] = []
        n = self.db.convert_legacy_summary(cid)
        if n:
            log.info("chronicle %s: converted %d summary block(s) from the first release", cid, n)
            self.db.set_recap_due(cid, True)
            events.append(self.db.append_event(cid, "memory", {
                "text": "Memory format updated: a summary made by an earlier version of the app was converted so play can continue. "
                        "The Storyteller will reread its notes next turn."
            }))
        for text in self.db.drop_unanswered_turns(cid):
            log.info("chronicle %s: removed a turn that never got an answer", cid)
            events.append(self.db.append_event(cid, "system", {
                "text": "An earlier turn was interrupted before the Storyteller answered, so it was removed from the "
                        f"Storyteller's memory. The message that was never answered: \u201c{text}\u201d. Send it again if you still want to say it."
            }))
        return events

    def _unwind_turn(self, chronicle_id: str, base_seq: int, player_event: Event | None) -> None:
        """Take back what an unanswered turn wrote: its messages, so the history stays valid
        (a system message may never be followed by a user message), and its transcript entry."""
        self.db.truncate_messages(chronicle_id, base_seq)
        if player_event:
            self.db.delete_event(chronicle_id, player_event["seq"])

    def _sync_prefix(self, ch: dict) -> None:
        """Thinking blocks are bound to the exact system prompt and tool list that produced
        them. If either changed since this chronicle's last turn (an app update, or an edit to
        a note in the cached prompt), drop the old blocks once so the API keeps accepting the
        history."""
        profile = profile_of(ch)
        h = prefix_hash(self.system_for(profile, ch["campaign"]), tool_specs(profile))
        if ch.get("prefix_hash") == h:
            return
        if self.db.has_messages(ch["id"]):
            n = self.db.strip_thinking(ch["id"])
            log.info("chronicle %s: Storyteller instructions changed; cleared %d old thinking blocks", ch["id"], n)
        self.db.set_prefix_hash(ch["id"], h)

    def turn(self, chronicle_id: str, player_text: str | None, operator_note: str | None = None,
             compact_after: bool = False) -> Iterator[Event]:
        """Run one player turn. Yields UI events as they happen.

        player_text: what the player typed (None for a Storyteller-initiated turn).
        operator_note: optional extra system instruction for this turn (e.g. session end).
        compact_after: condense the chronicle's memory once the turn is done (session end).
        """
        ch = self.db.get_chronicle(chronicle_id)
        if not ch:
            yield {"kind": "error", "message": "no such chronicle"}
            return
        vault = self.vault_for(ch)
        if vault is None:
            yield {"kind": "error", "message": f"no vault is available for the game '{ch.get('game')}'; see the README on vaults"}
            return
        self._sync_prefix(ch)
        repaired = self._repair_history(ch)
        if repaired:
            yield from repaired
            ch = self.db.get_chronicle(chronicle_id)
        self.db.set_usage(chronicle_id, meter.start_turn(ch.get("usage") or {}))
        pending: list[Event] = []

        def emit(kind: str, payload: dict):
            if kind in LIVE_ONLY:
                pending.append({"kind": "state", **public_state(self.db.get_chronicle(chronicle_id))})
            else:
                pending.append(self.db.append_event(chronicle_id, kind, payload))

        ctx = ToolContext(self.db, vault, chronicle_id, emit, chronicle_name=ch["name"], profile=profile_of(ch))

        base_seq = self.db.last_message_seq(chronicle_id)  # the history before this turn
        player_event = None
        answered = False  # has any response been saved for this turn?
        unwound = False  # have this turn's unanswered messages been taken back already?
        try:
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
            while True:
                rounds += 1
                if rounds > 12:
                    yield {"kind": "error", "message": "too many tool rounds in one turn; stopping"}
                    break
                messages = self.db.get_messages(chronicle_id)
                text_buf: list[str] = []
                try:
                    with self.client.beta.messages.stream(**self._request_kwargs(ch, messages)) as stream:
                        for event in stream:
                            if event.type == "text":
                                text_buf.append(event.text)
                                yield {"kind": "delta", "text": event.text}
                            elif event.type == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
                                yield {"kind": "working", "name": event.content_block.name}
                        response = stream.get_final_message()
                except (ValueError, anthropic.APIStatusError, anthropic.APIConnectionError, httpx2.HTTPError) as e:
                    if isinstance(e, anthropic.APIStatusError) and not healed and _is_thinking_binding_error(e):
                        # Something before an old thinking block changed. Drop the blocks once and retry.
                        healed = True
                        n = self.db.strip_thinking(chronicle_id)
                        log.warning("chronicle %s: API rejected old thinking blocks; cleared %d and retrying", chronicle_id, n)
                        rounds -= 1
                        continue
                    if isinstance(e, anthropic.APIStatusError):
                        msg = f"API error {e.status_code}: {e.message}"
                    elif isinstance(e, (anthropic.APIConnectionError, httpx2.HTTPError)):
                        # Before the first byte arrives the SDK raises its own connection error; once
                        # the stream is open, a dropped connection surfaces as the raw transport error.
                        msg = f"connection error: {e}"
                    else:  # unparseable tool JSON from the stream
                        msg = f"malformed tool call from the model: {e}"
                    err = {"kind": "error", "message": msg}
                    if not answered:
                        # Nothing was answered, so unwind what this turn added and keep the history
                        # valid (a system message may never be followed by a user message).
                        self._unwind_turn(chronicle_id, base_seq, player_event)
                        unwound = True
                        if player_event:
                            err["restore_text"] = player_text
                            err["unsent_seq"] = player_event["seq"]
                    yield err
                    break

                answered = True
                self._note_usage(chronicle_id, getattr(response, "usage", None))
                if ch.get("recap_due"):
                    self.db.set_recap_due(chronicle_id, False)  # the recap has been shown
                    ch["recap_due"] = 0
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
        finally:
            if not answered and not unwound:
                # The turn ended some other way: the browser went away mid-stream (this generator
                # was closed) or an error the loop does not handle. Leave the history valid, and
                # tell the player on their return that the message never got through.
                self._unwind_turn(chronicle_id, base_seq, player_event)
                if player_event:
                    self.db.append_event(chronicle_id, "system", {
                        "text": "The turn was interrupted before the Storyteller answered. This message was not delivered: "
                                f"\u201c{player_text}\u201d. Send it again to continue."
                    })

        if answered and config.ENABLE_COMPACTION:
            usage = self.db.get_chronicle(chronicle_id).get("usage") or {}
            if compact_after:
                yield from self.compact(chronicle_id, reason="session end", final=False)
            elif usage.get("context", 0) >= config.COMPACT_AT:
                yield from self.compact(chronicle_id, reason=f"context reached {usage['context']:,} tokens", final=False)
        yield {"kind": "done", **public_state(self.db.get_chronicle(chronicle_id))}
