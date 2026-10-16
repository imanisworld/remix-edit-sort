import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_smart_crate_plan as sc  # noqa: E402
import backup_manifest  # noqa: E402
import mutation_engine  # noqa: E402
import rewrite_serato_paths as sr  # noqa: E402

NO_SERATO = lambda: ["/bin/zsh"]  # noqa: E731
RUNNING = lambda: ["/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro"]  # noqa: E731
SUF = "\U00105e8a542e62afae2b4961812816cd5bd160d7"
SEP = sc.SEP


class FakeHeartbeat:
    def beat(self, force=False):
        return {}


COMMON = """
CREATE TABLE container_asset_list_columns (container_id INTEGER, usage INTEGER, column_names TEXT NOT NULL,
  column_widths TEXT NOT NULL, primary_sort_column TEXT, primary_sort_direction INTEGER NOT NULL, migrated_by INTEGER,
  PRIMARY KEY(container_id, usage), FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
"""
MASTER = """
CREATE TABLE container (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER DEFAULT NULL, name TEXT NOT NULL,
  type INTEGER DEFAULT 1, list_order INTEGER NOT NULL, space_id INTEGER DEFAULT NULL, time_added INTEGER NOT NULL DEFAULT 0,
  expanded INTEGER NOT NULL DEFAULT 0, portable_id TEXT NOT NULL DEFAULT '', color INTEGER,
  UNIQUE(parent_id, name COLLATE NOCASE, type), FOREIGN KEY(parent_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE location_container (id INTEGER PRIMARY KEY, container_id INTEGER NOT NULL, location_id INTEGER NOT NULL,
  external_container_id INTEGER, UNIQUE(container_id, location_id),
  FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE container_asset (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL, location_container_id INTEGER NOT NULL,
  space_asset_id INTEGER NOT NULL, external_container_asset_id INTEGER, list_order INTEGER NOT NULL, time_added INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(location_container_id) REFERENCES location_container(id) ON DELETE CASCADE);
CREATE TABLE smart_crate_rules (container_id INTEGER NOT NULL PRIMARY KEY, version INTEGER NOT NULL, rules TEXT NOT NULL,
  FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE dj_container_metadata (container_id INTEGER PRIMARY KEY, export_state INTEGER NOT NULL DEFAULT 0, filename TEXT,
  FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE container_keep_alive_list_tbl (container_id INTEGER NOT NULL, location_id INTEGER NOT NULL,
  PRIMARY KEY(container_id, location_id), FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE updated_container (container_id INTEGER);
CREATE TRIGGER t_upd AFTER UPDATE OF name ON container BEGIN INSERT INTO updated_container VALUES (new.id); END;
""" + COMMON
ROOTDB = """
CREATE TABLE serato (database_name TEXT, revision INTEGER);
CREATE TABLE container (id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL, parent_id INTEGER DEFAULT NULL,
  name TEXT NOT NULL, type INTEGER DEFAULT 1, list_order INTEGER NOT NULL, space_id INTEGER DEFAULT NULL,
  time_added INTEGER NOT NULL DEFAULT 0, expanded INTEGER NOT NULL DEFAULT 0, portable_id TEXT NOT NULL DEFAULT '', color INTEGER,
  UNIQUE(parent_id, name COLLATE NOCASE, type), FOREIGN KEY(parent_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE container_asset (id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL, container_id INTEGER NOT NULL,
  space_asset_id INTEGER NOT NULL, list_order INTEGER, time_added INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE smart_crate_rules (container_id INTEGER NOT NULL PRIMARY KEY, revision INTEGER NOT NULL, version INTEGER NOT NULL,
  rules TEXT NOT NULL, needs_refresh INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
CREATE TABLE dj_container_metadata (container_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, export_state INTEGER NOT NULL DEFAULT 0,
  filename TEXT, FOREIGN KEY(container_id) REFERENCES container(id) ON DELETE CASCADE);
""" + COMMON


