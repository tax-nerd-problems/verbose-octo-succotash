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

# Vaults. The vault this folder sits in is the home vault. Other vaults are found
# next to it (sibling folders holding an "AI Storyteller.md" note whose frontmatter
# names the game) or listed in ST_EXTRA_VAULTS (paths separated by the OS path
# separator; "game=path" pins the game). One vault per game.
EXTRA_VAULTS = os.environ.get("ST_EXTRA_VAULTS", "")
MARKER = "AI Storyteller.md"


def vault_game(path: Path) -> str | None:
    """The game id a vault declares in its marker note, or None if it has no marker."""
    marker = path / MARKER
    if not marker.is_file():
        return None
    try:
        import frontmatter
        meta = frontmatter.loads(marker.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")).metadata
        g = meta.get("game")
        return str(g).strip().lower() if g else None
    except Exception:
        return None


def discover_vaults() -> dict[str, Path]:
    """game id -> vault path. The home vault counts as VTM unless its marker says otherwise."""
    from . import games
    known = set(games.all_games())
    found: dict[str, Path] = {}
    home = VAULT_DIR
    home_game = vault_game(home)
    if home_game and home_game not in known:
        import logging
        logging.getLogger("storyteller").warning("the home vault's marker names an unknown game %r; treating it as vtm", home_game)
        home_game = None
    found[home_game or "vtm"] = home

    def add(path: Path, game: str | None, where: str) -> None:
        g = (game or "").strip().lower() or vault_game(path)
        if not path.is_dir() or not g:
            return
        if g not in known:
            import logging
            logging.getLogger("storyteller").warning("ignoring %s %s: its marker names an unknown game %r", where, path, g)
            return
        found.setdefault(g, path.resolve())

    for entry in EXTRA_VAULTS.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        head, sep, tail = entry.partition("=")
        if sep and head.strip().lower() in known:  # "game=path"; a bare path may itself contain "="
            add(Path(tail).expanduser(), head, "ST_EXTRA_VAULTS entry")
        else:
            add(Path(entry).expanduser(), None, "ST_EXTRA_VAULTS entry")
    try:
        siblings = sorted(p for p in home.parent.iterdir() if p.is_dir() and p != home)
    except OSError:
        siblings = []
    for path in siblings:
        add(path, None, "sibling vault")
    return found


DEFAULT_GAME = os.environ.get("ST_GAME", "") or (vault_game(VAULT_DIR) or "vtm")
