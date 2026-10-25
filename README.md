# AI Storyteller

A local web app that runs a Storyteller-System chronicle with Claude as the Storyteller. It ships with two games: **Vampire: The Masquerade (V20)** and **Mage: The Ascension (M20)**. Each game is played from its own Obsidian vault. A vault is the Storyteller's library and memory: rules digest, setting, campaign notes, NPC sheets, and sourcebooks are read from its notes, and session logs are written back into it.

The app and the two vaults are three separate repositories. They sit side by side in one parent folder:

```
Chronicles/            any folder, any name
  ai-storyteller/      the app       https://github.com/tax-nerd-problems/verbose-octo-succotash
  Vampire Vault/       V20 notes     https://github.com/tax-nerd-problems/cuddly-octo-system
  Mage Vault/          M20 notes     https://github.com/tax-nerd-problems/super-disco
```

The vault folders can be called whatever you like. The app finds them by a marker note inside each one, not by name (see Games and vaults).

## Setup

```bash
mkdir Chronicles && cd Chronicles
git clone https://github.com/tax-nerd-problems/verbose-octo-succotash ai-storyteller
git clone -b claude/ai-storyteller https://github.com/tax-nerd-problems/cuddly-octo-system "Vampire Vault"   # the vault's marker note is on this branch until it is merged into main
git clone https://github.com/tax-nerd-problems/super-disco "Mage Vault"
cd ai-storyteller
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # Windows: set ANTHROPIC_API_KEY=...
uvicorn storyteller.server:app --port 8000
```

Open http://localhost:8000, click **New chronicle**, pick the game, and the Storyteller opens with character creation.

If you already have the Vampire vault folder, you don't need a fresh copy of it. Clone the app (and the Mage vault) into the folder that contains your vault folder, so the three end up side by side. If your vault still has a `storyteller/` folder inside it, read Moving from the old layout below.

## Playing

- **Your sheet.** The **Sheet** button (top right) opens a full V20 character sheet: attributes, abilities, disciplines, backgrounds, virtues, merits and flaws, Humanity, Willpower, blood pool, health track, and experience. It updates live as the Storyteller changes things.
- **What you're carrying.** The side panel lists what's on you right now, and the sheet's **Possessions** tab shows everything: in hand, worn, pockets, bag, and what's stashed at your haven, in a car, or elsewhere. The Storyteller records every change as the story makes it. Click any gold line in the transcript to jump to the right tab.
- **Reading at your own pace.** When you send a move, your message moves to the top of the screen and the reply appears beneath it. The page never scrolls by itself while the Storyteller writes. If the reply runs past the bottom, a **New text below** button appears; scroll whenever you like.
- **Memory meter.** Click **Memory** at the bottom of the side panel to see how much of the chronicle the model is holding in its head right now (its context), when it will next be condensed, what the last turn cost in tokens and how much of it came from cache, and a running cost estimate for the chronicle. It stays folded away unless you open it. The estimate uses the list prices in `storyteller/config.py`; the Usage page in the Anthropic Console is the real bill.

## Games and vaults

Each game has its own vault. A vault is any folder that contains an `AI Storyteller.md` note whose frontmatter says `game: vtm` or `game: mage`. That marker is all the app goes by, so the folder's name doesn't matter.

Where the app looks:

- **Beside itself.** The app checks the other folders in the parent folder that holds its own folder. That is why the layout above works with no configuration.
- **`ST_VAULTS`.** Vault folders anywhere else. Several paths are separated by `:` on Mac/Linux or `;` on Windows, and `mage=/path` pins the game for a folder. Folders listed here win over folders beside the app. (`ST_EXTRA_VAULTS` is an older name for the same setting and still works.)
- **`VAULT_DIR`.** The older single-vault setting. It still names one vault folder, and a folder it names that has no marker counts as the Vampire vault.

One vault per game. The **New chronicle** dialog offers every game that has a vault, each with its own campaigns; a game whose vault was not found is greyed out. If no vault is found at all, the app still starts and the dialog says so. `ST_GAME` sets the game the dialog starts on; by default it is the first vault found.

The old layout, with the app folder inside the Vampire vault, still works, so nothing breaks if you have not moved yet.

