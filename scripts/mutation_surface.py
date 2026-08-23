#!/usr/bin/env python3
"""Structural discovery of every mutation path in the repo. CI fails on a new one.

The previous CI check grepped two specific filenames for forbidden calls. That
proves those two files stayed clean; it proves nothing about the other twenty-
odd scripts, and it silently covers nothing at all the day someone adds
`scripts/apply_something_new.py`. A safety property that only holds for a
hand-maintained list of today's filenames is not enforced.

So this scans with the AST instead: it finds every call to a mutation
primitive in every active Python module, and requires each module that has
one to declare, in code, which safety category it belongs to.

## The contract

Every module under `scripts/` declares a module-level constant::

    SAFETY_CLASSIFICATION = "engine-user"

with one of:

  read-only        no mutation primitives at all (audits, validators, checks)
  preview-only     builds plans/previews; may write its own output files, but
                   must not mutate library media
  mutation-engine  IS the approved shared engine
  engine-user      performs library mutations, and does so through the engine,
                   under lock + backup manifest
  disabled-legacy  historical mutator that is hard-disabled and cannot run

A module with mutation primitives may only be `mutation-engine`,
`engine-user`, or `disabled-legacy`. A `read-only` or `preview-only` module
with a mutation primitive is a hard failure -- that is the check that catches
a read-only audit script quietly growing a `.save()`.

An `engine-user` must actually import the engine, and must import lock_guard.
A `disabled-legacy` module must contain a hard-fail guard.

`handoff/` gets a blunter rule: no executable Python at all. Those files are
historical evidence of what past runs did, not tools; they are archived as
`*.py.archived` so nothing can import or `python3` them by accident.

Deletion primitives are tracked separately and reported, because "no
automated deletion path exists" is its own claim the docs make.

## What this scanner does NOT prove

Static AST analysis has hard limits, and overstating them would be worse than
having no gate at all. For an `engine-user` this proves (a) the module imports
the approved engine, and (b) every mutation primitive is written lexically
inside a `with engine.item(...)` block or inside one of at most four declared
`ENGINE_MUTATION_HELPERS` that are reachable from such a block.

It does NOT prove that a declared helper is *only ever* reached through a
transaction, nor that primitives reached via aliases, stored callbacks,
`getattr`, function tables, decorators, or cross-module calls are governed.
Proving that needs real data-flow analysis, which this deliberately is not.

So this gate is necessary but not sufficient: it catches the careless cases
reliably (it caught three undo paths doing bare `shutil.copy2()` restores
outside any transaction), and a new or substantially changed engine-user module
still requires human code review of its ENGINE_MUTATION_HELPERS. See the
"Honest limits" section of SAFETY-MODEL.md.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SAFETY_CLASSIFICATION = "read-only"

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
HANDOFF_DIR = REPO_ROOT / "handoff"

READ_ONLY = "read-only"
PREVIEW_ONLY = "preview-only"
MUTATION_ENGINE = "mutation-engine"
ENGINE_USER = "engine-user"
DISABLED_LEGACY = "disabled-legacy"
INFRASTRUCTURE = "infrastructure"

CLASSIFICATIONS = (READ_ONLY, PREVIEW_ONLY, MUTATION_ENGINE, ENGINE_USER, DISABLED_LEGACY, INFRASTRUCTURE)
MAY_MUTATE = frozenset({MUTATION_ENGINE, ENGINE_USER, DISABLED_LEGACY, INFRASTRUCTURE})

# `infrastructure` means: this module calls mutation primitives, but only ever
# against files it owns itself -- the coordination locks under .locks/, or a
# temp file it just created -- never against library media. That claim cannot
# be proven from the AST alone, so it is not a self-service label: a module
# may only declare it if it is listed here with a justification. This is
# deliberately a list of EXCEPTIONS (currently two), not the primary detection
# mechanism -- the mutation surface itself is discovered structurally.
INFRASTRUCTURE_ALLOWLIST = {
    "scripts/folder_lock.py":
        "unlink() targets only .locks/<folder>.lock.json coordination files",
    "scripts/detect_bpm.py":
        "unlink() targets only the temporary .wav it decoded for analysis",
    "scripts/lock_guard.py":
        "no primitives of its own; renews coordination locks via folder_lock",
    "scripts/metadata_batch.py":
        "atomically writes only repo handoff/T5 evidence and copies full scopes only to confined T5 backups",
}

# Attribute-style primitives: matched on the attribute name of the call.
RELOCATION_ATTRS = {"rename", "replace", "move", "copy2", "copyfile", "copytree"}
DELETION_ATTRS = {"unlink", "remove", "rmtree", "rmdir", "removedirs"}
TAG_WRITE_ATTRS = {"save"}

# Bare-name primitives (e.g. `from os import rename`).
BARE_NAMES = {"rename", "replace", "unlink", "remove", "rmtree", "rmdir", "removedirs", "move"}

PRIMITIVE_KINDS = {
    **{name: "relocation" for name in RELOCATION_ATTRS},
    **{name: "deletion" for name in DELETION_ATTRS},
    **{name: "tag_write" for name in TAG_WRITE_ATTRS},
}

# `.save()` is a very common method name. Only treat it as a media-tag write
# when the module actually works with an audio tag library; otherwise a
# json/config `.save()` would be a false positive. Every module in this repo
# that writes tags imports mutagen, so that is the discriminator.
TAG_LIBRARIES = ("mutagen",)


class Finding:
    def __init__(self, module: Path, line: int, primitive: str, kind: str, snippet: str):
        self.module = module
        self.line = line
        self.primitive = primitive
        self.kind = kind
        self.snippet = snippet

    def as_dict(self, root: Path) -> dict:
        return {
            "module": str(self.module.relative_to(root)),
            "line": self.line,
            "primitive": self.primitive,
            "kind": self.kind,
            "snippet": self.snippet,
        }


# An engine-user may declare a SMALL set of named helper functions that are
# allowed to contain mutation primitives without being lexically inside a
# `with engine.item(...)` block -- typically the one tag-writer the module
# calls from inside its transaction. Keeping the list short is the point: it
# is the exact set of functions a reviewer must check by hand.
MAX_ENGINE_MUTATION_HELPERS = 4


def _governed_call_ids(tree: ast.AST) -> set[int]:
    """id() of every node lexically inside a `with <expr>.item(...):` body.

    This is the transaction the engine opens per item, so anything written
    inside it is covered by the engine's heartbeat, precondition checks,
    write-ahead intent record and rollback.
    """
    governed: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        opens_transaction = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "item"
            for item in node.items
        )
        if not opens_transaction:
            continue
        for statement in node.body:
            for descendant in ast.walk(statement):
                governed.add(id(descendant))
    return governed


def _enclosing_function(node: ast.AST) -> str | None:
    current = getattr(node, "parent", None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = getattr(current, "parent", None)
    return None


def _attach_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _declared_helpers(tree: ast.AST) -> tuple[str, ...] | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "ENGINE_MUTATION_HELPERS":
                if isinstance(node.value, (ast.Tuple, ast.List)):
                    return tuple(
                        element.value for element in node.value.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    )
    return None


def _call_name(node: ast.Call) -> tuple[str | None, str | None]:
    """-> (attribute_or_bare_name, receiver_text)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        receiver = ""
        target = func.value
        if isinstance(target, ast.Name):
            receiver = target.id
        elif isinstance(target, ast.Attribute):
            receiver = target.attr
        return func.attr, receiver
    if isinstance(func, ast.Name):
        return func.id, None
    return None, None


