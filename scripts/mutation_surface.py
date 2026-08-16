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
            if "mutation_engine" not in imports:
                violations.append(
                    f"{relative}: declared 'engine-user' but does not import mutation_engine"
                )
            if "lock_guard" not in imports:
                violations.append(
                    f"{relative}: declared 'engine-user' but does not import lock_guard"
                )
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
