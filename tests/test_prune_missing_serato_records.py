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


SUFFIX = "\U00105e8a" + "a" * 40

REAL_SCHEMA = """
CREATE TABLE asset (id INTEGER PRIMARY KEY AUTOINCREMENT, portable_id TEXT);
CREATE TABLE serato (revision INTEGER NOT NULL);
INSERT INTO serato VALUES (77);
CREATE TABLE smart_crate_rules (container_id INTEGER NOT NULL PRIMARY KEY, rules TEXT NOT NULL);
CREATE TABLE space (id INTEGER PRIMARY KEY);
CREATE TABLE container (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, name TEXT NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE space_asset (id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL, space_id INTEGER NOT NULL,
    FOREIGN KEY(asset_id) REFERENCES asset(id) ON DELETE CASCADE);
"""
ROOT_CA = """CREATE TABLE container_asset (id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
    container_id INTEGER NOT NULL, space_asset_id INTEGER NOT NULL, list_order INTEGER DEFAULT NULL,
    time_added INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE,
    FOREIGN KEY(space_asset_id) REFERENCES space_asset(id) ON DELETE CASCADE);"""
MASTER_CA = """CREATE TABLE location_container (id INTEGER PRIMARY KEY AUTOINCREMENT, container_id INTEGER NOT NULL,
    location_id INTEGER, FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE container_asset (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL,
    location_container_id INTEGER NOT NULL, space_asset_id INTEGER NOT NULL, external_container_asset_id INTEGER,
    list_order INTEGER NOT NULL, time_added INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY(location_container_id) REFERENCES location_container(id) ON DELETE CASCADE,
    FOREIGN KEY(space_asset_id) REFERENCES space_asset(id) ON DELETE CASCADE);"""