def engine_provenance_violations(path: Path, relative: str) -> list[str]:
    """Check WHERE each mutation primitive sits in an engine-user module.

    See the honest-limits note in the module docstring and in SAFETY-MODEL.md:
    this is a LEXICAL check, not data-flow analysis. It proves each primitive
    call is written inside an `engine.item()` transaction, or inside one of a
    handful of explicitly named helper functions that are themselves called
    from inside such a transaction. It does not prove that every runtime path
    reaching that helper came through the engine.

    It is still worth having. It caught three real defects the import-only
    check could not: the undo paths of apply_genre_refinement_plan.py,
    apply_needs_check_recheck_genre_plan.py and delete_verified_duplicates.py
    were performing bare `shutil.copy2()` restores outside any transaction,
    with a docstring that already claimed write-ahead journaling.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    _attach_parents(tree)
    governed = _governed_call_ids(tree)
    helpers = _declared_helpers(tree) or ()
    violations: list[str] = []

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    uses_tag_library = any(name in imported for name in TAG_LIBRARIES)

    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # Which declared helpers are reachable from inside a transaction?
    #
    # Direct calls from a `with engine.item(...)` body seed the set; a declared
    # helper called from inside another already-reachable declared helper is
    # then reachable too. That transitivity is deliberate and bounded: the
    # closure may only be entered through a transaction, and may only pass
    # through functions on this short, hand-reviewed list. It is what lets
    # _apply_one_genre_write() -- which the transaction calls -- itself call
    # write_genre(), without opening the door to arbitrary call chains.
    reachable = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and id(node) in governed
    }
    for _ in range(len(helpers) + 1):        # fixpoint; the set is tiny
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            caller = _enclosing_function(node)
            if caller in reachable and caller in helpers:
                reachable.add(node.func.id)
    called_from_transaction = reachable

    if len(helpers) > MAX_ENGINE_MUTATION_HELPERS:
        violations.append(
            f"{relative}: ENGINE_MUTATION_HELPERS lists {len(helpers)} functions, more than the "
            f"{MAX_ENGINE_MUTATION_HELPERS} allowed. This list is what a human reviewer must "
            f"check by hand; keep it small or route the work through the engine directly."
        )
    for helper in helpers:
        if helper not in defined:
            violations.append(
                f"{relative}: ENGINE_MUTATION_HELPERS names {helper!r}, which is not a function "
                f"defined in this module."
            )
        elif helper not in called_from_transaction:
            violations.append(
                f"{relative}: helper {helper!r} may hold mutation primitives, but this module "
                f"never calls it from inside a `with engine.item(...)` block. Either call it "
                f"from a transaction or remove it from ENGINE_MUTATION_HELPERS."
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, receiver = _call_name(node)
        if name is None or name not in PRIMITIVE_KINDS:
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if name == "save" and receiver in {"json", "parser", "argparse"}:
            continue
        # Same discriminators the primary scan uses: a `.save()` only counts as
        # a media-tag write in a module that imports a tag library, and
        # `str.replace(a, b)` is not os.replace/Path.replace.
        if name == "save" and not uses_tag_library:
            continue
        if name == "replace" and len(node.args) >= 2:
            continue
        if id(node) in governed:
            continue
        enclosing = _enclosing_function(node)
        if enclosing in helpers:
            continue
        where = f"{enclosing}()" if enclosing else "module level"
        violations.append(
            f"{relative}:{node.lineno}: mutation primitive {name!r} in {where} is not inside a "
            f"`with engine.item(...)` transaction, and {enclosing!r} is not listed in this "
            f"module's ENGINE_MUTATION_HELPERS. Every mutation in an engine-user must run "
            f"under the engine."
        )
    return violations


def scan_module(path: Path) -> tuple[str | None, list[Finding], set[str], bool]:
    """-> (declared classification, findings, imported module names, has hard-fail guard)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()

    declared: str | None = None
    imports: set[str] = set()
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SAFETY_CLASSIFICATION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        declared = node.value.value
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    uses_tag_library = any(name in imports for name in TAG_LIBRARIES)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, receiver = _call_name(node)
        if name is None:
            continue
        is_attr = isinstance(node.func, ast.Attribute)
        if is_attr and name not in PRIMITIVE_KINDS:
            continue
        if not is_attr and name not in BARE_NAMES:
            continue
        kind = PRIMITIVE_KINDS.get(name, "relocation")
        if name == "save":
            if not uses_tag_library:
                continue
            # `parser.save()`-style false positives are not a thing here, but
            # be explicit: only count saves on objects, not module-level.
            if receiver in {"json", "parser", "argparse"}:
                continue
        # `str.replace()` is not a filesystem primitive. Distinguish by
        # argument count: os.replace/Path.replace take a path argument and
        # never take two string arguments the way str.replace does.
        if name == "replace" and len(node.args) >= 2:
            continue
        snippet = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
        findings.append(Finding(path, node.lineno, name, kind, snippet))

    guard = "MUTATION_DISABLED" in source or "_refuse_to_run" in source
    return declared, findings, imports, guard


