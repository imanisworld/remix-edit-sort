import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import backup_manifest  # noqa: E402
import mutation_engine  # noqa: E402
import prune_missing_serato_records as prune  # noqa: E402
import rewrite_serato_paths as sr  # noqa: E402
import serato_sqlite_fixtures as fx  # noqa: E402

NO_SERATO = lambda: ["/bin/zsh"]  # noqa: E731
RUNNING = lambda: ["/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro"]  # noqa: E731


class FakeHeartbeat:
    def beat(self, force=False):
        return {}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def stored(p):
    return str(p).lstrip("/")


class PruneCase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve(); self.music = self.tmp / "Music"
        live = ["Afrobeat/CLEAN/Afrobeats/a.mp3", "Afrobeat/b.mp3", "Hip Hop/h.mp3", "Pop/moved.mp3"]
        for rel in live:
            (self.music / rel).parent.mkdir(parents=True, exist_ok=True); (self.music / rel).write_bytes(rel.encode())
        S = lambda rel: stored(self.music / rel)  # noqa: E731
        self.twin = S("Afrobeat/CLEAN/Afrobeat/a.mp3")      # stale duplicate of a live record
        self.gone = S("Afrobeat/deleted.mp3")              # truly missing
        self.moved = S("Afrobeat/moved.mp3")               # same name exists elsewhere, no live record -> hold
        self.crated = S("Afrobeat/crated.mp3")             # truly missing but still in a crate -> hold
        self.outside = S("Hip Hop/old.mp3")                # missing but outside --prefix
        paths = [S("Afrobeat/CLEAN/Afrobeats/a.mp3"), S("Afrobeat/b.mp3"), S("Hip Hop/h.mp3"),
                 self.twin, self.gone, self.moved, self.crated, self.outside]
        self.serato = self.tmp / "_Serato_"
        fx.make_serato_dir(self.serato, paths, {"Mix": [S("Afrobeat/b.mp3"), self.crated]})
        (self.serato / "History").mkdir(); (self.serato / "History" / "s.session").write_bytes(b"hist " + self.gone.encode())
        self.lib = self.tmp / "Library"; self.lib.mkdir()
        self.master = fx.make_master_sqlite(self.lib / "master.sqlite", paths, history=[self.gone])
        self.root = fx.make_root_sqlite(self.lib / "root.sqlite", paths)
        # fixture gives every master asset a container row; stale ones are not in containers here,
        # except the crated one
        c = sqlite3.connect(str(self.master), isolation_level=None)
        c.execute("DELETE FROM container_asset WHERE asset_id IN (SELECT id FROM asset WHERE portable_id IN (?,?,?,?))",
                  (self.twin, self.gone, self.moved, self.outside))
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close()
        self.dbs = [self.master, self.root]

    def plan(self, prefix="Afrobeat/"):
        return prune.build_plan(self.serato, self.music, self.dbs, prefix)

    def manifests(self):
        sb = self.tmp / "bk-serato"; shutil.copytree(self.serato, sb)
        files = [str(p.relative_to(self.serato)) for p in self.serato.rglob("*") if p.is_file()]
        sm = backup_manifest.build_scoped_manifest(self.serato, sb, files)
        qb = self.tmp / "bk-sql"; qb.mkdir()
        for d in self.dbs:
            shutil.copy2(d, qb / d.name)
        qm = backup_manifest.build_scoped_manifest(self.lib, qb, [d.name for d in self.dbs])
        self.assertEqual(sm["status"], "PASS"); self.assertEqual(qm["status"], "PASS")
        return sm, qm

    def apply(self, plan, sm, qm, lister=NO_SERATO):
        return prune.apply_plan(plan=plan, serato_dir=self.serato, library_root=self.music, databases=self.dbs,
                                serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                                journal_path=self.tmp / "j.jsonl", result_path=self.tmp / "r.json",
                                process_lister=lister)

    def dbv2_paths(self):
        return [p for _, p in prune._dbv2_records((self.serato / "database V2").read_bytes()) if p]

    def assets(self, db):
        c = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            return [r[0] for r in c.execute("SELECT portable_id FROM asset")]
        finally:
            c.close()


