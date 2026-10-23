"""Offline tests: vault indexing, dice, persistence, and the turn loop with a fake API client.

Run from the vault root:  python -m unittest storyteller.tests.test_offline -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

VAULT = Path(__file__).resolve().parents[2]

from storyteller import config  # noqa: E402
from storyteller.db import DB  # noqa: E402
from storyteller.storyteller import Storyteller, build_system  # noqa: E402
from storyteller.tools import TOOL_SPECS  # noqa: E402
from storyteller.vault import Vault  # noqa: E402


class DiceTests(unittest.TestCase):
    def test_botch_and_success_rules(self):
        import storyteller.dice as d

        class FakeRng:
            def __init__(self, seq):
                self.seq = list(seq)

            def randint(self, a, b):
                return self.seq.pop(0)

        d._rng = FakeRng([1, 2, 3])
        r = d.roll(3, 6)
        self.assertTrue(r.botch)
        self.assertEqual(r.successes, 0)

        d._rng = FakeRng([10, 10, 1, 6])
        r = d.roll(4, 6, specialty=True)
        self.assertEqual(r.raw_successes, 5)  # two 10s double, one 6
        self.assertEqual(r.successes, 4)
        self.assertFalse(r.botch)

        d._rng = FakeRng([1, 1])
        r = d.roll(2, 6, willpower=True)
        self.assertFalse(r.botch)
        self.assertEqual(r.successes, 1)
        d._rng = __import__("random").SystemRandom()


class VaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = Vault(VAULT)

    def test_index_and_resolve(self):
        self.assertGreater(len(self.vault.notes), 200)
        n = self.vault.resolve("[[Hakim Olamaz]]")
        self.assertIsNotNone(n)
        self.assertEqual(n.meta.get("generation"), 7)
        self.assertNotIn("\x00", n.body)
        self.assertIsNotNone(self.vault.resolve("Core Mechanics"))
        self.assertIsNotNone(self.vault.resolve("Botch"))  # alias

    def test_search_and_sections(self):
        hits = self.vault.search("Nosferatu Prince Pro Tempore")
        self.assertTrue(any(n.title == "Calebros" for n, _, _ in hits[:3]))
        core = self.vault.resolve("Core Mechanics")
        self.assertIn("Botch", core.section("Dice Basics"))
        self.assertTrue(self.vault.list_by_tag("VTM/npc"))

    def test_writes_are_confined(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "Characters").mkdir()
            (tmp / "Characters" / "Bob.md").write_text("---\nchar state: alive\ntags: [VTM/npc]\n---\n# Bob\n\nA guy.\n")
            v = Vault(tmp)
            rel = v.write_session_log("Session 1 - Test", "It happened.", chronicle="t")
            self.assertTrue(rel.startswith(config.SESSION_LOG_DIR + "/"))
            self.assertTrue((tmp / rel).exists())
            v.append_to_note("Bob", "Owes the coterie a boon.")
            txt = (tmp / "Characters" / "Bob.md").read_text()
            self.assertIn("Chronicle Notes (AI Storyteller)", txt)
            self.assertIn("A guy.", txt)
            v.set_frontmatter("Bob", "char state", "dead")
            self.assertEqual(v.resolve("Bob").meta["char state"], "dead")
            with self.assertRaises(ValueError):
                v.set_frontmatter("Bob", "clan", "Brujah")
            with self.assertRaises(ValueError):
                v._safe_path("../escape.md")
        finally:
            shutil.rmtree(tmp)


class SystemPromptTests(unittest.TestCase):
    def test_build_system(self):
        blocks = build_system("campaign1", VAULT)
        self.assertEqual(blocks[-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        text = "\n".join(b["text"] for b in blocks)
        self.assertIn("Core Mechanics", text)
        self.assertIn("Eleazar", text)
        self.assertIn("roll_dice", text)
        words = len(text.split())
        self.assertLess(words, 40000, "system prompt grew too large")


# ---------- fake API client ----------
class _Block(SimpleNamespace):
    def model_dump(self, **kw):
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}


class FakeStream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.final.content:
            if b.type == "text":
                yield SimpleNamespace(type="text", text=b.text)
            elif b.type == "tool_use":
                yield SimpleNamespace(type="content_block_start", content_block=b)

    def get_final_message(self):
        return self.final


class FakeClient:
    """Scripted responses; records the kwargs of each request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.requests.append(kw)
        return FakeStream(self.script.pop(0))


def usage(input=1000, cache_read=0, cache_write=0, output=200, ttl="1h", iterations=None):
    cc = SimpleNamespace(ephemeral_5m_input_tokens=cache_write if ttl == "5m" else 0,
                         ephemeral_1h_input_tokens=cache_write if ttl == "1h" else 0)
    return SimpleNamespace(input_tokens=input, cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
                           cache_creation=cc, output_tokens=output, iterations=iterations)