def rules_json(conj, pairs):
    return json.dumps({"spaces": ["Serato Library", "iTunes"], "conjunction": conj, "live_updates": True,
                       "rules": [{"type": "STRING", "attribute": a, "operation": "CONTAINS", "version": 1, "value": v}
                                 for a, v in pairs]}, separators=(",", ":"))


def scrate(conj, pairs, tracks=()):
    base = (sr.encode_tlv("vrsn", "1.0/Serato ScratchLive Smart Crate".encode("utf-16-be"))
            + sr.encode_tlv("rart", b"brut\x00\x00\x00\x01\x01") + sr.encode_tlv("rlut", b"brut\x00\x00\x00\x01\x01")
            + sr.encode_tlv("rurt", b"") + sr.encode_tlv("osrt", b"x"))
    data = sc.encode_scrate(base, conj, [{"attribute": a, "value": v} for a, v in pairs])
    for t in tracks:
        data += sr.encode_tlv("otrk", sr.encode_tlv("ptrk", (t + "\x00").encode("utf-16-be")))
    return data


# path -> (kind, conj, rules)  kind: folder | smart
CRATES = {
    ("Twerk",): ("smart", "OR", [("genre", "twerk "), ("grouping", "twerk")]),
    ("Clean",): ("folder", None, None),
    ("Clean", "Clean R&B"): ("smart", "AND", [("genre", "r&b "), ("genre", "clean")]),
    ("Clean", "Afrobeat Clean"): ("smart", "AND", [("genre", "Afrobeat"), ("grouping", "Clean")]),
    ("Crate 3",): ("folder", None, None),
    ("Crate 3", "Smart Crate 1"): ("smart", "AND", [("genre", "house")]),
    ("Underground HOUSE / DANCE",): ("smart", "AND", [("genre", "Plex")]),
}


