"""Repo-level mutation-surface gate. CI fails if a new uncontrolled mutation
path is added.

The previous CI check grepped two named files for forbidden calls. That proves
those two files stayed clean and nothing else, and it covers nothing at all
the day someone adds `scripts/apply_something_new.py`. These tests assert on
the structural scan instead: every module under scripts/ is discovered, every
mutation primitive in it is found via the AST, and every module carrying one
must declare which safety category it belongs to.
"""

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mutation_surface  # noqa: E402

READ_ONLY_AUDIT_SCRIPTS = ("audit_library.py", "run_verified_audit.py")


class MutationSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.report = mutation_surface.audit(REPO_ROOT)

    def test_the_repo_currently_passes(self):
        self.assertTrue(
            self.report["ok"],
            "mutation surface violations:\n  " + "\n  ".join(self.report["violations"]),
        )

    def test_every_script_declares_a_safety_classification(self):
        undeclared = [entry["module"] for entry in self.report["modules"]
                      if entry["classification"] is None]
        self.assertEqual(undeclared, [])

    def test_read_only_audit_scripts_remain_mutation_free(self):
        by_name = {Path(entry["module"]).name: entry for entry in self.report["modules"]}
        for name in READ_ONLY_AUDIT_SCRIPTS:
            with self.subTest(script=name):
                self.assertIn(name, by_name)
                self.assertEqual(by_name[name]["classification"], mutation_surface.READ_ONLY)
                self.assertEqual(by_name[name]["primitive_count"], 0)

    def test_the_retired_mutators_have_no_mutation_primitives_left(self):
        """standardize_names.py and reclassify.py were the P0 ungated mutators.
        They are preview-only now, which the scanner enforces means zero
        primitives -- not merely a docstring saying so."""
        by_name = {Path(entry["module"]).name: entry for entry in self.report["modules"]}
        for name in ("standardize_names.py", "reclassify.py"):
            with self.subTest(script=name):
                self.assertEqual(by_name[name]["classification"], mutation_surface.PREVIEW_ONLY)
                self.assertEqual(by_name[name]["primitive_count"], 0)

    def test_every_engine_user_imports_the_engine_and_the_lock_guard(self):
        engine_users = [entry["module"] for entry in self.report["modules"]
                        if entry["classification"] == mutation_surface.ENGINE_USER]
        self.assertGreater(len(engine_users), 0)
        for module in engine_users:
            with self.subTest(module=module):
                source = (REPO_ROOT / module).read_text(encoding="utf-8")
                self.assertIn("import mutation_engine", source)
                self.assertIn("import lock_guard", source)
                self.assertIn("backup_manifest", source)

    def test_disabled_legacy_modules_cannot_run(self):
        legacy = [entry["module"] for entry in self.report["modules"]
                  if entry["classification"] == mutation_surface.DISABLED_LEGACY]
        self.assertGreater(len(legacy), 0)
        for module in legacy:
            with self.subTest(module=module):
                source = (REPO_ROOT / module).read_text(encoding="utf-8")
                self.assertIn("MUTATION_DISABLED", source)

    def test_no_automated_deletion_path_exists(self):
        """Deletion primitives may only appear where deletion is the explicit,
        human-approved purpose -- or against files the tool owns itself."""
        allowed = {
            "scripts/delete_verified_duplicates.py",  # manual, EXACT_DUPLICATE only
            "scripts/folder_lock.py",                 # removes its own lock files
            "scripts/detect_bpm.py",                  # removes its own temp wav
        }
        offenders = sorted({entry["module"] for entry in self.report["deletion_primitives"]}
                           - allowed)
        self.assertEqual(offenders, [], f"unexpected deletion primitives in {offenders}")

    def test_handoff_contains_no_executable_python(self):
        self.assertEqual(self.report["handoff_python_files"], [])
        self.assertEqual(list((REPO_ROOT / "handoff").rglob("*.py")), [])

    def test_historical_handoff_mutators_are_preserved_as_archived_evidence(self):
        archived = list((REPO_ROOT / "handoff").rglob("*.py.archived"))
        self.assertGreater(len(archived), 15,
                           "the historical evidence should still be present, just inert")
        # And it must still be readable evidence, not a stub.
        sample = next(p for p in archived if "apply_" in p.name)
        self.assertGreater(len(sample.read_text(encoding="utf-8")), 500)