class DryRunTests(PruneCase):
    def test_classifies_and_respects_prefix(self):
        before = {p: sha(p) for p in [self.serato / "database V2", *self.dbs]}
        plan = self.plan()
        recs = {r["path"]: r for r in plan["database_v2"]["records"]}
        self.assertEqual(recs[self.twin]["class"], "live_twin"); self.assertEqual(recs[self.twin]["action"], "remove")
        self.assertEqual(recs[self.gone]["class"], "truly_missing"); self.assertEqual(recs[self.gone]["action"], "remove")
        self.assertEqual(recs[self.moved]["action"], "hold")
        self.assertEqual(recs[self.crated]["action"], "hold"); self.assertEqual(recs[self.crated]["crate_refs"], 1)
        self.assertNotIn(self.outside, recs)
        self.assertEqual(plan["summary"]["database_v2_remove"], 2)
        self.assertEqual(plan["summary"]["root.sqlite_remove"], 3)   # root has no containers: crated is removable
        self.assertEqual(plan["summary"]["master.sqlite_remove"], 2)  # crated is still in a container -> hold
        self.assertEqual(before, {p: sha(p) for p in before})          # dry run changes nothing

    def test_without_prefix_scope_is_whole_library(self):
        self.assertIn(self.outside, {r["path"] for r in self.plan(None)["database_v2"]["records"]})

    def test_bad_prefix_refused(self):
        with self.assertRaises(prune.PruneError):
            self.plan("../x")


class ApplyTests(PruneCase):
    def test_apply_removes_only_planned_and_post_check_passes(self):
        plan = self.plan(); sm, qm = self.manifests()
        crate_before = sha(self.serato / "Subcrates" / "Mix.crate")
        hist_before = sha(self.serato / "History" / "s.session")
        result = self.apply(plan, sm, qm)
        self.assertEqual(result["status"], "PASS"); self.assertEqual(result["post_check"]["removable_remaining"], 0)
        paths = self.dbv2_paths()
        self.assertNotIn(self.twin, paths); self.assertNotIn(self.gone, paths)
        for keep in (self.moved, self.crated, self.outside):
            self.assertIn(keep, paths)
        self.assertEqual(len(paths), 6)
        self.assertNotIn(self.gone, self.assets(self.master)); self.assertIn(self.crated, self.assets(self.master))
        self.assertNotIn(self.crated, self.assets(self.root))
        self.assertEqual(sha(self.serato / "Subcrates" / "Mix.crate"), crate_before)
        self.assertEqual(sha(self.serato / "History" / "s.session"), hist_before)
        c = sqlite3.connect(f"file:{self.master}?immutable=1", uri=True)
        self.assertEqual(c.execute("SELECT portable_id FROM history_entry").fetchall(), [(self.gone,)])
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchall(), [("ok",)]); c.close()
        self.assertTrue(mutation_engine.recover(self.tmp / "j.jsonl")["clean"])

    def test_running_serato_refused_and_nothing_changes(self):
        plan = self.plan(); sm, qm = self.manifests()
        before = {p: sha(p) for p in [self.serato / "database V2", *self.dbs]}
        with self.assertRaises(sr.SeratoRewriteError):
            self.apply(plan, sm, qm, lister=RUNNING)
        self.assertEqual(before, {p: sha(p) for p in before})

    def test_plan_bound_to_bytes(self):
        plan = self.plan(); path = self.tmp / "p.json"; sr.write_json(path, plan)
        (self.serato / "database V2").write_bytes((self.serato / "database V2").read_bytes() + fx.otrk(fx.tlv("pfil", fx.text("x/y.mp3"))))
        with self.assertRaises(prune.PruneError):
            prune.load_and_validate_plan(path, self.serato, self.music, self.dbs)

    def test_undo_restores_every_file(self):
        before = {p: sha(p) for p in [self.serato / "database V2", *self.dbs]}
        plan = self.plan(); sm, qm = self.manifests(); self.apply(plan, sm, qm)
        self.assertNotEqual(before, {p: sha(p) for p in before})
        res = prune.undo(journal_path=self.tmp / "j.jsonl", serato_dir=self.serato, databases=self.dbs,
                         serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                         undo_journal=self.tmp / "undo", process_lister=NO_SERATO)
        self.assertEqual(len(res["restored"]), 3)
        self.assertEqual(before, {p: sha(p) for p in before})


if __name__ == "__main__":
    unittest.main()