def active_modules() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.py") if p.name != "__init__.py")


def audit(root: Path = REPO_ROOT) -> dict:
    """Full mutation-surface report. `violations` empty == the gate passes."""
    scripts_dir = root / "scripts"
    handoff_dir = root / "handoff"
    modules = sorted(p for p in scripts_dir.glob("*.py") if p.name != "__init__.py")

    violations: list[str] = []
    report: list[dict] = []
    deletion_paths: list[dict] = []

    for path in modules:
        try:
            declared, findings, imports, guard = scan_module(path)
        except SyntaxError as exc:
            violations.append(f"{path.name}: does not parse: {exc}")
            continue

        relative = str(path.relative_to(root))
        if declared is None:
            violations.append(
                f"{relative}: no SAFETY_CLASSIFICATION declared. Every module under scripts/ "
                f"must declare one of {list(CLASSIFICATIONS)}."
            )
        elif declared not in CLASSIFICATIONS:
            violations.append(
                f"{relative}: SAFETY_CLASSIFICATION {declared!r} is not one of {list(CLASSIFICATIONS)}"
            )

        if findings and declared in {READ_ONLY, PREVIEW_ONLY}:
            for finding in findings:
                violations.append(
                    f"{relative}:{finding.line}: declared {declared!r} but calls mutation primitive "
                    f"{finding.primitive!r} ({finding.snippet})"
                )

        if declared == ENGINE_USER:
            # Necessary but NOT sufficient -- see engine_provenance_violations()
            # and the "Honest limits" section of SAFETY-MODEL.md. An import
            # check proves the module has access to the approved engine; it
            # says nothing about whether any given rename/save/unlink call
            # actually flows through it. The provenance pass below adds a
            # lexical check on top; neither is a substitute for reviewing a new
            # engine-user module by hand.
            if "mutation_engine" not in imports:
                violations.append(
                    f"{relative}: declared 'engine-user' but does not import mutation_engine"
                )
            if "lock_guard" not in imports:
                violations.append(
                    f"{relative}: declared 'engine-user' but does not import lock_guard"
                )
            violations.extend(engine_provenance_violations(path, relative))
        if declared == INFRASTRUCTURE and relative not in INFRASTRUCTURE_ALLOWLIST:
            violations.append(
                f"{relative}: declared 'infrastructure' but is not in INFRASTRUCTURE_ALLOWLIST. "
                f"That label asserts the module only mutates files it owns itself; add it there "
                f"with a justification, or pick an accurate classification."
            )
        if declared == DISABLED_LEGACY and not guard:
            violations.append(
                f"{relative}: declared 'disabled-legacy' but has no hard-fail guard "
                f"(expected a MUTATION_DISABLED constant or _refuse_to_run())"
            )

        for finding in findings:
            if finding.kind == "deletion":
                deletion_paths.append(finding.as_dict(root))

        report.append({
            "module": relative,
            "classification": declared,
            "primitive_count": len(findings),
            "primitives": [finding.as_dict(root) for finding in findings],
        })

    # handoff/ must contain no executable Python at all.
    stray = sorted(handoff_dir.rglob("*.py")) if handoff_dir.is_dir() else []
    for path in stray:
        violations.append(
            f"{path.relative_to(root)}: handoff/ is historical evidence and must contain no "
            f"executable Python. Archive it as '*.py.archived'."
        )

    return {
        "modules_scanned": len(modules),
        "handoff_python_files": [str(p.relative_to(root)) for p in stray],
        "deletion_primitives": deletion_paths,
        "modules": report,
        "violations": violations,
        "ok": not violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args()

    result = audit(args.root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for entry in result["modules"]:
            print(f"{entry['classification'] or 'UNDECLARED':16} {entry['module']} "
                  f"({entry['primitive_count']} primitive(s))")
        print()
        if result["violations"]:
            print(f"{len(result['violations'])} VIOLATION(S):")
            for violation in result["violations"]:
                print(f"  - {violation}")
        else:
            print("Mutation surface OK: every mutation path is declared and governed.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