def fname(parts):
    return SEP.join(p.replace("/", "␛␛2f") for p in parts) + ".scrate"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.serato = self.tmp / "_Serato_"; (self.serato / "SmartCrates").mkdir(parents=True)
        (self.serato / "database V2").write_bytes(b"vrsn-dbv2")
        for parts, (kind, conj, rules) in CRATES.items():
            if kind == "smart":
                (self.serato / "SmartCrates" / fname(parts)).write_bytes(scrate(conj, rules, ["Users/x/a.mp3"]))
        self.lib = self.tmp / "Library"; self.lib.mkdir()
        self.root, self.master = self.lib / "root.sqlite", self.lib / "master.sqlite"
        self._build_dbs()
        self.dbs = [self.master, self.root]
        self.retire = self.tmp / "retired"
        self.preview = self._preview()

    def _build_dbs(self):
        r = sqlite3.connect(str(self.root), isolation_level=None); r.executescript(ROOTDB)
        m = sqlite3.connect(str(self.master), isolation_level=None); m.executescript(MASTER)
        r.execute("INSERT INTO serato VALUES ('', 100)")
        r.execute("INSERT INTO container (id, revision, parent_id, name, type, list_order, space_id) VALUES (74,1,0,'Serato Library root',0,6,6)")
        m.execute("INSERT INTO container (id, parent_id, name, type, list_order, space_id) VALUES (8,0,'Serato Library root',0,8,8)")
        rid, mid = {(): 74}, {(): 8}
        order = 1
        for parts, (kind, conj, rules) in CRATES.items():
            name = parts[-1] + (SUF if kind == "smart" else "")
            typ = 2 if kind == "smart" else 1
            rc = r.execute("INSERT INTO container (revision,parent_id,name,type,list_order,space_id) VALUES (50,?,?,?,?,6)",
                           (rid[parts[:-1]], name, typ, order)).lastrowid
            mc = m.execute("INSERT INTO container (parent_id,name,type,list_order,space_id) VALUES (?,?,?,?,8)",
                           (mid[parts[:-1]], name, typ, order)).lastrowid
            rid[parts], mid[parts] = rc, mc; order += 1
            for loc, ext in ((1, None), (3, rc), (7, None)):
                m.execute("INSERT INTO location_container (container_id, location_id, external_container_id) VALUES (?,?,?)", (mc, loc, ext))
            m.execute("INSERT INTO container_keep_alive_list_tbl VALUES (?,3)", (mc,))
            for db, c in ((r, rc), (m, mc)):
                db.execute("INSERT INTO container_asset_list_columns VALUES (?,0,'song','100',NULL,0,NULL)", (c,))
            if kind == "smart":
                r.execute("INSERT INTO smart_crate_rules VALUES (?,50,1,?,0)", (rc, rules_json(conj, rules)))
                m.execute("INSERT INTO smart_crate_rules VALUES (?,1,?)", (mc, rules_json(conj, rules)))
                r.execute("INSERT INTO dj_container_metadata VALUES (?,50,0,?)", (rc, fname(parts)))
                m.execute("INSERT INTO dj_container_metadata VALUES (?,0,?)", (mc, fname(parts)))
                r.execute("INSERT INTO container_asset (revision, container_id, space_asset_id, list_order) VALUES (50,?,1,1)", (rc,))
                lc = m.execute("SELECT id FROM location_container WHERE container_id=? AND location_id=3", (mc,)).fetchone()[0]
                m.execute("INSERT INTO container_asset (asset_id, location_container_id, space_asset_id, list_order) VALUES (1,?,1,1)", (lc,))
        r.close(); m.close()

    def _preview(self):
        p = self.tmp / "preview"; st = p / "staged" / "SmartCrates"; st.mkdir(parents=True)
        items = []

        def stage(action, target, conj, rules, source=None):
            data = scrate(conj, rules); (st / Path(target).name).write_bytes(data)
            it = {"action": action, "target": target, "staged": f"staged/{target}", "staged_sha256": sc.sha(data)}
            if source:
                it.update(source=source, source_sha256=sr.sha256_file(self.serato / source))
            items.append(it)
        S = lambda parts: "SmartCrates/" + fname(parts)  # noqa: E731
        stage("fix", S(("Clean", "Clean R&B")), "AND", [("genre", "R&B"), ("grouping", "Clean")], S(("Clean", "Clean R&B")))
        stage("fix", S(("Twerk",)), "OR", [("genre", "twerk"), ("grouping", "twerk")], S(("Twerk",)))
        stage("rename", S(("Clean", "Afrobeats Clean")), "AND", [("genre", "Afrobeats"), ("grouping", "Clean")], S(("Clean", "Afrobeat Clean")))
        stage("create", S(("Energy", "Peak")), "AND", [("grouping", "Energy: Peak")])
        stage("create", S(("Energy", "Peak Clean")), "AND", [("grouping", "Energy: Peak"), ("grouping", "Clean")])
        for parts in (("Underground HOUSE / DANCE",), ("Crate 3", "Smart Crate 1")):
            items.append({"action": "retire", "source": S(parts), "source_sha256": sr.sha256_file(self.serato / S(parts))})
        (p / "apply-plan.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        return p

    def plan(self):
        return sc.build_plan(self.preview, self.serato, self.dbs, self.retire)

    def manifests(self):
        self._n = getattr(self, "_n", 0) + 1
        sb = self.tmp / f"bk-serato-{self._n}"; shutil.copytree(self.serato, sb)
        files = [str(p.relative_to(self.serato)) for p in self.serato.rglob("*") if p.is_file()]
        sm = backup_manifest.build_scoped_manifest(self.serato, sb, files)
        qb = self.tmp / f"bk-sql-{self._n}"; qb.mkdir()
        for d in self.dbs:
            shutil.copy2(d, qb / d.name)
        qm = backup_manifest.build_scoped_manifest(self.lib, qb, [d.name for d in self.dbs])
        self.assertEqual(sm["status"], "PASS"); self.assertEqual(qm["status"], "PASS")
        return sm, qm

    def apply(self, plan, **kw):
        for f in ("j.jsonl", "j.jsonl.sqlite.jsonl"):
            (self.tmp / f).unlink(missing_ok=True)
        sm, qm = self.manifests()
        return sc.apply_plan(plan=plan, serato_dir=self.serato, databases=self.dbs, serato_manifest=sm, sqlite_manifest=qm,
                             heartbeat=FakeHeartbeat(), journal_path=self.tmp / "j.jsonl", result_path=self.tmp / "r.json",
                             process_lister=kw.pop("lister", NO_SERATO), **kw)

    def undo(self):
        sm, qm = self.manifests()
        return sc.undo(journal_path=self.tmp / "j.jsonl", result_path=self.tmp / "r.json", serato_dir=self.serato,
                       databases=self.dbs, serato_manifest=sm, sqlite_manifest=qm, heartbeat=FakeHeartbeat(),
                       undo_journal=self.tmp / "u.jsonl", process_lister=NO_SERATO)

    def dump(self):
        out = {}
        for d in self.dbs:
            c = sqlite3.connect(f"file:{d}?immutable=1", uri=True)
            # Serato's change-notification queues (updated_*/moved_*) are filled by its triggers by design.
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence','serato') "
                                  "AND name NOT LIKE 'updated%' AND name NOT LIKE 'moved%'"):
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")') if r[1] not in ("revision", "needs_refresh", "time_added")]
                out[(d.name, t)] = sorted(map(repr, c.execute(f'SELECT {",".join(chr(34)+x+chr(34) for x in cols)} FROM "{t}"')))
            c.close()
        files = {str(p.relative_to(self.serato)): sc.sha(p.read_bytes()) for p in self.serato.rglob("*") if p.is_file()}
        return out, files

    def find(self, db, parts):
        c = sc.sq.open_immutable(db)
        try:
            return sc._resolve(c, parts), c.execute("SELECT * FROM smart_crate_rules").fetchall()
        finally:
            c.close()


