"""edit_smart_crate.py: synthetic .scrate files only."""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fixtures  # noqa: E402
import edit_smart_crate as E  # noqa: E402

TOOL = SCRIPTS_DIR / "edit_smart_crate.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
C = E.chunk
U = E.u16


def brut(flag):
    return b"brut" + struct.pack(">I", 1) + bytes([flag])


def rule(op, field, value):
    return C("rurt", C("trft", U(op)) + C("urkt", struct.pack(">I", field)) + C("trpt", U(value)))


def crate_bytes(rules, match_all=True):
    return (C("vrsn", U("1.0/Serato ScratchLive Smart Crate")) + C("rart", brut(1 if match_all else 0))
            + C("rlut", brut(1)) + b"".join(rules)
            + C("osrt", C("tvcn", U("bpm")) + C("brev", b"\x00"))
            + C("ovct", C("tvcn", U("artist")) + C("tvcw", U("359")))
            + C("otrk", C("ptrk", U("Users/x/Music/a.mp3"))))


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True, cwd=REPO_ROOT)


class CrateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.serato = self.tmp / "_Serato_"
        sc = self.serato / "SmartCrates"
        sc.mkdir(parents=True)
        self.target = sc / "Clean\u226b\u226bClean R&B.scrate"
        self.target.write_bytes(crate_bytes([rule("cond_con_str", 9, "r&b "), rule("cond_con_str", 9, "clean")]))
        self.other = sc / "Juke.scrate"
        self.other.write_bytes(crate_bytes([rule("cond_con_str", 9, "Juke")]))
        (self.serato / "database V2").write_bytes(b"vrsn")
        self.lock = f"TEST-FIXTURE-crate-{uuid.uuid4().hex}"
        r = run(FOLDER_LOCK, "acquire", "--folder", self.lock, "--session", "s", "--agent", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", self.lock, "--session", "s", "--force", "--reason", "teardown")
        self._tmp.cleanup()

    def new_rules(self):
        return [{"op": "cond_con_str", "field": 9, "value": "r&b"}, {"op": "cond_con_str", "field": 19, "value": "Clean"}]

    def test_parse_and_round_trip_unchanged_crate_is_byte_identical(self):
        d = self.other.read_bytes()
        p = E.parse(d)
        self.assertEqual(p["rules"], [{"op": "cond_con_str", "field": 9, "value": "Juke"}])
        self.assertTrue(p["match_all"])
        self.assertEqual(E.rebuild(d, p["rules"], p["match_all"]), d)

    def test_rebuild_changes_only_rules(self):
        d = self.target.read_bytes()
        out = E.rebuild(d, self.new_rules(), True)
        p = E.parse(out)
        self.assertEqual(p["rules"], self.new_rules())
        keep = lambda cs: [x for x in cs if x[0] not in ("rurt",)]  # noqa: E731
        self.assertEqual(keep(E.tlv(out)), keep(E.tlv(d)))

    def test_unconfirmed_field_and_operator_refused(self):
        d = self.target.read_bytes()
        with self.assertRaises(E.CrateError):
            E.rebuild(d, [{"op": "cond_con_str", "field": 7, "value": "2000"}], True)
        with self.assertRaises(E.CrateError):
            E.rebuild(d, [{"op": "cond_greq_uint", "field": 15, "value": 2000}], True)

    def test_apply_reads_back_leaves_others_and_undoes(self):
        before_t, before_o = self.target.read_bytes(), self.other.read_bytes()
        plan = self.tmp / "plan.json"
        built = run(TOOL, "--crate", self.target, "--rule", "9:cond_con_str:r&b", "--rule", "19:cond_con_str:Clean",
                    "--match", "all", "--out", plan)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertEqual(self.target.read_bytes(), before_t)
        backup = fixtures.mirror_backup(self.serato, self.tmp / "bk")
        manifest = fixtures.passing_manifest(self.serato, backup, self.tmp / "m.json")
        res = run(TOOL, "--plan", plan, "--serato-dir", self.serato, "--backup", backup, "--backup-manifest", manifest,
                  "--out-dir", self.tmp / "out", "--apply", "--lock-folder", self.lock, "--session", "s")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(E.parse(self.target.read_bytes())["rules"], self.new_rules())
        self.assertEqual(self.other.read_bytes(), before_o)
        journal = next((self.tmp / "out").glob("*recovery*.jsonl"))
        self.assertEqual(json.loads(journal.read_text().splitlines()[-1])["type"], "run_complete")
        undo = run(TOOL, "--undo", json.loads(res.stdout)["undo_log"], "--lock-folder", self.lock, "--session", "s")
        self.assertEqual(undo.returncode, 0, undo.stdout + undo.stderr)
        self.assertEqual(self.target.read_bytes(), before_t)

    def test_stale_plan_refused(self):
        plan = self.tmp / "plan.json"
        run(TOOL, "--crate", self.target, "--rule", "9:cond_con_str:r&b", "--match", "all", "--out", plan)
        self.target.write_bytes(crate_bytes([rule("cond_con_str", 9, "edited")]))
        res = run(TOOL, "--plan", plan, "--serato-dir", self.serato)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("changed since plan", res.stderr)


if __name__ == "__main__":
    unittest.main()
