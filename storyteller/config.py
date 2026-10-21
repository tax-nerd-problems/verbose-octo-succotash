"""Configuration for the AI Storyteller.

Everything here can be overridden with environment variables so you never
have to edit this file to try a different model or campaign.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
# The Obsidian vault is the parent of this folder.
VAULT_DIR = Path(os.environ.get("VAULT_DIR", APP_DIR.parent)).resolve()
DB_PATH = Path(os.environ.get("ST_DB_PATH", APP_DIR / "chronicles.db"))

MODEL = os.environ.get("ST_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("ST_EFFORT", "medium")  # low | medium | high | xhigh | max
MAX_TOKENS = int(os.environ.get("ST_MAX_TOKENS", "8000"))

# Prompt caching. The rules/setting prefix and the growing conversation are both
# cached; "1h" keeps the cache alive across ordinary breaks in play, "5m" is the
# API default and is cheaper only when turns come less than five minutes apart.
CACHE_TTL = os.environ.get("ST_CACHE_TTL", "1h")  # "1h" | "5m"

# Memory condensing (server-side compaction, on request). The app asks the API to
# summarize the conversation into one block when the measured context reaches
# COMPACT_AT tokens, and at the end of each session. Flip ST_COMPACTION to "0" to
# never condense (the context then grows until the model's 1M-token limit).
ENABLE_COMPACTION = os.environ.get("ST_COMPACTION", "1") == "1"
COMPACT_AT = int(os.environ.get("ST_COMPACT_AT", "150000"))
COMPACT_AT_SESSION_END = os.environ.get("ST_COMPACT_AT_SESSION_END", "1") == "1"
# How many of the latest player turns stay word for word after a condensing.
KEEP_RECENT_TURNS = int(os.environ.get("ST_KEEP_RECENT_TURNS", "2"))

# Server-side refusal fallbacks. Flip to "0" if your account or platform rejects the beta.
ENABLE_FALLBACKS = os.environ.get("ST_FALLBACKS", "1") == "1"

# Prices in US dollars per million tokens, for the cost estimate in the side panel.
# (input, output, cache read, cache write 5m, cache write 1h). Unknown models show tokens only.
PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00, 8.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50, 4.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50, 20.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25, 10.00),
}

# Where the Storyteller may create new notes inside the vault.
SESSION_LOG_DIR = os.environ.get("ST_SESSION_DIR", "Sessions")

# Files loaded into the cached system prompt, per campaign. Paths are relative
# to the vault root and may be globs. Everything else in the vault is reachable
# through the search_vault / read_note tools on demand.
CAMPAIGNS: dict[str, dict] = {
    "campaign1": {
        "label": "Campaign 1 — New York, 2001",
        "premise": APP_DIR / "prompts" / "campaign1_premise.md",
        "core": [
            "Storyteller Reference/*.md",
            "Campaign 1 - Storyteller Overview.md",
            "Campaign 1 Blurb.md",
            "Setting/Campaign 1 Locations.md",
            "Setting/NYC Current State.md",
            "Setting/NYC Geography and Domains.md",
            "Setting/NYC Kindred History.md",
            "Setting/Telling the Tale.md",
        ],
    },
}
DEFAULT_CAMPAIGN = os.environ.get("ST_CAMPAIGN", "campaign1")
