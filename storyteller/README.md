# AI Storyteller

A local web app that runs a Storyteller-System chronicle with Claude as the Storyteller. It ships with two games: **Vampire: The Masquerade (V20)**, played from this vault, and **Mage: The Ascension (M20)**, played from a second vault that sits next to this one. A vault is the Storyteller's library and memory: rules digest, setting, campaign notes, NPC sheets, and sourcebooks are read from its notes, and session logs are written back into it.

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

## Playing

- **Your sheet.** The **Sheet** button (top right) opens a full V20 character sheet: attributes, abilities, disciplines, backgrounds, virtues, merits and flaws, Humanity, Willpower, blood pool, health track, and experience. It updates live as the Storyteller changes things.
- **What you're carrying.** The side panel lists what's on you right now, and the sheet's **Possessions** tab shows everything: in hand, worn, pockets, bag, and what's stashed at your haven, in a car, or elsewhere. The Storyteller records every change as the story makes it. Click any gold line in the transcript to jump to the right tab.
- **Reading at your own pace.** When you send a move, your message moves to the top of the screen and the reply appears beneath it. The page never scrolls by itself while the Storyteller writes. If the reply runs past the bottom, a **New text below** button appears; scroll whenever you like.
- **Memory meter.** Click **Memory** at the bottom of the side panel to see how much of the chronicle the model is holding in its head right now (its context), when it will next be condensed, what the last turn cost in tokens and how much of it came from cache, and a running cost estimate for the chronicle. It stays folded away unless you open it. The estimate uses the list prices in `config.py`; the Usage page in the Anthropic Console is the real bill.

## Games and vaults

Each game has its own vault, and the app finds vaults by a marker: an `AI Storyteller.md` note at the vault's root whose frontmatter says `game: vtm` or `game: mage`. This vault is the home vault. Any folder **next to it** (same parent folder) that carries the marker is picked up automatically; folders elsewhere can be listed in `ST_EXTRA_VAULTS` (several paths separated by `:` on Mac/Linux or `;` on Windows; `mage=/path` pins the game). One vault per game. The **New chronicle** dialog offers every game that has a vault, each with its own campaigns; a game without a vault is shown greyed out.

What a game profile changes (`storyteller/games/<game>/`): the Storyteller's persona and rules prompt, the campaign premises, the summarizer instructions, which vault notes go into the always-loaded prompt, the sheet layout (identity fields, ability lists, trait groups, pools and trackers), the per-turn screen summary, trait caps, and any game-specific tools. Mage adds `roll_magick`, which works out the M20 difficulty, rolls Arete, and applies Quintessence spent and Paradox gained to the sheet. Everything else (the turn loop, dice, possessions, the vault tools, memory condensing, the meter, the UI) is shared.

To get the Mage vault, see its own README: it is a separate folder cloned from the `mage-vault` branch of this repository.

## Several chronicles

The dropdown at the top switches between chronicles. Each has its own transcript, sheet, possessions, scene, memory and private Storyteller notes, and you can keep as many as you like; two can even run at the same time in two browser tabs (one turn at a time per chronicle). The vault is shared, so the app keeps chronicles from reading each other's play: session logs go in `Sessions/<chronicle name>/`, the dated notes it adds to NPC pages are tagged with the chronicle they came from and hidden from the others, and an NPC's `char state` records which chronicle set it. **Delete this chronicle** (side panel) removes one from the app; what it wrote into the vault stays.

## How it works

