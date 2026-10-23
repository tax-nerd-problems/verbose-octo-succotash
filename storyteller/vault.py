"""Read and write access to the Obsidian vault.

The vault is the Storyteller's library and long-term memory. Reads are open
across every markdown note. Writes are deliberately narrow:

- new notes only inside the session-log folder,
- appends to an existing note go under a clearly marked heading,
- frontmatter edits are limited to a small allow-list of keys,
- nothing is ever deleted or renamed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

from . import config

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
WORD = re.compile(r"[a-z0-9']+")
AI_SECTION = "## Chronicle Notes (AI Storyteller)"
ALLOWED_FRONTMATTER_KEYS = {"char state", "char state chronicle", "location", "status", "last seen"}
# A chronicle-notes bullet: "- **2026-10-20 · Anja in New York** — text" (the chronicle part is optional on old bullets)
BULLET = re.compile(r"^- \*\*\d{4}-\d{2}-\d{2}(?: · (?P<who>[^*]+?))?\*\* — ")


def slug(name: str) -> str:
    """A chronicle name as a safe folder name."""
    return re.sub(r"[^\w\s\-&',.!]", "", str(name)).strip() or "Chronicle"


def _read_text(path: Path) -> str:
    # Some notes carry trailing NUL bytes from a sync tool; strip them.
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


@dataclass
class Note:
    path: Path
    rel: str
    title: str
    aliases: list[str]
    tags: list[str]
    meta: dict
    body: str
    folder: str
    _words: set[str] = field(default_factory=set, repr=False)

    @property
    def summary_line(self) -> str:
        for line in self.body.splitlines():
            line = line.strip()
            if line.startswith(">"):
                return line.lstrip("> ").strip()
        for line in self.body.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("|"):
                return line[:160]
        return ""

    @property
    def is_sourcebook(self) -> bool:
        f = self.folder
        return "(revised)" in f.lower() or "(v20)" in f.lower() or "handbook" in f.lower() or "guide to" in f.lower()

    @property
    def is_index(self) -> bool:
        return str(self.meta.get("type", "")).lower() == "index" or any(t.lower() == "moc" for t in self.tags)

    def section(self, heading: str) -> str | None:
        """Return the text under a heading (any level), up to the next heading of same-or-higher level."""
        matches = list(HEADING.finditer(self.body))
        for i, m in enumerate(matches):
            if m.group(2).strip().lower() == heading.strip().lower():
                level = len(m.group(1))
                end = len(self.body)
                for n in matches[i + 1 :]:
                    if len(n.group(1)) <= level:
                        end = n.start()
                        break
                return self.body[m.start() : end].strip()
        return None

    def headings(self) -> list[str]:
        return [m.group(2).strip() for m in HEADING.finditer(self.body)]


class Vault:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.VAULT_DIR).resolve()
        self.notes: dict[str, Note] = {}  # key: lowercase title (first note wins when titles repeat)
        self.by_rel: dict[str, Note] = {}  # every note, by path
        self.dupes: dict[str, list[Note]] = {}  # titles shared by several notes
        self._alias_index: dict[str, Note] = {}
        self.reload()

    # ---------- indexing ----------
    def reload(self) -> None:
        notes: dict[str, Note] = {}
        by_rel: dict[str, Note] = {}
        dupes: dict[str, list[Note]] = {}
        alias_index: dict[str, Note] = {}
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(".") or "/." in rel or rel.startswith("storyteller/"):
                continue
            try:
                post = frontmatter.loads(_read_text(path))
            except Exception:
                post = frontmatter.Post(_read_text(path))
            meta = dict(post.metadata or {})
            body = post.content
            title = path.stem
            m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            h1 = m.group(1).strip() if m else ""
            aliases = meta.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            tags = meta.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            note = Note(
                path=path,
                rel=rel,
                title=title,
                aliases=[str(a) for a in aliases] + ([h1] if h1 and h1 != title else []),
                tags=[str(t) for t in tags],
                meta=meta,
                body=body,
                folder=rel.rsplit("/", 1)[0] if "/" in rel else "",
            )
            note._words = set(WORD.findall(body.lower()))
            key = title.lower()
            if key in notes:
                dupes.setdefault(key, [notes[key]]).append(note)
            else:
                notes[key] = note
            by_rel[rel] = note
            for a in note.aliases:
                alias_index.setdefault(a.lower(), note)
        # Swap the finished indexes in at once; readers on other threads see old or new, never a mix.
        self.notes, self.by_rel, self.dupes, self._alias_index = notes, by_rel, dupes, alias_index

    # ---------- reading ----------
    def resolve(self, name: str, chronicle: str | None = None) -> Note | None:
        """Resolve a note by title, alias, [[wikilink]], or relative path.

        When several notes share a title (two chronicles' session logs, say), the one
        belonging to `chronicle` wins.
        """
        if not name:
            return None
        key = name.strip()
        m = WIKILINK.fullmatch(key)
        if m:
            key = m.group(1)
        key = key.strip().strip("[]")
        if key.endswith(".md"):
            n = self.by_rel.get(key)
            if n:
                return n
            key = key[:-3]
        low = key.lower()
        if low in self.dupes and chronicle:
            for n in self.dupes[low]:
                if n.meta.get("chronicle") and str(n.meta["chronicle"]) == chronicle:
                    return n
        if low in self.notes:
            return self.notes[low]
        if low in self._alias_index:
            return self._alias_index[low]
        # last path component
        tail = low.rsplit("/", 1)[-1]
        if tail in self.notes:
            return self.notes[tail]
        # fuzzy: unique note whose title contains the key
        cands = [n for t, n in self.notes.items() if low in t]
        if len(cands) == 1:
            return cands[0]
        return None

    @staticmethod
    def belongs(note: Note, chronicle: str | None) -> bool:
        """Notes written for one chronicle (session logs) are invisible to the others."""
        owner = note.meta.get("chronicle")
        return not owner or not chronicle or str(owner) == chronicle

    def list_by_tag(self, tag: str, chronicle: str | None = None) -> list[Note]:
        tag = tag.lstrip("#").lower()
        return [n for n in self.by_rel.values()
                if self.belongs(n, chronicle) and any(t.lower() == tag or t.lower().startswith(tag + "/") for t in n.tags)]

    def list_folder(self, folder: str, chronicle: str | None = None) -> list[Note]:
        folder = folder.strip("/").lower()
        return [n for n in self.by_rel.values()
                if self.belongs(n, chronicle) and (n.folder.lower() == folder or n.folder.lower().startswith(folder + "/"))]

    def search(self, query: str, limit: int = 8, folder: str | None = None, chronicle: str | None = None) -> list[tuple[Note, int, str]]:
        """Rank notes by simple term scoring. Returns (note, score, snippet)."""
        terms = [t for t in WORD.findall(query.lower()) if len(t) > 1]
        if not terms:
            return []
        results = []
        for note in self.by_rel.values():
            if folder and not note.folder.lower().startswith(folder.strip("/").lower()):
                continue
            if not self.belongs(note, chronicle):
                continue
            score = 0
            title_l = note.title.lower()
            alias_l = " ".join(note.aliases).lower()
            tags_l = " ".join(note.tags).lower()
            body_l = note.body.lower()
            matched = 0
            for t in terms:
                hit = False
                if t in title_l:
                    score += 12
                    hit = True
                if t in alias_l:
                    score += 8
                    hit = True
                if t in tags_l:
                    score += 5
                    hit = True
                if t in note._words:
                    score += 2 + min(body_l.count(t), 5)
                    hit = True
                matched += hit
            if matched == len(terms) and len(terms) > 1:
                score += 10
            if len(terms) > 1 and " ".join(terms) in body_l:
                score += 15
            if note.is_sourcebook or note.is_index:
                score //= 2  # campaign material outranks sourcebooks and index pages
            if score:
                results.append((note, score, self._snippet(note, terms)))
        results.sort(key=lambda r: -r[1])
        return results[:limit]

    @staticmethod
    def _snippet(note: Note, terms: list[str]) -> str:
        body = note.body
        low = body.lower()
        for t in terms:
            i = low.find(t)
            if i >= 0:
                start = max(0, i - 90)
                end = min(len(body), i + 150)
                return re.sub(r"\s+", " ", body[start:end]).strip()
        return note.summary_line

    def body_for(self, note: Note, chronicle: str | None) -> str:
        """The note's body with chronicle notes from *other* chronicles removed."""
        if not chronicle or AI_SECTION not in note.body:
            return note.body
        out = []
        for line in note.body.splitlines():
            m = BULLET.match(line)
            if m and m.group("who") and m.group("who") != chronicle:
                continue
            out.append(line)
        return "\n".join(out)

    def meta_for(self, note: Note, chronicle: str | None) -> dict:
        """Frontmatter as this chronicle should read it: a state set by another chronicle is flagged."""
        meta = {k: v for k, v in note.meta.items() if k not in ("tags", "aliases")}
        owner = meta.pop("char state chronicle", None)
        if owner and chronicle and str(owner) != chronicle and "char state" in meta:
            meta["char state"] = (f"{meta['char state']} in the chronicle '{owner}', not this one; "
                                  "in this chronicle assume the original state unless your own notes say otherwise")
        return meta

    # ---------- writing ----------
    def _safe_path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root) or p.suffix != ".md":
            raise ValueError(f"refusing to touch {rel!r}: outside the vault or not a markdown note")
        return p

    def write_session_log(self, title: str, content: str, tags: list[str] | None = None, chronicle: str = "") -> str:
        """Create (or overwrite the same-day) session log in the chronicle's own folder."""
        safe_title = re.sub(r"[^\w\s\-&',.!]", "", title).strip() or "Session"
        folder = config.SESSION_LOG_DIR + (f"/{slug(chronicle)}" if chronicle else "")
        rel = f"{folder}/{date.today().isoformat()} - {safe_title}.md"
        path = self._safe_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(
            content.strip() + "\n",
            tags=["VTM", "VTM/session", "VTM/ai-storyteller"] + list(tags or []),
            type="session log",
            date=date.today().isoformat(),
            chronicle=chronicle,
        )
        path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
        self.reload()
        return rel

    def append_to_note(self, name: str, text: str, chronicle: str = "") -> str:
        note = self.resolve(name, chronicle or None)
        if not note:
            raise ValueError(f"no note named {name!r}")
        raw = _read_text(note.path).rstrip("\n")
        stamp = date.today().isoformat() + (f" · {chronicle}" if chronicle else "")
        entry = f"\n- **{stamp}** — {text.strip()}\n"
        if AI_SECTION in raw:
            raw = raw + entry
        else:
            raw = raw + f"\n\n{AI_SECTION}\n{entry}"
        note.path.write_text(raw + ("\n" if not raw.endswith("\n") else ""), encoding="utf-8")
        self.reload()
        return note.rel

    def set_frontmatter(self, name: str, key: str, value: str, chronicle: str = "") -> str:
        key = key.strip().lower()
        if key not in ALLOWED_FRONTMATTER_KEYS:
            raise ValueError(f"frontmatter key {key!r} is not editable; allowed: {sorted(ALLOWED_FRONTMATTER_KEYS)}")
        note = self.resolve(name, chronicle or None)
        if not note:
            raise ValueError(f"no note named {name!r}")
        post = frontmatter.loads(_read_text(note.path))
        post.metadata[key] = value
        note.path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
        self.reload()
        return note.rel

    def set_character_state(self, name: str, state: str, chronicle: str = "") -> str:
        """Record an NPC's state for Obsidian (frontmatter) and for this chronicle (a tagged bullet)."""
        rel = self.set_frontmatter(name, "char state", state, chronicle)
        if chronicle:
            self.set_frontmatter(name, "char state chronicle", chronicle, chronicle)
        self.append_to_note(name, f"char state → {state}", chronicle)
        return rel
