"""apply_hidden_grouping_label_plan.py: watermark GRP1 -> Label, stray GRP1 drop.
Synthetic fixtures only."""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from mutagen.id3 import ID3, GRP1, TPUB  # noqa: E402

import fixtures  # noqa: E402
import apply_hidden_grouping_label_plan as tool  # noqa: E402
import apply_grouping_plan  # noqa: E402
import mutation_engine  # noqa: E402

TOOL = SCRIPTS_DIR / "apply_hidden_grouping_label_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
LOCK_FOLDER = "test-fixture-hidden-grouping"
SESSION = "hidden-grouping-test"
VIRDIKO = "VirDiKO Global Music Promotion....Serving The World's Best DJ's!"
BPMS = "www.bpmsupreme.com"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=REPO_ROOT)


def set_frames(path, grp1=None, label=None):
    tags = ID3(path)
    version = tags.version[1]
    if grp1 is not None:
        tags.add(GRP1(encoding=1, text=[grp1]))
    if label is not None:
        tags.add(TPUB(encoding=1, text=[label]))
    tags.save(path, v2_version=version)


class DeriveTests(unittest.TestCase):
    def state(self, grp1, label=()):
        return {"grp1": list(grp1), "label": list(label), "tit1": [], "id3_version": 4}

    def test_watermark_moves_to_empty_label(self):
        r = tool.derive(self.state([VIRDIKO]), [VIRDIKO], ["November"])
        self.assertEqual((r["new_grp1"], r["new_label"], r["refused"]), ([], VIRDIKO, []))

    def test_watermark_appends_after_existing_label(self):
        r = tool.derive(self.state([BPMS], ["Def Jam"]), [BPMS], [])
        self.assertEqual(r["new_label"], "Def Jam; " + BPMS)

    def test_watermark_not_duplicated_in_label(self):
        r = tool.derive(self.state([BPMS], ["WWW.BPMSUPREME.COM"]), [BPMS], [])
        self.assertEqual(r["new_label"], "WWW.BPMSUPREME.COM")

    def test_drop_value_is_removed_and_label_untouched(self):
        r = tool.derive(self.state(["November"], ["RCA"]), [VIRDIKO], ["November"])
        self.assertEqual((r["new_grp1"], r["new_label"], r["dropped"]), ([], "RCA", ["November"]))

    def test_unapproved_value_is_refused_and_kept(self):
        r = tool.derive(self.state(["Source: 2025"]), [VIRDIKO], ["November"])
        self.assertEqual(r["refused"], ["Source: 2025"])
        self.assertEqual(r["new_grp1"], ["Source: 2025"])

    def test_engine_knows_the_mutation_class(self):
        self.assertIn(mutation_engine.HIDDEN_GROUPING_LABEL, mutation_engine.MUTATION_CLASSES)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Hip Hop"
        self.made = fixtures.make_library(self.root, [
            {"relative": "a.mp3", "grouping": "Dirty", "serato": True},
            {"relative": "b.mp3", "grouping": "Clean", "v2_version": 3},
            {"relative": "c.mp3", "grouping": "Dirty"},
            {"relative": "d.mp3", "grouping": "Dirty"},
        ])
        set_frames(self.made["a.mp3"], grp1=VIRDIKO)
        set_frames(self.made["b.mp3"], grp1=BPMS, label="Atlantic")
        set_frames(self.made["c.mp3"], grp1="November", label="RCA")
        set_frames(self.made["d.mp3"], grp1="Source: keep me")
        self.backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        self.manifest = fixtures.passing_manifest(self.root, self.backup, self.tmp / "manifest.json")
        self.out = self.tmp / "out"
        acquire = run(FOLDER_LOCK, "acquire", "--folder", LOCK_FOLDER, "--session", SESSION, "--agent", "test")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", LOCK_FOLDER, "--session", SESSION, "--force",
            "--reason", "test teardown")
        self._tmp.cleanup()

    def build(self):
        paths = self.tmp / "paths.txt"
        paths.write_text("\n".join(str(p) for p in self.made.values()) + "\n", encoding="utf-8")
        built = run(TOOL, "--build", "--paths", paths, "--out-dir", self.out,
                    "--watermark", VIRDIKO, "--watermark", BPMS, "--drop", "November")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return self.out / tool.PLAN_NAME

    def test_build_is_read_only_and_refuses_unapproved(self):
        before = {p: p.read_bytes() for p in self.made.values()}
        plan = json.loads(self.build().read_text(encoding="utf-8"))
        self.assertEqual(len(plan["items"]), 3)
        self.assertTrue(any("REFUSED" in s["reason"] for s in plan["skipped"]))
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data)
        with (self.out / tool.PREVIEW_NAME).open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)

    def test_apply_verifies_preserves_payloads_and_undoes(self):
        plan = self.build()
        a, b, c, d = (self.made[k] for k in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"))
        before = {p: p.read_bytes() for p in (a, b, c, d)}
        audio = apply_grouping_plan.audio_payload(a)
        serato = apply_grouping_plan.serato_digest(ID3(a))

        dry = run(TOOL, "--root", self.root, "--plan", plan)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(a.read_bytes(), before[a])

        applied = run(TOOL, "--root", self.root, "--plan", plan, "--backup", self.backup,
                      "--backup-manifest", self.manifest, "--apply",
                      "--lock-folder", LOCK_FOLDER, "--session", SESSION, "--label", "t")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        result = json.loads(applied.stdout)
        self.assertEqual(result["changed"], 3)

        self.assertEqual(tool.read_state(a), {"id3_version": 4, "grp1": [], "label": [VIRDIKO], "tit1": ["Dirty"]})
        self.assertEqual(tool.read_state(b), {"id3_version": 3, "grp1": [], "label": ["Atlantic; " + BPMS],
                                              "tit1": ["Clean"]})
        self.assertEqual(tool.read_state(c)["grp1"], [])
        self.assertEqual(tool.read_state(c)["label"], ["RCA"])
        self.assertEqual(d.read_bytes(), before[d])
        self.assertEqual(apply_grouping_plan.audio_payload(a), audio)
        self.assertEqual(apply_grouping_plan.serato_digest(ID3(a)), serato)

        journal = next(self.out.glob("t-recovery-*.jsonl"))
        records = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(records[-1]["type"], "run_complete")

        undone = run(TOOL, "--undo", result["undo_log"], "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data)

    def test_apply_refuses_when_tags_changed_since_preview(self):
        plan = self.build()
        a = self.made["a.mp3"]
        set_frames(a, label="Someone edited this")
        before = a.read_bytes()
        applied = run(TOOL, "--root", self.root, "--plan", plan, "--backup", self.backup,
                      "--backup-manifest", self.manifest, "--apply",
                      "--lock-folder", LOCK_FOLDER, "--session", SESSION, "--label", "t")
        self.assertNotEqual(applied.returncode, 0)
        self.assertIn("changed since preview", applied.stderr)
        self.assertEqual(a.read_bytes(), before)

    def test_apply_requires_lock(self):
        plan = self.build()
        applied = run(TOOL, "--root", self.root, "--plan", plan, "--backup", self.backup,
                      "--backup-manifest", self.manifest, "--apply",
                      "--lock-folder", LOCK_FOLDER, "--session", "not-the-holder")
        self.assertNotEqual(applied.returncode, 0)


if __name__ == "__main__":
    unittest.main()