class DryRunTests(Fixture):
    def test_plan_ready_and_counts(self):
        p = self.plan()
        self.assertEqual(p["status"], "READY", p["problems"])
        self.assertEqual(p["summary"]["fix"], 2); self.assertEqual(p["summary"]["create"], 2); self.assertEqual(p["summary"]["retire"], 2)
        for e in p["sqlite"]:
            kinds = [o["op"] for o in e["ops"]]
            self.assertEqual(kinds.count("create_folder"), 1)  # Energy folder once
        self.assertEqual([e["name"] for e in p["sqlite"]], ["root.sqlite", "master.sqlite"])

    def test_refuses_source_drift(self):
        f = self.serato / "SmartCrates" / fname(("Twerk",)); f.write_bytes(f.read_bytes() + sr.encode_tlv("otrk", b""))
        p = self.plan()
        self.assertEqual(p["status"], "REFUSED"); self.assertTrue(any("changed since the preview" in x for x in p["problems"]))

    def test_refuses_existing_create_target(self):
        (self.serato / "SmartCrates" / fname(("Energy", "Peak"))).write_bytes(b"x")
        self.assertEqual(self.plan()["status"], "REFUSED")

    def test_refuses_absolute_and_traversal_paths(self):
        p = self.preview / "apply-plan.json"
        original = json.loads(p.read_text(encoding="utf-8"))
        for dangerous in ("/tmp/outside.scrate", "../outside.scrate",
                          "SmartCrates/../outside.scrate", "other/else.scrate"):
            with self.subTest(path=dangerous):
                spec = json.loads(json.dumps(original))
                spec["items"][3]["target"] = dangerous  # create does not require a source
                p.write_text(json.dumps(spec), encoding="utf-8")
                with self.assertRaises(sc.SmartCrateError):
                    self.plan()
        p.write_text(json.dumps(original), encoding="utf-8")

    def test_refuses_symlinked_crate_or_staging_path(self):
        source = self.serato / "SmartCrates" / fname(("Twerk",))
        source.unlink()
        external = self.tmp / "external.scrate"
        external.write_bytes(b"private")
        source.symlink_to(external)
        with self.assertRaises(sc.SmartCrateError):
            self.plan()
        source.unlink()
        source.write_bytes(scrate("OR", [("genre", "twerk")]))
        stage = self.preview / "staged" / "SmartCrates" / fname(("Energy", "Peak"))
        stage.unlink()
        stage.symlink_to(external)
        with self.assertRaises(sc.SmartCrateError):
            self.plan()

    def test_stale_plan_rejected(self):
        p = self.plan(); pp = self.tmp / "plan.json"; sr.write_json(pp, p)
        c = sqlite3.connect(str(self.root), isolation_level=None)
        c.execute("UPDATE smart_crate_rules SET rules = rules || ' ' WHERE container_id = (SELECT min(container_id) FROM smart_crate_rules)")
        c.close()
        with self.assertRaises(sc.SmartCrateError):
            sc.load_and_validate_plan(pp, self.dbs, self.serato)

    def test_decode_roundtrip(self):
        data = scrate("AND", [("genre", "R&B"), ("grouping", "Clean")])
        self.assertEqual(sc.decode_rules(data), {"conjunction": "AND", "rules": [{"attribute": "genre", "value": "R&B"},
                                                                                  {"attribute": "grouping", "value": "Clean"}]})


