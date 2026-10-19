# AI Storyteller

A local web app that runs this vault's Vampire: The Masquerade chronicle with Claude as the Storyteller. The vault is the Storyteller's library and memory: rules digest, setting, campaign overview, NPC sheets, and sourcebooks are read from the notes around this folder, and session logs are written back into the vault.

## Setup

```bash
cd <vault root>
python3 -m venv storyteller/.venv
source storyteller/.venv/bin/activate      # Windows: storyteller\.venv\Scripts\activate
pip install -r storyteller/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # Windows: set ANTHROPIC_API_KEY=...
uvicorn storyteller.server:app --port 8000
```

Open http://localhost:8000, click **New chronicle**, and the Storyteller opens with character creation.

## How it works

- **Cached core.** `prompts/system.md`, the Storyteller Reference folder, the campaign overview and blurb, and the Setting folder are sent as the system prompt on every turn. The prompt is stable, so after the first turn it is read from Anthropic's prompt cache at a fraction of the price.
- **On-demand library.** Everything else in the vault is reachable through tools: `search_vault`, `read_note`, `list_notes`. The Storyteller reads an NPC's note before that NPC speaks.
- **Honest mechanics.** Dice are rolled by `roll_dice` (V20 rules, 1s cancel, botches, specialties, Willpower), never by the model. The character sheet changes only through `set_sheet` and `update_trait`. Hidden rolls show the player only "rolled behind the screen".
- **Memory.** Chat history, the sheet, the scene, and the Storyteller's private notes live in `storyteller/chronicles.db` (SQLite, gitignored). Private notes hold NPC agendas, clocks, secrets, and a recap.
- **Write-back.** "End session" makes the Storyteller write a log to `Sessions/` in the vault, append dated chronicle notes to NPC notes under a `## Chronicle Notes (AI Storyteller)` heading, and update `char state` in frontmatter when someone dies or disappears. It never deletes or rewrites your original text.
- **Long campaigns.** Server-side compaction (beta) summarizes old history automatically when the context grows large.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `ST_MODEL` | `claude-opus-5-5` | Model id |
| `ST_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| `ST_MAX_TOKENS` | `8000` | Max output tokens per response |
| `ST_CAMPAIGN` | `campaign1` | Default campaign (see `config.py`) |
| `ST_COMPACTION` | `1` | Set `0` to disable server-side compaction |
| `ST_FALLBACKS` | `1` | Set `0` to disable server-side refusal fallbacks |
| `ST_SESSION_DIR` | `Sessions` | Vault folder for session logs |
| `VAULT_DIR` | parent folder | Vault root |
| `ST_DB_PATH` | `storyteller/chronicles.db` | SQLite file |

To add a campaign, add an entry to `CAMPAIGNS` in `config.py` with the core files to cache and a premise file in `prompts/`.

## Tests

Offline tests (no API key needed) cover the dice, the vault indexer and its write guards, the system prompt build, and the turn loop against a scripted fake client:

```bash
python -m unittest storyteller.tests.test_offline -v
```

## Obsidian

Obsidian ignores the non-markdown files here. To keep this README out of search and graph view, add `storyteller` under Settings → Files and links → Excluded files.