class MembershipCase(unittest.TestCase):
    """Real-shaped root (container_id) and master (location_container) membership tables."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve(); self.music = self.tmp / "Music"
        S = lambda rel: stored(self.music / rel)  # noqa: E731
        for rel in ("Afrobeat/CLEAN/Afrobeats/a.mp3", "Afrobeat/CLEAN/Afrobeats/b.mp3"):
            (self.music / rel).parent.mkdir(parents=True, exist_ok=True); (self.music / rel).write_bytes(rel.encode())
        self.live_a, self.live_b = S("Afrobeat/CLEAN/Afrobeats/a.mp3"), S("Afrobeat/CLEAN/Afrobeats/b.mp3")
        self.stale_a, self.stale_b = S("Afrobeat/CLEAN/Afrobeat/a.mp3"), S("Afrobeat/CLEAN/Afrobeat/b.mp3")
        self.gone = S("Afrobeat/gone.mp3")
        paths = [self.live_a, self.live_b, self.stale_a, self.stale_b, self.gone]
        self.serato = self.tmp / "_Serato_"; fx.make_serato_dir(self.serato, [self.live_a, self.live_b], {})
        self.lib = self.tmp / "Library"; self.lib.mkdir()
        self.root = self.lib / "root.sqlite"; self.master = self.lib / "master.sqlite"
        # containers: 1 'afro world', 2 'Afrobeat' (child of 1), 3 'Unsorted'
        # memberships: stale_a in 2,3 ; live_a in 2,3 -> removable
        #              stale_b in 2,3 ; live_b in 2 only -> held (lacks Unsorted)
        #              gone in 2 -> truly missing, held
        member = {self.stale_a: [2, 3], self.live_a: [2, 3], self.stale_b: [2, 3], self.live_b: [2], self.gone: [2]}
        for db, master in ((self.root, False), (self.master, True)):
            c = sqlite3.connect(str(db), isolation_level=None)
            c.executescript(REAL_SCHEMA + (MASTER_CA if master else ROOT_CA))
            c.execute("INSERT INTO space (id) VALUES (1)")
            for cid, parent, name in ((1, None, "afro world"), (2, 1, "Afrobeat"), (3, None, "Unsorted")):
                c.execute("INSERT INTO container (id, parent_id, name) VALUES (?,?,?)", (cid, parent, name + SUFFIX))
                if master:
                    c.execute("INSERT INTO location_container (id, container_id, location_id) VALUES (?,?,1)", (cid + 10, cid))
            for i, path in enumerate(paths, start=1):
                c.execute("INSERT INTO asset (id, portable_id) VALUES (?,?)", (i, path))
                c.execute("INSERT INTO space_asset (id, asset_id, space_id) VALUES (?,?,1)", (i, i))
                for cid in member[path]:
                    if master:
                        c.execute("INSERT INTO container_asset (asset_id, location_container_id, space_asset_id, list_order, "
                                  "time_added) VALUES (?,?,?,?,1)", (i, cid + 10, i, 100 + i))
                    else:
                        c.execute("INSERT INTO container_asset (revision, container_id, space_asset_id, list_order, time_added) "
                                  "VALUES (5,?,?,?,1)", (cid, i, 100 + i))
            c.close()
        self.dbs = [self.master, self.root]

    def plan(self, flag=True, add=False):
        return prune.build_plan(self.serato, self.music, self.dbs, "Afrobeat/", flag, add)

    def apply(self, plan):
        sm, qm = self.manifests()
        res = prune.apply_plan(plan=plan, serato_dir=self.serato, library_root=self.music, databases=self.dbs,
                               serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                               journal_path=self.tmp / "j.jsonl", result_path=self.tmp / "r.json",
                               process_lister=NO_SERATO)
        return res, sm, qm

    def make_smart(self, cid):
        for d in self.dbs:
            c = sqlite3.connect(str(d), isolation_level=None)
            c.execute("INSERT INTO smart_crate_rules VALUES (?, '{}')", (cid,)); c.close()

    def rows(self, plan, name):
        return {r["path"]: r for e in plan["sqlite"] if e["name"] == name for r in e["rows"]}

    def memberships(self, db):
        c = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            return sorted(c.execute("SELECT * FROM container_asset").fetchall())
        finally:
            c.close()

    def manifests(self):
        sb = self.tmp / "bk-serato"; shutil.copytree(self.serato, sb)
        files = [str(p.relative_to(self.serato)) for p in self.serato.rglob("*") if p.is_file()]
        sm = backup_manifest.build_scoped_manifest(self.serato, sb, files)
        qb = self.tmp / "bk-sql"; qb.mkdir()
        for d in self.dbs:
            shutil.copy2(d, qb / d.name)
        return sm, backup_manifest.build_scoped_manifest(self.lib, qb, [d.name for d in self.dbs])

    def test_flag_off_holds_every_crated_row(self):
        plan = self.plan(False)
        for name in ("root.sqlite", "master.sqlite"):
            self.assertTrue(all(r["action"] == "hold" for r in self.rows(plan, name).values()))
            self.assertEqual(plan["summary"][f"{name}_memberships_dropped"], 0)

    def test_flag_on_removes_only_fully_covered_live_twin(self):
        plan = self.plan()
        for name in ("root.sqlite", "master.sqlite"):
            rows = self.rows(plan, name)
            self.assertEqual(rows[self.stale_a]["action"], "remove")
            self.assertEqual(len(rows[self.stale_a]["drop_container_asset_ids"]), 2)
            self.assertEqual(rows[self.stale_b]["action"], "hold")
            self.assertEqual(rows[self.stale_b]["membership_check"]["lacking"], ["Unsorted"])
            self.assertEqual(rows[self.gone]["action"], "hold")          # truly missing: memberships never dropped
            self.assertNotIn("drop_container_asset_ids", rows[self.gone])
            labels = {c["container"] for c in rows[self.stale_a]["membership_check"]["containers"]}
            self.assertEqual(labels, {"afro world \u226b Afrobeat", "Unsorted"})
            self.assertEqual(plan["summary"][f"{name}_memberships_dropped"], 2)

    def test_apply_drops_only_stale_memberships_and_undo_restores_exactly(self):
        before = {d: (sha(d), self.memberships(d)) for d in self.dbs}
        plan = self.plan(); sm, qm = self.manifests()
        res = prune.apply_plan(plan=plan, serato_dir=self.serato, library_root=self.music, databases=self.dbs,
                               serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                               journal_path=self.tmp / "j.jsonl", result_path=self.tmp / "r.json",
                               process_lister=NO_SERATO)
        self.assertEqual(res["status"], "PASS")
        for d in self.dbs:
            after = self.memberships(d)
            self.assertEqual(len(after), len(before[d][1]) - 2)
            # only stale_a's (space_asset 3) rows went; every other membership is byte-identical
            self.assertEqual(after, [m for m in before[d][1] if m[3] != 3])
            c = sqlite3.connect(f"file:{d}?immutable=1", uri=True)
            self.assertEqual(c.execute("SELECT count(*) FROM container").fetchone()[0], 3)   # crates untouched
            self.assertIsNone(c.execute("SELECT 1 FROM asset WHERE portable_id=?", (self.stale_a,)).fetchone())
            for keep in (self.live_a, self.live_b, self.stale_b, self.gone):
                self.assertIsNotNone(c.execute("SELECT 1 FROM asset WHERE portable_id=?", (keep,)).fetchone())
            c.close()
        prune.undo(journal_path=self.tmp / "j.jsonl", serato_dir=self.serato, databases=self.dbs,
                   serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                   undo_journal=self.tmp / "undo", process_lister=NO_SERATO)
        self.assertEqual(before, {d: (sha(d), self.memberships(d)) for d in self.dbs})

    def test_membership_drift_invalidates_plan(self):
        plan = self.plan(); path = self.tmp / "p.json"; sr.write_json(path, plan)
        c = sqlite3.connect(str(self.root), isolation_level=None)
        c.execute("DELETE FROM container_asset WHERE container_id = 3 AND space_asset_id = 1"); c.close()
        with self.assertRaises(prune.PruneError):
            prune.load_and_validate_plan(path, self.serato, self.music, self.dbs)

    def test_two_live_twins_hold(self):
        extra = self.music / "Afrobeats" / "a.mp3"; extra.parent.mkdir(parents=True); extra.write_bytes(b"x")
        for d in self.dbs:
            c = sqlite3.connect(str(d), isolation_level=None)
            c.execute("INSERT INTO asset (portable_id) VALUES (?)", (stored(extra),)); c.close()
        rows = self.rows(self.plan(), "root.sqlite")
        self.assertEqual(rows[self.stale_a]["action"], "hold")


    def test_add_requires_drop_flag(self):
        with self.assertRaises(prune.PruneError):
            self.plan(False, True)

    def test_add_live_plans_append_at_end(self):
        plan = self.plan(add=True)
        for name in ("root.sqlite", "master.sqlite"):
            r = self.rows(plan, name)[self.stale_b]
            self.assertEqual(r["action"], "remove")
            adds = r["membership_check"]["adds"]
            self.assertEqual([a["container"] for a in adds], ["Unsorted"])
            self.assertEqual(adds[0]["values"]["list_order"], 100 + 4 + 1)    # max in Unsorted was stale_b's 104
            self.assertEqual(adds[0]["values"]["space_asset_id"], 2)          # live_b
            if name == "root.sqlite":
                self.assertEqual(adds[0]["values"]["revision"], 77)
            else:
                self.assertIsNone(adds[0]["values"]["external_container_asset_id"])
            self.assertEqual(plan["summary"][f"{name}_memberships_added"], 1)
            self.assertEqual(self.rows(plan, name)[self.gone]["action"], "hold")

    def test_add_live_apply_post_check_and_undo(self):
        before = {d: (sha(d), self.memberships(d)) for d in self.dbs}
        res, sm, qm = self.apply(self.plan(add=True))
        self.assertEqual(res["status"], "PASS")
        for d in self.dbs:
            after = self.memberships(d)
            orig_ids = {m[0] for m in before[d][1]}
            stale_ids = {m[0] for m in before[d][1] if m[3] in (3, 4)}
            # every original membership that was not a stale row's is byte-identical
            self.assertEqual([m for m in after if m[0] in orig_ids], [m for m in before[d][1] if m[0] not in stale_ids])
            new = [m for m in after if m[0] not in orig_ids]
            self.assertEqual(len(new), 1)
            c = sqlite3.connect(f"file:{d}?immutable=1", uri=True)
            in_unsorted = c.execute(
                "SELECT count(*) FROM container_asset WHERE space_asset_id = 2 AND " +
                ("location_container_id = 13" if d == self.master else "container_id = 3")).fetchone()[0]
            self.assertEqual(in_unsorted, 1)
            for gone in (self.stale_a, self.stale_b):
                self.assertIsNone(c.execute("SELECT 1 FROM asset WHERE portable_id=?", (gone,)).fetchone())
            self.assertEqual(c.execute("SELECT count(*) FROM container").fetchone()[0], 3)
            c.close()
        prune.undo(journal_path=self.tmp / "j.jsonl", serato_dir=self.serato, databases=self.dbs,
                   serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                   undo_journal=self.tmp / "undo", process_lister=NO_SERATO)
        self.assertEqual(before, {d: (sha(d), self.memberships(d)) for d in self.dbs})

    def test_smart_crate_is_reported_never_inserted(self):
        self.make_smart(3)
        plan = self.plan(add=True)
        for name in ("root.sqlite", "master.sqlite"):
            r = self.rows(plan, name)[self.stale_b]
            self.assertEqual(r["action"], "hold")
            self.assertEqual(r["membership_check"]["smart_lacking"], ["Unsorted"])
            self.assertEqual(r["membership_check"]["adds"], [])
            self.assertEqual(plan["summary"][f"{name}_memberships_added"], 0)

    def test_live_copy_added_after_plan_invalidates_it(self):
        plan = self.plan(add=True); path = self.tmp / "p.json"; sr.write_json(path, plan)
        c = sqlite3.connect(str(self.root), isolation_level=None)
        c.execute("INSERT INTO container_asset (revision, container_id, space_asset_id, list_order) VALUES (5,3,2,999)"); c.close()
        with self.assertRaises(prune.PruneError):
            prune.load_and_validate_plan(path, self.serato, self.music, self.dbs)


if __name__ == "__main__":
    unittest.main()