What a game profile changes (`storyteller/games/<game>/`): the Storyteller's persona and rules prompt, the campaign premises, the summarizer instructions, which vault notes go into the always-loaded prompt, the sheet layout (identity fields, ability lists, trait groups, pools and trackers), the per-turn screen summary, trait caps, and any game-specific tools. Mage adds `roll_magick`, which works out the M20 difficulty, rolls Arete, and applies Quintessence spent and Paradox gained to the sheet. Everything else (the turn loop, dice, possessions, the vault tools, memory condensing, the meter, the UI) is shared.

The Mage vault is its own repository, https://github.com/tax-nerd-problems/super-disco. Clone it beside the app folder as in Setup.

## Several chronicles

The dropdown at the top switches between chronicles. Each has its own transcript, sheet, possessions, scene, memory and private Storyteller notes, and you can keep as many as you like; two can even run at the same time in two browser tabs (one turn at a time per chronicle). The vault is shared, so the app keeps chronicles from reading each other's play: session logs go in `Sessions/<chronicle name>/`, the dated notes it adds to NPC pages are tagged with the chronicle they came from and hidden from the others, and an NPC's `char state` records which chronicle set it. **Delete this chronicle** (side panel) removes one from the app; what it wrote into the vault stays.

## How it works

- **Cached core.** The game's `prompts/system.md` (under `storyteller/games/<game>/`), the Storyteller Reference folder, the campaign overview and blurb, and the Setting folder are sent as the system prompt on every turn. The prompt is stable, so after the first turn it is read from Anthropic's prompt cache at a fraction of the price.
- **On-demand library.** Everything else in the vault is reachable through tools: `search_vault`, `read_note`, `list_notes`. The Storyteller reads an NPC's note before that NPC speaks.
- **Honest mechanics.** Dice are rolled by `roll_dice` (V20 rules, 1s cancel, botches, specialties, Willpower), never by the model. The character sheet changes only through `set_sheet` and `update_trait`, and possessions only through `set_inventory` and `update_inventory`. Hidden rolls show the player only "rolled behind the screen".
- **Memory.** Chat history, the sheet, possessions, the scene, and the Storyteller's private notes live in `storyteller/chronicles.db` inside the app folder (SQLite, gitignored). Private notes hold NPC agendas, clocks, secrets, and a recap.
- **Write-back.** "End session" makes the Storyteller write a log to `Sessions/<chronicle name>/` in the vault, append dated chronicle notes to NPC notes under a `## Chronicle Notes (AI Storyteller)` heading, and update `char state` in frontmatter when someone dies or disappears. It never deletes or rewrites your original text.
- **Caching.** The fixed prefix and the growing conversation are both cached on Anthropic's side with a one-hour lifetime, so a turn re-reads the previous one from cache at a small fraction of the price. A break longer than an hour means the next turn rebuilds the cache once.
- **Long campaigns.** When the context reaches 150K tokens, and at the end of every session, the app asks the API to condense the older turns into one summary (server-side compaction, beta). The summarizer is told exactly what a chronicle needs to keep (the game's `prompts/compaction.md`): NPCs and their attitudes, boons and debts, clues, open threads, the in-fiction calendar, rolls that mattered, secrets, and your out-of-character preferences. The last two turns stay word for word. The sheet, possessions, scene, and the Storyteller's private notes live outside the conversation and are untouched, and on the next turn the Storyteller is shown its notes in full so it picks the thread back up. You can also condense at any time from the side panel.

## Updating without losing your chronicle

Everything you've played lives in `storyteller/chronicles.db` inside the app folder (`ai-storyteller/storyteller/chronicles.db`). Before any update, copy that file somewhere safe, such as your Desktop.

- **If you use git:** in the app folder (`ai-storyteller`), run `git pull`. The database and `.venv` are ignored by git and stay put. Each vault updates the same way, with `git pull` in its own folder.
- **If you downloaded a zip:** open the new zip and replace the contents of your `ai-storyteller` folder with what is inside it, choosing **Replace** for files that already exist, but keep two things: the `.venv` folder and `storyteller/chronicles.db`. Don't throw away the whole `ai-storyteller` folder and put the new one in its place: that takes your database and `.venv` with it. If that happens, copy `chronicles.db` back from your backup into `storyteller/` and rerun the `pip install` step.

Then restart the server. If `requirements.txt` changed, rerun `pip install -r requirements.txt` first.

**Chronicles from the first version.** If a chronicle grew long enough under the first version for the API to summarize it (an "Input tag 'compaction'" error on every turn was the symptom), the app converts that old summary into plain text at the start of the next turn and carries on. A line in the transcript says so.

**A turn interrupted by a lost connection.** If the network drops while the Storyteller is answering, the message you sent goes back into the box and nothing is kept from that turn. If the app itself was interrupted first (a release before this one could leave the chronicle refusing every turn with "role 'system' must precede an 'assistant' message"), the next turn removes the unanswered message from the Storyteller's memory and says so in the transcript. Send it again to continue.

**Editing notes mid-chronicle.** You can edit any vault note while a chronicle is running. Changes to the notes loaded into every turn (the Storyteller Reference, Setting, and campaign overview notes) take effect the next time you start the server. When the app detects that the Storyteller's instructions changed, whether from an update or your edits, it clears the model's hidden reasoning from earlier turns once so the API keeps accepting the chronicle. Narration, rolls, and notes are untouched.

## Moving from the old layout

Until now the app lived inside the Vampire vault, in its `storyteller/` folder, and the Mage vault was a branch (`mage-vault`) of the Vampire vault's repository. Now each of the three is its own repository and they sit side by side. To move:

1. **Back up your chronicles.** Copy `<Vampire vault>/storyteller/chronicles.db` somewhere safe.
2. **Clone the app beside the vault**, as in Setup. Either clone it into the folder that already contains your vault folder, or make a new parent folder (say `Chronicles`), move the vault folder into it, and clone the app there. Set up `.venv` and install the requirements in the new app folder.
3. **Bring the database across.** Copy that `chronicles.db` to `ai-storyteller/storyteller/chronicles.db`. Your existing chronicles carry on from where they were.
4. **Update the Vampire vault.** In the vault folder, run `git pull origin claude/ai-storyteller` (the vault's updates live on that branch until it is merged into `main`). The `storyteller/` folder disappears from the vault. If anything is left behind in `storyteller/`, it is the old `.venv` and database, which git never tracked; delete them once the app is running from its new folder.
5. **Replace the Mage vault.** It is now its own repository. Delete any folder you cloned from the old `mage-vault` branch and clone https://github.com/tax-nerd-problems/super-disco beside the app instead, as in Setup. If you have already played Mage, move its `Sessions/` folder and any notes you changed into the new clone first.

The vault folders now hold nothing but notes, so the old Obsidian tip about excluding the `storyteller` folder is no longer needed.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `ST_MODEL` | `claude-opus-5-5` | Model id |
| `ST_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| `ST_MAX_TOKENS` | `8000` | Max output tokens per response |
| `ST_GAME` | the first vault found | Default game in the New chronicle dialog |
| `ST_VAULTS` | the folders beside the app | Vault folders, see Games and vaults |
| `ST_EXTRA_VAULTS` | (empty) | Older name for `ST_VAULTS` |
| `ST_CACHE_TTL` | `1h` | Cache lifetime: `1h`, or `5m` if your turns always come less than five minutes apart |
| `ST_COMPACTION` | `1` | Set `0` to never condense (the context then grows until the model's 1M-token limit) |
| `ST_COMPACT_AT` | `150000` | Condense when the context reaches this many tokens |
| `ST_COMPACT_AT_SESSION_END` | `1` | Set `0` to skip condensing when you click End session |
| `ST_KEEP_RECENT_TURNS` | `2` | Turns kept word for word after condensing |
| `ST_FALLBACKS` | `1` | Set `0` to disable server-side refusal fallbacks |
| `ST_SESSION_DIR` | `Sessions` | Vault folder for session logs |
| `VAULT_DIR` | (empty) | Older single-vault setting: one vault folder, counted as the Vampire vault if it has no marker |
| `ST_DB_PATH` | `storyteller/chronicles.db` | SQLite file |

To add a campaign, add an entry to the game's `campaigns` in `storyteller/games/<game>/__init__.py` with the vault notes to cache and a premise file in that game's `prompts/` folder.

## Tests

Offline tests (no API key needed) cover the dice, the vault indexer and its write guards, the system prompt build, the character sheet display (including older sheet shapes), possessions, upgrading an older database, the token meter, memory condensing and the recap that follows it, and the turn loop against a scripted fake client. Run them from the app folder; they use a small built-in fixture vault under `storyteller/tests/fixtures/vtm` and need no real vault:

```bash
python -m unittest storyteller.tests.test_offline -v
```
