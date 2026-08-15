"""Proves lock acquisition is atomic across real, independent OS processes --
not just sequential calls in one process, which is all the earlier
test_two_sessions_competing_for_same_folder exercised. Uses
multiprocessing's 'spawn' start method (fresh interpreter per worker, same
as any two truly separate agent sessions would be) with a Barrier so every
contender's acquire() call overlaps as closely as the OS scheduler allows.
"""

import io
import json
import multiprocessing
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "folder_lock.py"


def _acquire_worker(locks_dir: str, folder: str, session: str, barrier, result_path: str) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("folder_lock_worker", str(MODULE_PATH))
    fl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fl)
    fl.LOCKS_DIR = Path(locks_dir)

    args = SimpleNamespace(
        folder=folder, session=session, agent="test-worker", note=None,
        run_id=None, repo_sha="testsha", stale_hours=6.0,
    )

    barrier.wait()  # every worker crosses this at ~the same instant

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fl.cmd_acquire(args)
    Path(result_path).write_text(
        json.dumps({"code": code, "out": json.loads(buf.getvalue())}), encoding="utf-8"
    )


class FolderLockConcurrencyTests(unittest.TestCase):
    def test_true_simultaneous_acquisition_of_same_folder_has_exactly_one_winner(self):
        ctx = multiprocessing.get_context("spawn")
        trials, contenders = 6, 4
        for trial in range(trials):
            with tempfile.TemporaryDirectory() as tmp:
                locks_dir = Path(tmp)
                barrier = ctx.Barrier(contenders)
                result_paths = [locks_dir / f"result-{i}.json" for i in range(contenders)]
                procs = [
                    ctx.Process(
                        target=_acquire_worker,
                        args=(str(locks_dir), "Hip Hop", f"session-{i}", barrier, str(result_paths[i])),
                    )
                    for i in range(contenders)
                ]
                for p in procs:
                    p.start()
                for p in procs:
                    p.join(timeout=15)
                    self.assertEqual(p.exitcode, 0, f"trial {trial}: worker process crashed")

                results = [json.loads(rp.read_text(encoding="utf-8")) for rp in result_paths]
                winners = [r for r in results if r["out"].get("acquired") is True]
                losers = [r for r in results if r["out"].get("acquired") is False]

                self.assertEqual(
                    len(winners), 1,
                    f"trial {trial}: expected exactly one winner among {contenders} simultaneous "
                    f"contenders, got {len(winners)}: {results}",
                )
                self.assertEqual(len(losers), contenders - 1, f"trial {trial}")
                for loser in losers:
                    self.assertEqual(loser["code"], 1, f"trial {trial}: a loser must exit non-zero")
                    self.assertIn("held_by", loser["out"], f"trial {trial}: loser must report the actual winner")

                final_lock = json.loads((locks_dir / "hip-hop.lock.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    final_lock["session"], winners[0]["out"]["lock"]["session"],
                    f"trial {trial}: on-disk lock must belong to the sole winner",
                )
                # Every loser's reported winner must agree with what's on disk --
                # nobody should have been told a different session won.
                for loser in losers:
                    self.assertEqual(loser["out"]["held_by"]["session"], final_lock["session"], f"trial {trial}")

    def test_true_simultaneous_acquisition_of_different_folders_all_succeed(self):
        ctx = multiprocessing.get_context("spawn")
        folders = ["Hip Hop", "R&b", "Amapiano ", "Dub", "Events"]
        with tempfile.TemporaryDirectory() as tmp:
            locks_dir = Path(tmp)
            barrier = ctx.Barrier(len(folders))
            result_paths = [locks_dir / f"result-{i}.json" for i in range(len(folders))]
            procs = [
                ctx.Process(
                    target=_acquire_worker,
                    args=(str(locks_dir), folder, f"session-{i}", barrier, str(result_paths[i])),
                )
                for i, folder in enumerate(folders)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=15)
                self.assertEqual(p.exitcode, 0)

            results = [json.loads(rp.read_text(encoding="utf-8")) for rp in result_paths]
            for r in results:
                self.assertTrue(r["out"].get("acquired"), r)
                self.assertEqual(r["code"], 0)


if __name__ == "__main__":
    unittest.main()