class ApplyTests(Fixture):
    def test_refuses_when_serato_running(self):
        with self.assertRaises(sr.SeratoRewriteError):
            self.apply(self.plan(), lister=RUNNING)

    def test_apply_both_stores_and_undo(self):
        before = self.dump()
        res = self.apply(self.plan())
        self.assertEqual(res["status"], "PASS", res["check"])
        sm = self.serato / "SmartCrates"
        self.assertFalse((sm / fname(("Underground HOUSE / DANCE",))).exists())
        self.assertTrue((self.retire / fname(("Underground HOUSE / DANCE",))).is_file())  # moved, not deleted
        self.assertTrue((self.retire / fname(("Clean", "Afrobeat Clean"))).is_file())
        self.assertTrue((sm / fname(("Energy", "Peak Clean"))).is_file())
        for db in self.dbs:
            self.assertIsNone(self.find(db, ("Crate 3", "Smart Crate 1"))[0])
            self.assertIsNotNone(self.find(db, ("Energy", "Peak"))[0])
            self.assertIsNotNone(self.find(db, ("Clean", "Afrobeats Clean"))[0])
            c = sqlite3.connect(str(db))
            self.assertEqual([r for r in c.execute("PRAGMA foreign_key_check") if r[1] not in (8, 74)], [])
            if db.name == "master.sqlite":
                new = self.find(db, ("Energy", "Peak"))[0]
                ext = c.execute("SELECT external_container_id FROM location_container WHERE container_id=? AND location_id=3", (new,)).fetchone()[0]
                self.assertEqual(ext, self.find(self.root, ("Energy", "Peak"))[0])  # mirrors the new root row
                self.assertEqual(c.execute("SELECT filename FROM dj_container_metadata WHERE container_id=?", (new,)).fetchone()[0],
                                 fname(("Energy", "Peak")))
            else:
                self.assertEqual(c.execute("SELECT revision FROM serato").fetchone()[0], 101)
            c.close()
        self.undo()
        after = self.dump()
        self.assertEqual(before[1], {k: v for k, v in after[1].items()})
        for k in before[0]:
            self.assertEqual(before[0][k], after[0][k], k)

    def test_failure_rolls_back_everything(self):
        before = self.dump()
        for step in ("master.sqlite", "1:fix", "6:retire", "create"):
            with self.subTest(step=step):
                with self.assertRaises(mutation_engine.MutationVerificationError):
                    self.apply(self.plan(), fail_after=step)
                self.assertEqual(self.dump(), before)
                for p in self.serato.rglob("*.rolled-back"):
                    p.unlink()  # test cleanup of parked files only

    def test_failed_final_postcheck_restores_both_stores(self):
        before = self.dump()
        with patch.object(sc, "verify_applied", return_value={"status": "FAIL", "problems": ["injected post-check failure"]}):
            with self.assertRaises(mutation_engine.MutationVerificationError):
                self.apply(self.plan())
        self.assertEqual(self.dump(), before)
        rollback = json.loads((self.tmp / "j.jsonl.rollback.json").read_text())
        self.assertEqual(rollback["status"], "ROLLED_BACK")
        self.assertEqual(rollback["errors"], [])
        self.assertFalse((self.tmp / "r.json").exists())

    def test_exception_during_final_postcheck_rolls_back(self):
        before = self.dump()
        with patch.object(sc, "verify_applied", side_effect=RuntimeError("injected verifier exception")):
            with self.assertRaisesRegex(RuntimeError, "injected verifier exception"):
                self.apply(self.plan())
        self.assertEqual(self.dump(), before)
        record = json.loads((self.tmp / "j.jsonl.rollback.json").read_text())
        self.assertEqual(record["status"], "ROLLED_BACK")
        self.assertEqual(record["errors"], [])

    def test_failed_postcheck_rollback_error_is_partial(self):
        plan = self.plan()
        real_restore = sc._restore_db
        calls = []
        def failing_restore(*args, **kw):
            calls.append(args[0])
            if len(calls) == 1:
                raise OSError("injected rollback restore failure")
            return real_restore(*args, **kw)
        with patch.object(sc, "verify_applied", return_value={"status": "FAIL", "problems": ["injected"]}):
            with patch.object(sc, "_restore_db", side_effect=failing_restore):
                with self.assertRaisesRegex(sc.SmartCrateError, "PARTIAL rollback"):
                    self.apply(plan)
        result = json.loads((self.tmp / "r.json").read_text())
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("DO NOT OPEN SERATO", result["action_required"])

    def test_undo_after_serato_touched_rows(self):
        self.apply(self.plan())
        c = sqlite3.connect(str(self.master), isolation_level=None)
        c.execute("UPDATE container SET expanded = 1 WHERE name = 'Clean'")  # unrelated Serato-style change
        c.close()
        self.undo()
        for db in self.dbs:
            self.assertIsNotNone(self.find(db, ("Crate 3", "Smart Crate 1"))[0])
            self.assertIsNone(self.find(db, ("Energy", "Peak"))[0])


