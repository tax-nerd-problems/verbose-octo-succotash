"""Token accounting for the context meter and the running cost estimate.

Everything here is derived from the `usage` object on API responses. The API
reports each sampling step of a request under `usage.iterations` (a normal
message, a memory-condensing step, or a fallback model's attempt); when that
list is present the totals are summed across it, since the top-level numbers
leave the condensing step out.
"""
from __future__ import annotations

from typing import Any

from . import config

EMPTY_TOTAL = {"input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 0, "requests": 0, "compactions": 0}


def _get(obj: Any, name: str, default=0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(name, default)
    else:
        v = getattr(obj, name, default)
    return default if v is None else v


def _parts(u: Any) -> dict:
    """One usage record (top-level or one iteration) as plain numbers."""
    cc = _get(u, "cache_creation", None)
    w5 = _get(cc, "ephemeral_5m_input_tokens") if cc is not None else 0
    w1 = _get(cc, "ephemeral_1h_input_tokens") if cc is not None else 0
    total_write = _get(u, "cache_creation_input_tokens")
    if not w5 and not w1 and total_write:
        w5 = total_write  # no breakdown reported; count it at the 5-minute rate
    return {
        "input": int(_get(u, "input_tokens")),
        "cache_read": int(_get(u, "cache_read_input_tokens")),
        "cache_write_5m": int(w5),
        "cache_write_1h": int(w1),
        "output": int(_get(u, "output_tokens")),
    }


def context_size(p: dict) -> int:
    """Tokens the model actually had in front of it: fresh + cached + newly cached."""
    return p["input"] + p["cache_read"] + p["cache_write_5m"] + p["cache_write_1h"]


def cost(parts: dict, model: str | None = None) -> float | None:
    price = config.PRICES.get(model or config.MODEL)
    if not price:
        return None
    inp, out, read, w5, w1 = price
    return (parts["input"] * inp + parts["output"] * out + parts["cache_read"] * read
            + parts["cache_write_5m"] * w5 + parts["cache_write_1h"] * w1) / 1e6


def record(usage_state: dict, response_usage: Any, model: str | None = None) -> dict:
    """Fold one response's usage into the chronicle's running totals. Returns the new state."""
    state = {
        "context": int(usage_state.get("context", 0)),
        "turn": dict(usage_state.get("turn") or {}),
        "total": {**EMPTY_TOTAL, **(usage_state.get("total") or {})},
    }
    iterations = _get(response_usage, "iterations", None) or []
    steps = [(_get(it, "type", "message"), _parts(it)) for it in iterations] or [("message", _parts(response_usage))]
    for kind, p in steps:
        for k, v in p.items():
            state["total"][k] += v
        if kind == "compaction":
            state["total"]["compactions"] += 1
        for k, v in p.items():
            state["turn"][k] = state["turn"].get(k, 0) + v
    state["total"]["requests"] += 1
    state["turn"]["requests"] = state["turn"].get("requests", 0) + 1
    # The last normal sampling step tells us how big the context is now.
    msgs = [p for kind, p in steps if kind != "compaction"]
    if msgs:
        state["context"] = context_size(msgs[-1])
        state["estimated"] = False
        if "prefix" not in usage_state:
            # The first request of a chronicle is almost all fixed prefix (rules, setting, tools).
            state["prefix"] = state["context"]
    elif "prefix" in usage_state:
        state["prefix"] = usage_state["prefix"]
    return state


def start_turn(usage_state: dict) -> dict:
    state = dict(usage_state)
    state["turn"] = {}
    return state


def after_compaction(usage_state: dict, compaction_usage: Any, new_context: int | None = None) -> dict:
    state = record(usage_state, compaction_usage)
    if new_context is not None:
        state["context"] = new_context
        state["estimated"] = True  # until the next turn reports the real size
    return state


def view(usage_state: dict, model: str | None = None) -> dict:
    """What the side panel shows."""
    total = {**EMPTY_TOTAL, **(usage_state.get("total") or {})}
    turn = usage_state.get("turn") or {}
    turn_in = turn.get("input", 0) + turn.get("cache_read", 0) + turn.get("cache_write_5m", 0) + turn.get("cache_write_1h", 0)
    cached = (turn.get("cache_read", 0) / turn_in) if turn_in else None
    return {
        "context": int(usage_state.get("context", 0)),
        "estimated": bool(usage_state.get("estimated", False)),
        "limit": config.COMPACT_AT if config.ENABLE_COMPACTION else None,
        "hard_limit": 1_000_000,
        "turn": {"input": turn_in, "output": turn.get("output", 0), "cached": cached, "requests": turn.get("requests", 0)},
        "total": total,
        "cost": cost(total, model),
        "model": model or config.MODEL,
    }
