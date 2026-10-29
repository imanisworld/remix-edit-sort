"""set_serato_date_added.py: synthetic database V2 only."""
import struct, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import set_serato_date_added as t  # noqa: E402


def field(tag, payload):
    return tag + struct.pack(">I", len(payload)) + payload


def otrk(path, epoch):
    body = (field(b"ttyp", "mp3".encode("utf-16-be")) + field(b"pfil", path.encode("utf-16-be"))
            + field(b"tadd", str(epoch).encode("utf-16-be")) + field(b"uadd", struct.pack(">I", epoch)))
    return field(b"otrk", body)


def db(*tracks):
    return field(b"vrsn", "2.0/Serato Scratch LIVE Database".encode("utf-16-be")) + b"".join(tracks)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = db(otrk("Users/a/x.mp3", 1775177187), otrk("Users/a/y.mp3", 1775177187))
        (self.tmp / "database V2").write_bytes(self.data)

    def test_only_fields_change(self):
        rec = t.track_fields(self.data, "Users/a/x.mp3")[0]
        new = t.rebuild(self.data, rec, 1499225906)
        self.assertEqual(len(new), len(self.data))
        self.assertTrue(t.changed_ranges_ok(self.data, new, rec))
        self.assertEqual(t.read_values(new, rec), {"tadd": 1499225906, "uadd": 1499225906})
        other = t.track_fields(new, "Users/a/y.mp3")[0]
        self.assertEqual(t.read_values(new, other), {"tadd": 1775177187, "uadd": 1775177187})

    def test_refuses_later_or_length_change(self):
        with self.assertRaises(t.DateAddedError):
            t.make_plan(self.tmp / "database V2", "Users/a/x.mp3", 1800000000, "")
        rec = t.track_fields(self.data, "Users/a/x.mp3")[0]
        with self.assertRaises(t.DateAddedError):
            t.rebuild(self.data, rec, 999999999)

    def test_plan_validates_and_detects_drift(self):
        import json
        plan = t.make_plan(self.tmp / "database V2", "Users/a/x.mp3", 1499225906, "test")
        p = self.tmp / "plan.json"; p.write_text(json.dumps(plan))
        t.load_and_validate(p, self.tmp)
        (self.tmp / "database V2").write_bytes(self.data + b"x")
        with self.assertRaises(t.DateAddedError):
            t.load_and_validate(p, self.tmp)

    def test_unknown_track(self):
        with self.assertRaises(t.DateAddedError):
            t.make_plan(self.tmp / "database V2", "Users/a/none.mp3", 1499225906, "")


if __name__ == "__main__":
    unittest.main()


class BatchTests(unittest.TestCase):
    def test_batch_plan_simulate(self):
        import json, shutil
        tmp = Path(tempfile.mkdtemp())
        data = db(otrk("Users/a/x.mp3", 1775177187), otrk("Users/a/y.mp3", 1775177187), otrk("Users/a/z.mp3", 1600000000))
        (tmp / "database V2").write_bytes(data)
        plan, skipped = t.make_batch_plan(tmp / "database V2", [
            ("Users/a/x.mp3", 1499225906, ""), ("Users/a/z.mp3", 1700000000, ""), ("Users/a/nope.mp3", 1499225906, "")])
        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(skipped, {"database V2 date already earlier or equal": 1, "not in database V2": 1})
        p = tmp / "plan.json"; p.write_text(json.dumps(plan))
        copy = tmp / "copy"; shutil.copy(tmp / "database V2", copy)
        r = t.simulate(p, tmp, copy)
        self.assertEqual(r["tracks_read_back"], 1)
        self.assertEqual((tmp / "database V2").read_bytes(), data)  # live untouched
        with self.assertRaises(t.DateAddedError):
            t.simulate(p, tmp, tmp / "database V2")


class ApplyTests(unittest.TestCase):
    """apply() end to end with a stub engine (regression: NameError 'after' on batch apply)."""
    def test_batch_apply_writes_and_logs_undo(self):
        import contextlib, json, shutil
        tmp = Path(tempfile.mkdtemp())
        data = db(otrk("Users/a/x.mp3", 1775177187), otrk("Users/a/y.mp3", 1775177187))
        (tmp / "database V2").write_bytes(data)
        plan, _ = t.make_batch_plan(tmp / "database V2", [("Users/a/x.mp3", 1499225906, ""), ("Users/a/y.mp3", 1499225907, "")])
        p = tmp / "plan.json"; p.write_text(json.dumps(plan))
        (tmp / "bk").mkdir(); shutil.copy(tmp / "database V2", tmp / "bk" / "database V2")

        class Txn:
            def on_rollback(self, fn): pass
            def mark_mutated(self): pass
            def record(self, **kw): self.rec = kw

        class Engine:
            def __init__(self, *a, **k): pass
            def start(self, *a): pass
            def finish(self, *a): pass
            @contextlib.contextmanager
            def item(self, *a, **k): yield Txn()

        orig = t.mutation_engine.MutationEngine
        t.mutation_engine.MutationEngine = Engine
        try:
            r = t.apply(p, tmp, {}, tmp / "bk", tmp, "test", None)
        finally:
            t.mutation_engine.MutationEngine = orig
        self.assertEqual(r["changed"], 2)
        new = (tmp / "database V2").read_bytes()
        self.assertEqual(len(new), len(data))
        for path, ep in (("Users/a/x.mp3", 1499225906), ("Users/a/y.mp3", 1499225907)):
            self.assertEqual(t.read_values(new, t.track_fields(new, path)[0]), {"tadd": ep, "uadd": ep})
        undo = json.loads(Path(r["undo_log"]).read_text().splitlines()[0])
        self.assertEqual(undo["length"], len(data))
        self.assertEqual(undo["tracks"], 2)