@unittest.skipUnless(os.environ.get("SMART_CRATE_LIVE_COPY"), "set SMART_CRATE_LIVE_COPY to a copied _Serato_/Library/preview folder")
class LiveCopyTest(unittest.TestCase):
    """Runs dry run + apply + undo on a COPY of real Serato data (never the live library)."""

    def test_live_copy(self):
        src = Path(os.environ["SMART_CRATE_LIVE_COPY"]).resolve()
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            for n in ("_Serato_", "Library", "preview"):
                shutil.copytree(src / n, t / n)
            fx = Fixture(); fx.tmp = t; fx.serato = t / "_Serato_"; fx.lib = t / "Library"
            fx.root, fx.master = fx.lib / "root.sqlite", fx.lib / "master.sqlite"; fx.dbs = [fx.master, fx.root]
            fx.retire, fx.preview = t / "retired", t / "preview"
            plan = fx.plan()
            sr.write_json(src / "dry-run-plan.json", plan)
            self.assertEqual(plan["status"], "READY", plan["problems"])
            before = fx.dump()
            res = fx.apply(plan)
            sr.write_json(src / "apply-result.json", res)
            self.assertEqual(res["status"], "PASS", res["check"])
            fx.undo()
            after = fx.dump()
            self.assertEqual(before[1], after[1])
            for k in before[0]:
                self.assertEqual(before[0][k], after[0][k], k)


if __name__ == "__main__":
    unittest.main()
