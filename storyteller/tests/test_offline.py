"""Offline tests: vault indexing, dice, persistence, and the turn loop with a fake API client.

Run from the vault root:  python -m unittest storyteller.tests.test_offline -v
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

VAULT = Path(__file__).resolve().parents[2]

from storyteller import config, dice  # noqa: E402
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
        self.assertEqual(blocks[-1]["cache_control"], {"type": "ephemeral"})
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


def msg(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


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
        self.assertIn("scene", kinds)
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
        self.assertEqual(kw["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["messages"][-1]["role"], "system")
        # state persisted
        ch = self.db.get_chronicle(self.ch["id"])
        self.assertEqual(ch["scene"]["location"], "The Met")
        self.assertEqual(ch["sheet"]["blood"], -1)
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
        self.assertEqual(events[-2]["kind"], "error")
        self.assertEqual(self.db.get_messages(self.ch["id"]), [])


if __name__ == "__main__":
    unittest.main()