class ScannerBehaviorTests(unittest.TestCase):
    """The scanner has to actually catch a newly-added mutation path.

    A gate nobody has ever seen fire is not a gate. These build throwaway
    modules in a temp repo and assert the scan rejects them.
    """

    def _scan(self, filename: str, source: str) -> dict:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir()
        (root / "handoff").mkdir()
        (root / "scripts" / filename).write_text(textwrap.dedent(source), encoding="utf-8")
        return mutation_surface.audit(root)

    def test_a_new_undeclared_mutator_is_rejected(self):
        report = self._scan("apply_something_new.py", '''
            import os
            def go(src, dest):
                os.rename(src, dest)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("no SAFETY_CLASSIFICATION" in v for v in report["violations"]))

    def test_a_module_claiming_read_only_while_mutating_is_rejected(self):
        report = self._scan("sneaky_audit.py", '''
            SAFETY_CLASSIFICATION = "read-only"
            import shutil
            def go(src, dest):
                shutil.move(src, dest)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("declared 'read-only' but calls mutation primitive" in v
                            for v in report["violations"]))

    def test_a_preview_only_module_that_saves_tags_is_rejected(self):
        report = self._scan("sneaky_preview.py", '''
            SAFETY_CLASSIFICATION = "preview-only"
            from mutagen.id3 import ID3
            def go(path):
                tags = ID3(path)
                tags.save(path)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("'preview-only' but calls mutation primitive" in v
                            for v in report["violations"]))

    def test_an_engine_user_that_bypasses_the_engine_is_rejected(self):
        report = self._scan("apply_bypass.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            import os
            import lock_guard
            def go(src, dest):
                os.rename(src, dest)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("does not import mutation_engine" in v for v in report["violations"]))

    def test_a_disabled_legacy_module_without_a_guard_is_rejected(self):
        report = self._scan("old_thing.py", '''
            SAFETY_CLASSIFICATION = "disabled-legacy"
            import os
            def go(p):
                os.remove(p)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("no hard-fail guard" in v for v in report["violations"]))

    def test_self_service_infrastructure_labels_are_rejected(self):
        report = self._scan("pretends_to_be_infra.py", '''
            SAFETY_CLASSIFICATION = "infrastructure"
            import shutil
            def go(a, b):
                shutil.move(a, b)
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("INFRASTRUCTURE_ALLOWLIST" in v for v in report["violations"]))

    def test_python_reappearing_under_handoff_is_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir()
        (root / "handoff" / "old").mkdir(parents=True)
        (root / "handoff" / "old" / "apply_thing.py").write_text("import os\n", encoding="utf-8")
        report = mutation_surface.audit(root)
        self.assertFalse(report["ok"])
        self.assertTrue(any("must contain no" in v for v in report["violations"]))

    def test_a_primitive_outside_any_transaction_is_rejected(self):
        """The check that caught three real undo-path defects.

        An engine-user that imports the engine but performs a bare copy2
        somewhere outside `with engine.item(...)` must fail, even though the
        import-only check would have passed it.
        """
        report = self._scan("apply_sloppy.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            import shutil
            import lock_guard
            import mutation_engine

            def apply(engine, items):
                for index, item in enumerate(items):
                    with engine.item(index, kind="genre", source=item) as txn:
                        shutil.copy2(item, item + ".bak")
                        txn.mark_mutated()

            def undo(records):
                for record in records:
                    shutil.copy2(record["backup"], record["path"])   # no transaction
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("undo()" in v and "not inside a" in v for v in report["violations"]))

    def test_a_primitive_inside_a_transaction_is_accepted(self):
        report = self._scan("apply_tidy.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            import shutil
            import lock_guard
            import mutation_engine

            def apply(engine, items):
                for index, item in enumerate(items):
                    with engine.item(index, kind="genre", source=item) as txn:
                        shutil.copy2(item, item + ".bak")
                        txn.mark_mutated()
        ''')
        self.assertTrue(report["ok"], report["violations"])

    def test_a_declared_helper_called_from_a_transaction_is_accepted(self):
        report = self._scan("apply_helper.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            ENGINE_MUTATION_HELPERS = ("_write",)
            import shutil
            import lock_guard
            import mutation_engine

            def _write(src, dest):
                shutil.copy2(src, dest)

            def apply(engine, items):
                for index, item in enumerate(items):
                    with engine.item(index, kind="genre", source=item) as txn:
                        _write(item, item + ".bak")
                        txn.mark_mutated()
        ''')
        self.assertTrue(report["ok"], report["violations"])

    def test_a_declared_helper_never_called_from_a_transaction_is_rejected(self):
        """Declaring a helper is not enough; it must be reachable from one."""
        report = self._scan("apply_orphan.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            ENGINE_MUTATION_HELPERS = ("_write",)
            import shutil
            import lock_guard
            import mutation_engine

            def _write(src, dest):
                shutil.copy2(src, dest)

            def apply(items):
                for item in items:
                    _write(item, item + ".bak")     # never inside a transaction
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("never calls it from inside" in v for v in report["violations"]))

    def test_helper_reachability_is_transitive_only_through_declared_helpers(self):
        report = self._scan("apply_chain.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            ENGINE_MUTATION_HELPERS = ("_outer", "_inner")
            import shutil
            import lock_guard
            import mutation_engine

            def _inner(src, dest):
                shutil.copy2(src, dest)

            def _outer(txn, src, dest):
                _inner(src, dest)

            def apply(engine, items):
                for index, item in enumerate(items):
                    with engine.item(index, kind="genre", source=item) as txn:
                        _outer(txn, item, item + ".bak")
        ''')
        self.assertTrue(report["ok"], report["violations"])

    def test_an_undeclared_function_in_the_chain_breaks_reachability(self):
        report = self._scan("apply_gap.py", '''
            SAFETY_CLASSIFICATION = "engine-user"
            ENGINE_MUTATION_HELPERS = ("_inner",)
            import shutil
            import lock_guard
            import mutation_engine

            def _inner(src, dest):
                shutil.copy2(src, dest)

            def _undeclared(src, dest):
                _inner(src, dest)

            def apply(engine, items):
                for index, item in enumerate(items):
                    with engine.item(index, kind="genre", source=item) as txn:
                        _undeclared(item, item + ".bak")
        ''')
        self.assertFalse(report["ok"])
        self.assertTrue(any("never calls it from inside" in v for v in report["violations"]))

    def test_the_helper_list_may_not_grow_without_bound(self):
        limit = mutation_surface.MAX_ENGINE_MUTATION_HELPERS
        helpers = ", ".join(f'"_h{n}"' for n in range(limit + 1))
        body = f'''
            SAFETY_CLASSIFICATION = "engine-user"
            ENGINE_MUTATION_HELPERS = ({helpers},)
            import shutil
            import lock_guard
            import mutation_engine
        '''
        report = self._scan("apply_bloat.py", body)
        self.assertFalse(report["ok"])
        self.assertTrue(any("more than the" in v for v in report["violations"]))

    def test_every_engine_user_in_this_repo_passes_the_provenance_check(self):
        """Every real engine-user's primitives are inside a transaction or a
        declared, transaction-reachable helper."""
        report = mutation_surface.audit(REPO_ROOT)
        engine_users = [entry["module"] for entry in report["modules"]
                        if entry["classification"] == mutation_surface.ENGINE_USER]
        self.assertGreater(len(engine_users), 0)
        for module in engine_users:
            with self.subTest(module=module):
                violations = mutation_surface.engine_provenance_violations(
                    REPO_ROOT / module, module)
                self.assertEqual(violations, [])

    def test_str_replace_is_not_mistaken_for_a_filesystem_primitive(self):
        report = self._scan("harmless.py", '''
            SAFETY_CLASSIFICATION = "read-only"
            def go(text):
                return text.replace("a", "b")
        ''')
        self.assertTrue(report["ok"], report["violations"])

    def test_a_clean_declared_module_passes(self):
        report = self._scan("fine.py", '''
            SAFETY_CLASSIFICATION = "read-only"
            def go(path):
                return path.read_text()
        ''')
        self.assertTrue(report["ok"], report["violations"])


class CliTests(unittest.TestCase):
    def test_the_scanner_exits_zero_on_this_repo(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "mutation_surface.py")],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Mutation surface OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
