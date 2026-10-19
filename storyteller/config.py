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

# Server-side features. Flip to "0" if your account or platform rejects a beta.
ENABLE_COMPACTION = os.environ.get("ST_COMPACTION", "1") == "1"
ENABLE_FALLBACKS = os.environ.get("ST_FALLBACKS", "1") == "1"

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
