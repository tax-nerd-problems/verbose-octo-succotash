"""Game profiles: everything that differs between the Storyteller System games the app can run.

A profile bundles the prompts, the campaign list, the sheet layout, the per-turn
screen keys, the trait caps, the tool descriptions and any game-specific tools.
The turn loop, the vault, the UI and the memory handling are shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Pool:
    """A spendable pool shown as filled boxes (blood, Willpower)."""
    key: str
    label: str
    max_key: str | None = None
    style: str = "will"
    aliases: tuple[str, ...] = ()  # other keys older sheets used for the current value
    max_aliases: tuple[str, ...] = ()
    max_default: Callable[[dict, Callable], int | None] | None = None  # (sheet, take) -> max from other values
    note: Callable[[dict, Callable], str | None] | None = None  # (sheet, take) -> a legend line


@dataclass(frozen=True)
class Group:
    """A block of dotted traits (Disciplines, Spheres, Backgrounds...)."""
    title: str
    key: str
    aliases: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()  # always show these names, zero-rated if absent (Spheres)
    order: tuple[str, ...] = ()  # canonical names and their display order; absent ones are not shown (Virtues)
    max_dots: int | None = 5  # None: the sheet's own maximum (generation); 0: a level, not dots (Rituals)
    note_key: str | None = None  # a sheet key naming one trait to mark (e.g. the affinity Sphere)


@dataclass(frozen=True)
class SheetLayout:
    identity: tuple[tuple[str, str, tuple[str, ...]], ...]  # (key, label, aliases)
    abilities: tuple[tuple[str, tuple[str, ...]], ...]
    groups: tuple[Group, ...]
    pools: tuple[Pool, ...]
    subtitle: tuple[str, ...]
    track_label: str | None = None  # the 1-10 dotted track shown first in Status (Humanity, Arete)
    track_keys: tuple[str, ...] = ()
    wheel: tuple[str, str, int] | None = None  # (left key, right key, size): Quintessence/Paradox
    texts: tuple[tuple[str, str, tuple[str, ...]], ...] = ()  # (key, label, aliases) shown as prose
    max_dots: Callable[[dict, Callable], int] | None = None  # (sheet, take) -> dots per trait
    panel: tuple[str, ...] = ()  # which trackers the side panel shows, by label; "{track}" is the morality/Arete track


@dataclass(frozen=True)
class GameProfile:
    id: str
    label: str
    short: str  # e.g. "V20", used in tool text
    prompts_dir: Path
    campaigns: dict[str, dict]
    default_campaign: str
    sheet: SheetLayout
    sheet_doc: str  # the layout example in the set_sheet tool description
    trait_doc: str  # the example paths in the update_trait description
    cap_doc: str  # the sentence about limits in the update_trait description (starts with ", " or ". ")
    dice_doc: str  # the whole roll_dice description
    list_doc: str  # tag examples for list_notes
    state_keys: tuple[str, ...]
    caps: dict[str, Any] = field(default_factory=dict)  # key -> max key, or ("wheel", size, other key)
    extra_tools: tuple[dict, ...] = ()
    extra_impls: dict[str, Callable] = field(default_factory=dict)
    morality: Callable[[dict, Callable], dict | None] | None = None  # (sheet, take) -> {"label", "rating"}
    reconcile: Callable[[dict], list[str]] | None = None  # fix up linked values after a change; returns what changed


_REGISTRY: dict[str, GameProfile] = {}


def register(profile: GameProfile) -> GameProfile:
    _REGISTRY[profile.id] = profile
    return profile


def get(game_id: str) -> GameProfile:
    _load()
    try:
        return _REGISTRY[game_id]
    except KeyError:
        raise KeyError(f"unknown game {game_id!r}; known: {sorted(_REGISTRY)}") from None


def all_games() -> dict[str, GameProfile]:
    _load()
    return dict(_REGISTRY)


def _load() -> None:
    if not _REGISTRY:
        from . import mage, vtm  # registration happens on import

        assert mage.PROFILE and vtm.PROFILE