def msg(*blocks, stop="end_turn", use=None):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=use or usage())


def _Msg(blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage())


def text(t):
    return _Block(type="text", text=t)


def tool(id_, tool_name, **inp):
    return _Block(type="tool_use", id=id_, name=tool_name, input=inp)


class TurnLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DB(self.tmp / "t.db")
        self.vault = Vault(VAULT)
        self.ch = self.db.create_chronicle("Test", "campaign1")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_turn_with_tools(self):
        client = FakeClient([
            msg(text("Rene smiles. Roll it."), tool("t1", "roll_dice", pool=5, difficulty=6, label="Charisma + Etiquette"),
                tool("t2", "set_scene", location="The Met", time="Sept 2001, 11pm", present=["Rene Valentine"], tension="tense"),
                stop="tool_use"),
            msg(tool("t3", "read_note", name="Rene Valentine"), tool("t4", "update_trait", path="blood", delta=-1, reason="Blush of Life"), stop="tool_use"),
            msg(text("He is impressed.")),
        ])
        st = Storyteller(self.db, self.vault, client)
        events = list(st.turn(self.ch["id"], "I bow to Rene."))
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds[0], "player")
        self.assertIn("dice", kinds)
        self.assertIn("state", kinds)  # live sheet/scene refresh
        self.assertIn("trait", kinds)
        self.assertIn("narration", kinds)
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(len(client.requests), 3)
        # history shape: user, system, assistant, user(tool_results), assistant, user(tool_results), assistant
        hist = self.db.get_messages(self.ch["id"])
        self.assertEqual([m["role"] for m in hist], ["user", "system", "assistant", "user", "assistant", "user", "assistant"])
        self.assertEqual(hist[3]["content"][0]["type"], "tool_result")
        self.assertIn("Rene", hist[5]["content"][0]["content"])  # read_note returned the sheet
        # request shape
        kw = client.requests[0]
        self.assertEqual(kw["model"], config.MODEL)
        self.assertEqual(kw["output_config"], {"effort": config.EFFORT})
        self.assertEqual(kw["tools"], TOOL_SPECS)
        self.assertTrue(all(t.get("eager_input_streaming") for t in kw["tools"]))
        self.assertEqual(kw["system"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(kw["cache_control"], {"type": "ephemeral", "ttl": "1h"})  # conversation tail is cached too
        self.assertNotIn("context_management", kw)  # no threshold compaction: the app condenses on request
        self.assertEqual(kw["messages"][-1]["role"], "system")
        self.assertNotIn("compact-2026-09-04", kw.get("betas", []))  # no summary block yet
        # state persisted
        ch = self.db.get_chronicle(self.ch["id"])
        self.assertEqual(ch["scene"]["location"], "The Met")
        self.assertEqual(ch["sheet"]["blood"], 0)  # never below zero
        # live state events are not saved to the transcript
        self.assertFalse(any(e["kind"] == "state" for e in self.db.get_events(self.ch["id"])))
        done = events[-1]
        self.assertEqual(done["scene"]["location"], "The Met")
        self.assertIn("view", done)
        self.assertNotIn("notes", done)
        # usage was recorded: three requests, the last one's context is the meter reading
        m = done["meter"]
        self.assertEqual(m["total"]["requests"], 3)
        self.assertEqual(m["context"], 1000)
        self.assertEqual(m["limit"], config.COMPACT_AT)
        self.assertIsNotNone(m["cost"])
        self.assertTrue(any(e["kind"] == "dice" and not e.get("hidden") and len(e["dice"]) == 5 for e in self.db.get_events(self.ch["id"])))

    def test_hidden_roll_and_bad_tool(self):
        client = FakeClient([
            msg(tool("t1", "roll_dice", pool=4, difficulty=7, label="secret", hidden=True), tool("t2", "read_note", name="Nobody Real At All"),
                tool("t3", "update_trait", path="blood", reason="no delta"), stop="tool_use"),
            msg(text("...")),
        ])
        st = Storyteller(self.db, self.vault, client)
        events = list(st.turn(self.ch["id"], "I wait."))
        dice_ev = next(e for e in events if e["kind"] == "dice")
        self.assertTrue(dice_ev["hidden"])
        self.assertNotIn("dice", dice_ev)
        results = client.requests[1]["messages"][-1]["content"]
        self.assertTrue(json.loads(results[0]["content"])["dice"])  # model sees the numbers
        self.assertIn("no note named", results[1]["content"])
        self.assertTrue(results[2]["is_error"])

    def test_api_error_unwinds_history(self):
        import anthropic

        class Boom(FakeClient):
            def _stream(self, **kw):
                raise anthropic.APIConnectionError(request=None)

        st = Storyteller(self.db, self.vault, Boom([]))
        events = list(st.turn(self.ch["id"], "Hello"))
        err = events[-2]
        self.assertEqual(err["kind"], "error")
        self.assertEqual(err["restore_text"], "Hello")
        self.assertEqual(self.db.get_messages(self.ch["id"]), [])
        self.assertEqual(self.db.get_events(self.ch["id"]), [])  # the unsent message leaves no trace


# ---------- sheet view ----------
from storyteller.sheet import sheet_view  # noqa: E402

CANONICAL = {
    "name": "Anja", "concept": "Disgraced violinist", "clan": "Toreador", "generation": 11, "sire": "Rene Valentine",
    "nature": "Visionary", "demeanor": "Bon Vivant",
    "attributes": {"physical": {"strength": 2, "dexterity": 3, "stamina": 2},
                   "social": {"charisma": 4, "manipulation": 3, "appearance": 4},
                   "mental": {"perception": 3, "intelligence": 2, "wits": 3}},
    "abilities": {"talents": {"empathy": 3, "subterfuge": 2}, "skills": {"performance": 4, "animal_ken": 1, "dancing": 2},
                  "knowledges": {"occult": 1}},
    "specialties": {"performance": "Violin"},
    "disciplines": {"auspex": 1, "celerity": 1, "presence": 2},
    "backgrounds": {"resources": 2},
    "virtues": {"courage": 2, "conscience": 3, "self_control": 4},
    "merits_flaws": [{"name": "Eat Food", "points": 1, "type": "merit"}, {"name": "Haunted", "points": 3, "type": "flaw"}],
    "humanity": 7, "willpower": 3, "willpower_max": 4, "blood": 9, "blood_max": 12,
    "health": {"bashing": 1, "lethal": 2, "aggravated": 1},
    "experience": {"unspent": 2, "total": 5}, "weakness": "Rapture", "notes": "Owes Rene a boon.",
}


class SheetViewTests(unittest.TestCase):
    def test_canonical(self):
        v = sheet_view(CANONICAL)
        self.assertEqual(v["subtitle"], "Toreador · 11th generation · Disgraced violinist")
        social = v["attributes"][1]["traits"]
        self.assertEqual([(t["name"], t["rating"]) for t in social], [("Charisma", 4), ("Manipulation", 3), ("Appearance", 4)])
        skills = {t["name"]: t for t in v["abilities"][1]["traits"]}
        self.assertEqual(skills["Performance"]["specialty"], "Violin")
        self.assertEqual(skills["Animal Ken"]["rating"], 1)
        self.assertEqual(skills["Dancing"]["rating"], 2)  # secondary ability stays in its group
        self.assertEqual(len(v["abilities"][0]["traits"]), 10)
        self.assertEqual([t["name"] for t in v["virtues"]], ["Conscience", "Self-Control", "Courage"])
        self.assertEqual(v["blood"], {"current": 9, "max": 12, "per_turn": 1})
        self.assertEqual(v["willpower"], {"current": 3, "max": 4})
        self.assertEqual([b["mark"] for b in v["health"]["boxes"]], ["*", "X", "X", "/", "", "", ""])
        self.assertEqual((v["health"]["status"], v["health"]["penalty"]), ("Wounded", -2))
        self.assertEqual([m["kind"] for m in v["merits_flaws"]], ["merit", "flaw"])
        self.assertEqual([x["label"] for x in v["extras"]], ["Notes"])

    def test_legacy_shapes(self):
        legacy = {
            "Name": "Bea", "Clan": "Malkavian", "Generation": "12th",
            "Strength": 1, "Dexterity": 2, "Charisma": "3",
            "abilities": ["Brawl 2", "Stealth (Shadows) 3", "Occult: 1"],
            "disciplines": ["Auspex 2", "Obfuscate 1"],
            "merits_flaws": ["Eat Food (1pt Merit)", "Nightmares (-1)"],
            "path": {"name": "Path of Night", "rating": 5},
            "willpower": {"permanent": 5, "temporary": 2},
            "health": "Injured (-1)", "experience": 4, "equipment": ["Knife"],
        }
        v = sheet_view(legacy)
        self.assertEqual(v["name"], "Bea")
        phys = {t["name"]: t["rating"] for t in v["attributes"][0]["traits"]}
        self.assertEqual(phys, {"Strength": 1, "Dexterity": 2, "Stamina": 0})
        self.assertEqual(v["attributes"][1]["traits"][0]["rating"], 3)
        skills = {t["name"]: t for t in v["abilities"][1]["traits"]}
        self.assertEqual((skills["Stealth"]["rating"], skills["Stealth"]["specialty"]), (3, "Shadows"))
        self.assertEqual(v["morality"], {"label": "Path of Night", "rating": 5})
        self.assertEqual(v["willpower"], {"current": 2, "max": 5})
        self.assertEqual(v["health"]["status"], "Injured")
        self.assertEqual(sum(1 for b in v["health"]["boxes"] if b["mark"]), 3)
        self.assertEqual(v["merits_flaws"][1], {"name": "Nightmares", "points": 1, "kind": "flaw", "note": None})
        self.assertEqual(v["experience"]["unspent"], 4)
        self.assertIn("Equipment", [x["label"] for x in v["extras"]])
        self.assertIsNone(v["blood"])

    def test_empty_and_inventory(self):
        v = sheet_view({}, {"seq": 3, "items": [
            {"id": "i1", "name": "Glock 17", "location": "held"},
            {"id": "i2", "name": "Cash", "location": "pockets", "qty": 340},
            {"id": "i3", "name": "Laptop", "location": "haven", "detail": "under the bed"},
        ]})
        self.assertTrue(v["empty"])
        inv = v["inventory"]
        self.assertEqual([g["key"] for g in inv["carried"]], ["held", "pockets"])
        self.assertEqual([g["key"] for g in inv["stashed"]], ["haven"])
        self.assertEqual(inv["count"], 3)


# ---------- possessions & traits ----------
from storyteller.tools import ToolContext  # noqa: E402


class InventoryToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DB(self.tmp / "t.db")
        self.ch = self.db.create_chronicle("Inv", "campaign1")
        self.events = []
        self.ctx = ToolContext(self.db, None, self.ch["id"], lambda k, p: self.events.append((k, p)))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def items(self):
        return self.db.get_chronicle(self.ch["id"])["inventory"]["items"]

    def test_inventory_lifecycle(self):
        run = self.ctx.run
        run("set_inventory", {"items": [
            {"name": "Cash", "location": "pockets", "qty": 340, "detail": "wallet"},
            {"name": "Keys", "location": "pockets"},
            {"name": "Glock 17", "location": "bag", "notes": "4L, 17 rounds"},
            {"name": "Car keys", "location": "glovebox of the Civic"},  # free text is mapped
        ], "reason": "Starting possessions"})
        self.assertEqual([i["id"] for i in self.items()], ["i1", "i2", "i3", "i4"])
        self.assertEqual(self.items()[3]["location"], "vehicle")
        run("update_inventory", {"action": "remove", "item": "Cash", "qty": 40, "reason": "Paid the cabbie"})
        self.assertEqual(self.items()[0]["qty"], 300)
        run("update_inventory", {"action": "move", "item": "i3", "location": "held", "reason": "Draws the gun"})
        self.assertEqual(self.items()[2]["location"], "held")
        run("update_inventory", {"action": "add", "item": "Rene's calling card", "location": "pockets", "reason": "A gift"})
        self.assertEqual(self.items()[-1]["id"], "i5")
        run("update_inventory", {"action": "move", "item": "cash", "location": "haven", "qty": 100, "detail": "coffee tin", "reason": "Stashed"})
        cash = [i for i in self.items() if i["name"] == "Cash"]
        self.assertEqual(sorted((i["location"], i["qty"]) for i in cash), [("haven", 100), ("pockets", 200)])
        with self.assertRaises(ValueError) as cm:
            run("update_inventory", {"action": "remove", "item": "cash", "reason": "x"})
        self.assertIn("several", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            run("update_inventory", {"action": "remove", "item": "crowbar", "reason": "x"})
        self.assertIn("no possession", str(cm.exception))
        run("update_inventory", {"action": "update", "item": "i3", "notes": "4L, 15 rounds", "reason": "Fired twice"})
        run("update_inventory", {"action": "remove", "item": "Keys", "reason": "Lost in the fight"})
        self.assertNotIn("Keys", [i["name"] for i in self.items()])
        ops = [p["op"] for k, p in self.events if k == "item"]
        self.assertEqual(ops, ["set", "remove", "move", "add", "move", "update", "remove"])
        self.assertIn("Possessions", self.ctx.run("read_sheet", {}))

    def test_trait_updates(self):
        run = self.ctx.run
        run("set_sheet", {"sheet": {"name": "A", "blood": 5, "blood_max": 12, "health": "Healthy", "possessions": ["x"]}})
        self.assertNotIn("possessions", self.db.get_chronicle(self.ch["id"])["sheet"])
        out = json.loads(run("update_trait", {"path": "blood", "delta": 20, "reason": "Fed"}))
        self.assertEqual((out["new"], out.get("clamped")), (12, True))
        with self.assertRaises(ValueError) as cm:
            run("update_trait", {"path": "health.lethal", "delta": 1, "reason": "Shot"})
        self.assertIn("not an object", str(cm.exception))
        run("update_trait", {"path": "health", "value": {"bashing": 0, "lethal": 0, "aggravated": 0}, "reason": "Track damage"})
        run("update_trait", {"path": "health.lethal", "delta": 2, "reason": "Shot"})
        self.assertEqual(self.db.get_chronicle(self.ch["id"])["sheet"]["health"]["lethal"], 2)


# ---------- upgrading an existing save ----------
import sqlite3  # noqa: E402

import anthropic  # noqa: E402

from storyteller import storyteller as stmod  # noqa: E402


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = Vault(VAULT)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _legacy_db(self):
        """A database exactly as the first release created it, with one played turn."""
        path = self.tmp / "old.db"
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE chronicles (id TEXT PRIMARY KEY, name TEXT NOT NULL, campaign TEXT NOT NULL, created TEXT NOT NULL,
                sheet TEXT NOT NULL DEFAULT '{}', scene TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE messages (chronicle_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                PRIMARY KEY (chronicle_id, seq));
            CREATE TABLE events (chronicle_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (chronicle_id, seq));
        """)
        c.execute("INSERT INTO chronicles VALUES ('abc', 'Old', 'campaign1', '2026-10-19', ?, '{}', '{}')",
                  (json.dumps({"name": "Anja", "health": "Healthy"}),))
        rows = [
            ("user", [{"type": "text", "text": "[The table is ready. Begin.]"}]),
            ("system", "Storyteller screen: {}"),
            ("assistant", [{"type": "thinking", "thinking": "", "signature": "sig1"}, {"type": "text", "text": "Welcome."}]),
            ("user", [{"type": "text", "text": "I bow."}]),
            ("system", "Storyteller screen: {}"),
            ("assistant", [{"type": "thinking", "thinking": "", "signature": "sig2"}]),
        ]
        for i, (role, content) in enumerate(rows, 1):
            c.execute("INSERT INTO messages VALUES ('abc', ?, ?, ?)", (i, role, json.dumps(content)))
        c.commit()
        c.close()
        return DB(path)

    def test_old_database_upgrades_and_old_thinking_is_cleared(self):
        db = self._legacy_db()
        ch = db.get_chronicle("abc")
        self.assertEqual(ch["inventory"], {"seq": 0, "items": []})
        self.assertEqual(ch["prefix_hash"], "")
        client = FakeClient([msg(text("The night goes on."))])
        list(Storyteller(db, self.vault, client).turn("abc", "I look around."))
        sent = client.requests[0]["messages"]
        blocks = [b for m in sent if m["role"] == "assistant" for b in m["content"]]
        self.assertFalse([b for b in blocks if b["type"] == "thinking"])
        self.assertEqual(sent[5]["content"], [{"type": "text", "text": "…"}])  # an all-thinking turn keeps a placeholder
        self.assertTrue(db.get_chronicle("abc")["prefix_hash"])

    def test_unchanged_instructions_keep_thinking(self):
        db = DB(self.tmp / "new.db")
        ch = db.create_chronicle("New", "campaign1")
        st = Storyteller(db, self.vault, FakeClient([
            _Msg([_Block(type="thinking", thinking="", signature="s"), text("One.")]),
            _Msg([_Block(type="thinking", thinking="", signature="t"), text("Two.")]),
        ]))
        list(st.turn(ch["id"], "a"))
        list(st.turn(ch["id"], "b"))
        second = st.client.requests[1]["messages"]
        self.assertIn("thinking", [b["type"] for m in second if m["role"] == "assistant" for b in m["content"]])

    def test_rejected_thinking_is_cleared_and_retried(self):
        db = DB(self.tmp / "heal.db")
        ch = db.create_chronicle("Heal", "campaign1")
        db.append_message(ch["id"], "user", [{"type": "text", "text": "hi"}])
        db.append_message(ch["id"], "system", "screen")
        db.append_message(ch["id"], "assistant", [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "Hello."}])
        st = Storyteller(db, self.vault, None)
        db.set_prefix_hash(ch["id"], stmod.prefix_hash(st.system_for("campaign1"), stmod.TOOL_SPECS))

        class Rejecting(FakeClient):
            def _stream(self, **kw):
                self.requests.append(kw)
                if len(self.requests) == 1:
                    import httpx2
                    resp = httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
                    raise anthropic.BadRequestError(
                        "messages.2.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation.",
                        response=resp, body={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid `signature` in `thinking` block"}},
                    )
                return FakeStream(self.script.pop(0))

        st.client = Rejecting([msg(text("Recovered."))])
        events = list(st.turn(ch["id"], "again"))
        self.assertIn("narration", [e["kind"] for e in events])
        self.assertEqual(len(st.client.requests), 2)
        retry = st.client.requests[1]["messages"]
        self.assertFalse([b for m in retry if m["role"] == "assistant" for b in m["content"] if b["type"] == "thinking"])


# ---------- meter ----------
from storyteller import meter  # noqa: E402


class MeterTests(unittest.TestCase):
    def test_record_sums_iterations_and_reads_context(self):
        st = meter.record({}, usage(input=500, cache_read=40000, cache_write=3000, output=900))
        self.assertEqual(st["context"], 43500)
        self.assertEqual(st["total"]["cache_write_1h"], 3000)
        self.assertEqual(st["total"]["requests"], 1)
        its = [SimpleNamespace(type="compaction", input_tokens=150000, output_tokens=3500, cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None),
               SimpleNamespace(type="message", input_tokens=23000, output_tokens=1000, cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None)]
        st = meter.record(st, usage(input=23000, output=1000, iterations=its))
        self.assertEqual(st["total"]["compactions"], 1)
        self.assertEqual(st["total"]["input"], 500 + 150000 + 23000)
        self.assertEqual(st["context"], 23000)  # the normal step, not the condensing step
        v = meter.view(st, "claude-opus-5-5")
        self.assertEqual(v["context"], 23000)
        self.assertAlmostEqual(v["cost"], (173500 * 4 + 40000 * 0.2 + 3000 * 8 + 5400 * 20) / 1e6, places=6)
        self.assertIsNone(meter.view(st, "some-unknown-model")["cost"])

    def test_turn_resets(self):
        st = meter.record({}, usage(input=100, output=10))
        st = meter.start_turn(st)
        st = meter.record(st, usage(input=50, cache_read=200, output=5))
        self.assertEqual(st["turn"]["input"], 50)
        self.assertEqual(st["total"]["input"], 150)
        self.assertAlmostEqual(meter.view(st)["turn"]["cached"], 200 / 250)


# ---------- condensing & recap ----------
def compaction_block(text="Summary of the nights so far.", **extra):
    return _Block(type="compaction", content=text, encrypted_content="enc", signature="sig", **extra)


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DB(self.tmp / "t.db")
        self.vault = Vault(VAULT)
        self.ch = self.db.create_chronicle("C", "campaign1")
        self.cid = self.ch["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _play(self, st, n):
        for i in range(n):
            list(st.turn(self.cid, f"move {i + 1}"))

    def test_split_keeps_recent_player_turns(self):
        msgs = []
        for i in range(4):
            msgs += [{"role": "user", "content": [{"type": "text", "text": f"move {i}"}]}, {"role": "system", "content": "screen"},
                     {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "roll_dice", "input": {}}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "{}"}]},
                     {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
        older, recent = Storyteller._split_for_compaction(msgs, 2)
        self.assertEqual(len(older), 10)
        self.assertEqual(recent[0]["content"][0]["text"], "move 2")
        self.assertEqual(Storyteller._split_for_compaction(msgs, 0), (msgs, []))
        self.assertEqual(Storyteller._split_for_compaction(msgs, 9), ([], msgs))

    def test_condense_replaces_history_and_recap_follows(self):
        class Client(FakeClient):
            def __init__(self, script):
                super().__init__(script)
                self.creates = []

            def _stream(self, **kw):
                if "compaction" in kw:
                    self.creates.append(kw)
                    final = SimpleNamespace(content=[compaction_block()], stop_reason="compaction",
                                            usage=usage(input=20000, output=800, iterations=[SimpleNamespace(type="compaction", input_tokens=20000, output_tokens=800, cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None)]))
                    return FakeStream(final)
                return super()._stream(**kw)

        client = Client([msg(text(f"Night {i}.")) for i in range(6)])
        st = Storyteller(self.db, self.vault, client)
        self._play(st, 4)
        self.db.set_notes(self.cid, {"recap": "Anja owes Rene a boon.", "secrets": "Eleazar watches."})
        events = list(st.compact(self.cid, reason="requested"))
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds[0], "working")
        self.assertIn("memory", kinds)
        self.assertEqual(kinds[-1], "done")
        # the condensing request: older turns only, the summarizer instructions, the beta
        req = client.creates[0]
        self.assertEqual(req["compaction"]["type"], "summarize")
        self.assertIn("Vampire", req["compaction"]["instructions"])
        self.assertEqual(req["betas"], ["compact-2026-09-04"])
        self.assertEqual(req["tools"], TOOL_SPECS)  # identical to play, so kept turns' thinking stays valid
        self.assertEqual(len(req["messages"]), 6)  # moves 1-2: user, system, assistant each
        self.assertNotIn("cache_control", req)
        # history now starts with the summary block, followed by the last two turns verbatim
        hist = self.db.get_messages(self.cid)
        self.assertEqual(hist[0]["role"], "assistant")
        self.assertEqual(hist[0]["content"][0]["type"], "compaction")
        self.assertEqual(hist[0]["content"][0]["signature"], "sig")
        self.assertEqual(hist[1]["content"][0]["text"], "move 3")
        self.assertEqual(len(hist), 7)
        self.assertEqual(self.db.get_chronicle(self.cid)["usage"]["total"]["compactions"], 1)
        self.assertTrue(self.db.get_chronicle(self.cid)["usage"]["estimated"])  # until the next real reading
        self.assertTrue(self.db.get_chronicle(self.cid)["recap_due"])
        # the next turn shows the notes in full, then clears the flag
        list(st.turn(self.cid, "move 5"))
        sent = client.requests[-1]["messages"]
        self.assertEqual(sent[0]["content"][0]["type"], "compaction")
        self.assertIn("compact-2026-09-04", client.requests[-1]["betas"])  # the block needs the beta on every request
        self.assertFalse(self.db.get_chronicle(self.cid)["usage"]["estimated"])  # a real reading again
        screen = [m for m in sent if m["role"] == "system"][-1]["content"]
        self.assertIn("condensed", screen)
        self.assertIn("Anja owes Rene a boon.", screen)
        self.assertFalse(self.db.get_chronicle(self.cid)["recap_due"])
        list(st.turn(self.cid, "move 6"))
        screen2 = [m for m in client.requests[-1]["messages"] if m["role"] == "system"][-1]["content"]
        self.assertNotIn("Anja owes Rene a boon.", screen2)

    def test_threshold_and_session_end_trigger(self):
        class Client(FakeClient):
            def __init__(self, script):
                super().__init__(script)
                self.creates = []

            def _stream(self, **kw):
                if "compaction" in kw:
                    self.creates.append(kw)
                    return FakeStream(SimpleNamespace(content=[compaction_block()], stop_reason="compaction", usage=usage(input=100, output=50)))
                return super()._stream(**kw)

        big = usage(input=1000, cache_read=config.COMPACT_AT, output=100)
        client = Client([msg(text("a")), msg(text("b")), msg(text("c"), use=big), msg(text("d")), msg(text("e"))])
        st = Storyteller(self.db, self.vault, client)
        self._play(st, 2)
        self.assertEqual(client.creates, [])
        events = list(st.turn(self.cid, "move 3"))  # this reply reports a context past the limit
        self.assertEqual(len(client.creates), 1)
        self.assertTrue(any(e["kind"] == "memory" and "condensed" in e["text"] for e in events))
        self.assertEqual(events[-1]["kind"], "done")
        list(st.turn(self.cid, "move 4"))
        self.assertEqual(len(client.creates), 1)  # small context again: no condensing
        list(st.turn(self.cid, "move 5", compact_after=True))
        self.assertEqual(len(client.creates), 2)  # session end condenses

    def test_failed_summary_keeps_history(self):
        class Client(FakeClient):
            def _stream(self, **kw):
                if "compaction" in kw:
                    return FakeStream(SimpleNamespace(content=[], stop_reason="tool_use", usage=usage(input=100, output=20)))
                return super()._stream(**kw)

        client = Client([msg(text("a")) for _ in range(4)])
        st = Storyteller(self.db, self.vault, client)
        self._play(st, 4)
        before = self.db.get_messages(self.cid)
        events = list(st.compact(self.cid))
        self.assertIn("tried to call a tool", next(e for e in events if e["kind"] == "memory")["text"])
        self.assertEqual(self.db.get_messages(self.cid), before)
        self.assertFalse(self.db.get_chronicle(self.cid)["recap_due"])

    def test_guards(self):
        client = FakeClient([msg(text("a"))])
        st = Storyteller(self.db, self.vault, client)
        self._play(st, 1)
        events = list(st.compact(self.cid))
        self.assertIn("nothing old enough", events[0]["text"])
        self.db.append_message(self.cid, "user", [{"type": "tool_result", "tool_use_id": "x", "content": "dangling"}])
        events = list(st.compact(self.cid))
        self.assertIn("did not finish cleanly", events[0]["text"])


# ---------- several chronicles sharing one vault ----------
from storyteller.vault import slug  # noqa: E402


class SharedVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "Characters").mkdir()
        (self.tmp / "Characters" / "Bob.md").write_text("---\nchar state: alive\ntags: [VTM/npc]\n---\n# Bob\n\nA guy.\n")
        self.v = Vault(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_session_logs_are_separated_and_hidden_from_each_other(self):
        a = self.v.write_session_log("Session 1 - The Ball", "Anja met Rene.", chronicle="Anja in New York")
        b = self.v.write_session_log("Session 1 - The Ball", "Bea met Calebros.", chronicle="Bea: a second run")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(f"{config.SESSION_LOG_DIR}/{slug('Anja in New York')}/"))
        self.assertTrue((self.tmp / a).exists() and (self.tmp / b).exists())
        hits = [n.rel for n, _, _ in self.v.search("Session Ball", chronicle="Anja in New York")]
        self.assertIn(a, hits)
        self.assertNotIn(b, hits)
        self.assertEqual([n.rel for n in self.v.list_by_tag("VTM/session", chronicle="Bea: a second run")], [b])
        self.assertEqual(len(self.v.list_by_tag("VTM/session")), 2)  # no chronicle given: everything
        # same title in two chronicles: each resolves to its own
        self.assertEqual(self.v.resolve("2026-10-23 - Session 1 - The Ball".replace("2026-10-23", a.split("/")[-1][:10]), "Bea: a second run").rel, b)
        self.assertEqual(self.v.resolve(a.split("/")[-1][:-3], "Anja in New York").rel, a)

    def test_npc_notes_are_tagged_and_filtered(self):
        self.v.append_to_note("Bob", "Owes Anja a boon.", chronicle="Anja in New York")
        self.v.append_to_note("Bob", "Hates Bea.", chronicle="Bea: a second run")
        self.v.append_to_note("Bob", "An untagged, older note.")
        bob = self.v.resolve("Bob")
        raw = bob.body
        self.assertIn("· Anja in New York** — Owes Anja a boon.", raw)
        seen_by_anja = self.v.body_for(bob, "Anja in New York")
        self.assertIn("Owes Anja a boon.", seen_by_anja)
        self.assertNotIn("Hates Bea.", seen_by_anja)
        self.assertIn("An untagged, older note.", seen_by_anja)
        self.assertIn("A guy.", seen_by_anja)
        self.assertEqual(self.v.body_for(bob, None), raw)

    def test_character_state_names_its_chronicle(self):
        self.v.set_character_state("Bob", "dead", chronicle="Anja in New York")
        bob = self.v.resolve("Bob")
        self.assertEqual(bob.meta["char state"], "dead")
        self.assertEqual(bob.meta["char state chronicle"], "Anja in New York")
        self.assertIn("char state → dead", bob.body)
        self.assertEqual(self.v.meta_for(bob, "Anja in New York")["char state"], "dead")
        other = self.v.meta_for(bob, "Bea: a second run")["char state"]
        self.assertIn("not this one", other)
        self.assertNotIn("char state chronicle", self.v.meta_for(bob, "Bea: a second run"))

    def test_tools_are_scoped(self):
        db = DB(self.tmp / "t.db")
        a = db.create_chronicle("Anja in New York", "campaign1")
        b = db.create_chronicle("Bea: a second run", "campaign1")
        ctx_a = ToolContext(db, self.v, a["id"], lambda *x: None)
        ctx_b = ToolContext(db, self.v, b["id"], lambda *x: None)
        ctx_a.run("set_character_state", {"name": "Bob", "state": "torpor"})
        ctx_a.run("append_to_vault_note", {"name": "Bob", "text": "Staked under the Cloisters."})
        ctx_a.run("write_session_log", {"title": "Session 2", "content": "Bob fell."})
        out_b = ctx_b.run("read_note", {"name": "Bob"})
        self.assertIn("not this one", out_b)
        self.assertNotIn("Staked under the Cloisters.", out_b)
        self.assertIn("Staked under the Cloisters.", ctx_a.run("read_note", {"name": "Bob"}))
        self.assertIn("no note named", ctx_b.run("read_note", {"name": "Session 2"}))
        self.assertIn("Bob fell.", ctx_a.run("read_note", {"name": "Session 2"}))


# ---------- the server keeps one turn per chronicle ----------
from storyteller import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class ServerLockTests(unittest.TestCase):
    def test_second_turn_on_same_chronicle_is_refused_until_the_first_ends(self):
        def gen():
            yield {"kind": "delta"}
            yield {"kind": "done"}
        first = server.exclusive("c1", gen())
        next(first)  # streaming has begun
        with self.assertRaises(HTTPException) as cm:
            server.exclusive("c1", gen())
        self.assertEqual(cm.exception.status_code, 409)
        other = server.exclusive("c2", gen())  # a different chronicle runs side by side
        self.assertEqual(list(other)[-1]["kind"], "done")
        list(first)  # drain: the lock is released
        self.assertEqual(list(server.exclusive("c1", gen()))[-1]["kind"], "done")

    def test_lock_is_released_when_the_client_disconnects(self):
        def gen():
            yield {"kind": "delta"}
            yield {"kind": "done"}
        g = server.exclusive("c3", gen())
        next(g)
        g.close()  # the browser went away mid-stream
        self.assertEqual(list(server.exclusive("c3", gen()))[-1]["kind"], "done")

    def test_delete_removes_everything_for_that_chronicle_only(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            db = DB(tmp / "t.db")
            a = db.create_chronicle("A", "campaign1")
            b = db.create_chronicle("B", "campaign1")
            for cid in (a["id"], b["id"]):
                db.append_message(cid, "user", [{"type": "text", "text": "hi"}])
                db.append_event(cid, "player", {"text": "hi"})
            db.delete_chronicle(a["id"])
            self.assertIsNone(db.get_chronicle(a["id"]))
            self.assertEqual(db.get_messages(a["id"]), [])
            self.assertEqual(db.get_events(a["id"]), [])
            self.assertEqual(len(db.get_messages(b["id"])), 1)
            self.assertEqual([c["name"] for c in db.list_chronicles()], ["B"])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
