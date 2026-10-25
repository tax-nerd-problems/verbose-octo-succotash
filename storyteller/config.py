"""Configuration for the AI Storyteller.

Everything here can be overridden with environment variables so you never
have to edit this file to try a different model or campaign.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent  # this package
APP_ROOT = APP_DIR.parent  # the app's own folder (the repository root)
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

# Vaults. A vault is an Obsidian folder holding an "AI Storyteller.md" note whose
# frontmatter names the game (game: vtm, game: mage). The app looks for vaults in the
# folders beside its own folder, so the usual layout is one parent folder holding the
# app folder and a folder per vault. ST_VAULTS lists vault folders anywhere else (paths
# separated by the OS path separator; "game=path" pins the game). One vault per game:
# the first found wins, in the order ST_VAULTS, VAULT_DIR, the folders beside the app.
VAULTS = os.environ.get("ST_VAULTS", "") or os.environ.get("ST_EXTRA_VAULTS", "")
# The first release's single-vault setting. A folder it names counts as Vampire even
# without a marker, because that release's marker named no game.
VAULT_DIR = os.environ.get("VAULT_DIR", "").strip()
MARKER = "AI Storyteller.md"


def _unquote(s: str) -> str:
    """Drop one pair of surrounding quotes: Windows keeps them from `set X="C:\\My Vault"`."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1].strip()
    return s


def vault_game(path: Path) -> str | None:
    """The game id a vault's marker note declares. None when there is no marker; "" when the
    marker exists but names no game or could not be read."""
    marker = path / MARKER
    if not marker.is_file():
        return None
    try:
        import frontmatter
        raw = marker.read_bytes().replace(b"\x00", b"").decode("utf-8-sig", errors="replace")  # -sig drops a BOM
        g = frontmatter.loads(raw).metadata.get("game")
        if isinstance(g, list) and g:  # an Obsidian List property
            g = g[0]
        return str(g).strip().lower() if g else ""
    except Exception as e:
        import logging
        logging.getLogger("storyteller").warning("cannot read %s: %s", marker, e)
        return ""


def vaults_beside_app() -> list[Path]:
    """The folders next to the app's own folder, in name order. Hidden folders are skipped."""
    try:
        return sorted(p for p in APP_ROOT.parent.iterdir() if p.is_dir() and p != APP_ROOT and not p.name.startswith("."))
    except OSError:
        return []


def discover_vaults() -> dict[str, Path]:
    """game id -> vault folder, for every game whose vault could be found."""
    import logging

    from . import games
    known = set(games.all_games())
    log = logging.getLogger("storyteller")
    found: dict[str, Path] = {}

    explicit = ("ST_VAULTS entry", "VAULT_DIR")

    def add(path: Path, game: str | None, where: str) -> None:
        if not path.is_dir():
            if where in explicit:
                log.warning("%s %s is not a folder; ignored", where, path)
            return
        g = (game or "").strip().lower() or vault_game(path)
        if g == "" and not game:  # a marker that names no game, or could not be read
            if where == "VAULT_DIR" or where == "the app's own folder":
                g = "vtm"  # the first release's marker named no game and meant Vampire
            else:
                log.warning("ignoring %s %s: its %s names no game", where, path, MARKER)
                return
        if g is None:  # no marker at all
            if where != "VAULT_DIR":
                return
            g = "vtm"
        if g not in known:
            log.warning("ignoring %s %s: its marker names an unknown game %r", where, path, g)
            return
        if g in found:
            log.info("%s %s also claims %s; using %s", where, path, g, found[g])
            return
        found[g] = path.resolve()

    for entry in VAULTS.split(os.pathsep):
        entry = _unquote(entry)
        if not entry:
            continue
        head, sep, tail = entry.partition("=")
        head = head.strip().lower()
        if sep and head in known:  # "game=path"; a bare path may itself contain "="
            add(Path(_unquote(tail)).expanduser(), head, "ST_VAULTS entry")
        elif sep and head.isidentifier() and Path(_unquote(tail)).expanduser().is_dir():
            log.warning("ignoring ST_VAULTS entry %s: unknown game %r (known: %s)", entry, head, ", ".join(sorted(known)))
        else:
            add(Path(entry).expanduser(), None, "ST_VAULTS entry")
    if VAULT_DIR:
        add(Path(_unquote(VAULT_DIR)).expanduser(), None, "VAULT_DIR")
    # The first release kept the app's package inside the Vampire vault, so the app's own
    # folder may itself be a vault; the usual layout has the vaults next to it.
    if (APP_ROOT / MARKER).is_file():
        add(APP_ROOT, None, "the app's own folder")
    for path in vaults_beside_app():
        add(path, None, "folder beside the app")
    if not found:
        settings = " or ".join(n for n, v in (("ST_VAULTS", VAULTS), ("VAULT_DIR", VAULT_DIR)) if v)
        log.warning("no vault found beside %s%s; see the README on vaults", APP_ROOT, f" or in {settings}" if settings else "")
    return found


# The game the New chronicle dialog starts on. Empty means the first vault found.
DEFAULT_GAME = os.environ.get("ST_GAME", "").strip().lower()