- **Cached core.** `prompts/system.md`, the Storyteller Reference folder, the campaign overview and blurb, and the Setting folder are sent as the system prompt on every turn. The prompt is stable, so after the first turn it is read from Anthropic's prompt cache at a fraction of the price.
- **On-demand library.** Everything else in the vault is reachable through tools: `search_vault`, `read_note`, `list_notes`. The Storyteller reads an NPC's note before that NPC speaks.
- **Honest mechanics.** Dice are rolled by `roll_dice` (V20 rules, 1s cancel, botches, specialties, Willpower), never by the model. The character sheet changes only through `set_sheet` and `update_trait`, and possessions only through `set_inventory` and `update_inventory`. Hidden rolls show the player only "rolled behind the screen".
- **Memory.** Chat history, the sheet, possessions, the scene, and the Storyteller's private notes live in `storyteller/chronicles.db` (SQLite, gitignored). Private notes hold NPC agendas, clocks, secrets, and a recap.
- **Write-back.** "End session" makes the Storyteller write a log to `Sessions/<chronicle name>/` in the vault, append dated chronicle notes to NPC notes under a `## Chronicle Notes (AI Storyteller)` heading, and update `char state` in frontmatter when someone dies or disappears. It never deletes or rewrites your original text.
- **Caching.** The fixed prefix and the growing conversation are both cached on Anthropic's side with a one-hour lifetime, so a turn re-reads the previous one from cache at a small fraction of the price. A break longer than an hour means the next turn rebuilds the cache once.
- **Long campaigns.** When the context reaches 150K tokens, and at the end of every session, the app asks the API to condense the older turns into one summary (server-side compaction, beta). The summarizer is told exactly what a chronicle needs to keep (`prompts/compaction.md`): NPCs and their attitudes, boons and debts, clues, open threads, the in-fiction calendar, rolls that mattered, secrets, and your out-of-character preferences. The last two turns stay word for word. The sheet, possessions, scene, and the Storyteller's private notes live outside the conversation and are untouched, and on the next turn the Storyteller is shown its notes in full so it picks the thread back up. You can also condense at any time from the side panel.

## Updating without losing your chronicle

Everything you've played lives in `storyteller/chronicles.db`. Before any update, copy that file somewhere safe, such as your Desktop.

- **If you use git:** `git pull`. The database and `.venv` are ignored by git and stay put.
- **If you downloaded a zip:** open the new zip, go into its `storyteller` folder, select everything inside, and drag it into your existing `storyteller` folder, choosing **Replace** for files that already exist. Don't replace the whole `storyteller` folder itself: on a Mac that deletes your database and `.venv`. If that happens, copy `chronicles.db` back from your backup and rerun the `pip install` step.

Then restart the server. If `requirements.txt` changed, rerun `pip install -r storyteller/requirements.txt` first.

**Chronicles from the first version.** If a chronicle grew long enough under the first version for the API to summarize it (an "Input tag 'compaction'" error on every turn was the symptom), the app converts that old summary into plain text at the start of the next turn and carries on. A line in the transcript says so.

**Editing notes mid-chronicle.** You can edit any vault note while a chronicle is running. Changes to the notes loaded into every turn (the Storyteller Reference, Setting, and campaign overview notes) take effect the next time you start the server. When the app detects that the Storyteller's instructions changed, whether from an update or your edits, it clears the model's hidden reasoning from earlier turns once so the API keeps accepting the chronicle. Narration, rolls, and notes are untouched.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `ST_MODEL` | `claude-opus-5-5` | Model id |
| `ST_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| `ST_MAX_TOKENS` | `8000` | Max output tokens per response |
| `ST_GAME` | the home vault's game | Default game in the New chronicle dialog |
| `ST_EXTRA_VAULTS` | (empty) | Extra vault folders, see Games and vaults |
| `ST_CACHE_TTL` | `1h` | Cache lifetime: `1h`, or `5m` if your turns always come less than five minutes apart |
| `ST_COMPACTION` | `1` | Set `0` to never condense (the context then grows until the model's 1M-token limit) |
| `ST_COMPACT_AT` | `150000` | Condense when the context reaches this many tokens |
| `ST_COMPACT_AT_SESSION_END` | `1` | Set `0` to skip condensing when you click End session |
| `ST_KEEP_RECENT_TURNS` | `2` | Turns kept word for word after condensing |
| `ST_FALLBACKS` | `1` | Set `0` to disable server-side refusal fallbacks |
| `ST_SESSION_DIR` | `Sessions` | Vault folder for session logs |
| `VAULT_DIR` | parent folder | Vault root |
| `ST_DB_PATH` | `storyteller/chronicles.db` | SQLite file |

To add a campaign, add an entry to the game's `campaigns` in `storyteller/games/<game>/__init__.py` with the vault notes to cache and a premise file in that game's `prompts/` folder.

## Tests

Offline tests (no API key needed) cover the dice, the vault indexer and its write guards, the system prompt build, the character sheet display (including older sheet shapes), possessions, upgrading an older database, the token meter, memory condensing and the recap that follows it, and the turn loop against a scripted fake client:

```bash
python -m unittest storyteller.tests.test_offline -v
```

## Obsidian

Obsidian ignores the non-markdown files here. To keep this README out of search and graph view, add `storyteller` under Settings → Files and links → Excluded files.
