"""Read-only Artist/Title identification proposal coverage."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_artist_identification_plan  # noqa: E402
import apply_artist_title_plan  # noqa: E402


INVENTORY_FIELDS = [
    "full_path",
    "filename",
    "artist",
    "title",
    "file_type",
    "duration_seconds",
    "sha256",
]


def inventory(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def row(
    root: Path,
    filename: str,
    *,
    artist: str = "",
    title: str = "",
    file_type: str = "mp3",
    sha256: str = "a" * 64,
    duration: str = "180",
) -> dict[str, str]:
    return {
        "full_path": str(root / filename),
        "filename": Path(filename).name,
        "artist": artist,
        "title": title,
        "file_type": file_type,
        "duration_seconds": duration,
        "sha256": sha256,
    }


class ArtistIdentificationProposalTests(unittest.TestCase):
    def build(self, rows: list[dict[str, str]], *, chromaprint: bool = False) -> tuple[dict, list[dict], Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        inventory_path = inventory(root / "inventory.csv", rows)
        out_dir = root / "out"
        result = build_artist_identification_plan.build(
            inventory_path=inventory_path,
            out_dir=out_dir,
            use_chromaprint=chromaprint,
        )
        plan = json.loads((out_dir / "artist-identification-plan.json").read_text(encoding="utf-8"))
        with (out_dir / "artist-identification-review.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            review = list(csv.DictReader(handle))
        return plan, review, result

    def test_filename_alone_is_candidate_only_and_never_approved(self):
        root = Path("/music")
        plan, review, _ = self.build([row(root, "Artist - Title.mp3")])

        self.assertEqual(plan["mode"], "artist-identification-review")
        self.assertFalse(plan["rules"]["apply_compatible"])
        self.assertEqual(review[0]["proposed_artist"], "Artist")
        self.assertEqual(review[0]["proposed_title"], "Title")
        self.assertEqual(review[0]["status"], "NEEDS_CORROBORATION")
        self.assertEqual(review[0]["confidence"], "LOW")
        self.assertEqual(review[0]["review_decision"], "")
        self.assertIn("filename", review[0]["reasons"])

    def test_review_plan_is_rejected_by_existing_apply_loader(self):
        root = Path("/music")
        _, _, result = self.build([row(root, "Artist - Title.mp3")])
        plan_path = result["out_dir"] / "artist-identification-plan.json"

        with self.assertRaisesRegex(RuntimeError, "unexpected plan mode"):
            apply_artist_title_plan.load_and_validate(plan_path)

    def test_exact_hash_with_different_tag_state_is_an_inventory_conflict(self):
        root = Path("/music")
        same_hash = "b" * 64
        _, review, _ = self.build(
            [
                row(root, "imports/Artist - Title.mp3", sha256=same_hash),
                row(
                    root,
                    "known/reference.mp3",
                    artist="Artist",
                    title="Title",
                    sha256=same_hash,
                ),
            ]
        )

        self.assertEqual(review[0]["status"], "CONFLICT")
        self.assertEqual(review[0]["confidence"], "NONE")
        self.assertIn("identical bytes cannot have different embedded tags", review[0]["reasons"])

    def test_exact_hash_blank_twin_is_reported_but_does_not_elevate_filename(self):
        root = Path("/music")
        same_hash = "e" * 64
        _, review, _ = self.build(
            [
                row(root, "imports/Artist - Title.mp3", sha256=same_hash),
                row(root, "copies/Artist - Title (1).mp3", sha256=same_hash),
            ]
        )

        self.assertEqual(review[0]["status"], "NEEDS_CORROBORATION")
        self.assertEqual(review[0]["confidence"], "LOW")
        self.assertIn("exact-hash duplicate", review[0]["reasons"])

    def test_conflicting_exact_hash_tags_fail_closed(self):
        root = Path("/music")
        same_hash = "c" * 64
        _, review, _ = self.build(
            [
                row(root, "imports/Artist - Title.mp3", sha256=same_hash),
                row(root, "known/a.mp3", artist="Artist", title="Title", sha256=same_hash),
                row(root, "known/b.mp3", artist="Wrong", title="Identity", sha256=same_hash),
            ]
        )

        self.assertEqual(review[0]["status"], "CONFLICT")
        self.assertEqual(review[0]["confidence"], "NONE")
        self.assertIn("inventory is inconsistent", review[0]["reasons"])

    def test_existing_title_can_anchor_only_the_missing_artist_candidate(self):
        root = Path("/music")
        _, review, _ = self.build(
            [row(root, "Artist - Title.mp3", title="Title")]
        )

        self.assertEqual(review[0]["current_title"], "Title")
        self.assertEqual(review[0]["proposed_artist"], "Artist")
        self.assertEqual(review[0]["proposed_title"], "Title")
        self.assertIn("existing title matches", review[0]["reasons"])

    def test_meaningful_numeric_artist_is_not_stripped_as_track_number(self):
        root = Path("/music")
        _, review, _ = self.build([row(root, "21 Savage - Redrum.mp3")])

        self.assertEqual(review[0]["proposed_artist"], "21 Savage")
        self.assertEqual(review[0]["proposed_title"], "Redrum")

    def test_punctuated_track_number_prefix_is_removed(self):
        root = Path("/music")
        _, review, _ = self.build([row(root, "01. Artist - Title.mp3")])

        self.assertEqual(review[0]["proposed_artist"], "Artist")
        self.assertEqual(review[0]["proposed_title"], "Title")

    def test_outside_scope_rows_are_separate_from_review_items(self):
        root = Path("/music")
        plan, review, result = self.build(
            [
                row(root, "Imani Drops/Artist - Title.mp3"),
                row(root, "imports/Artist - Title.flac", file_type="flac"),
                row(root, "known/complete.mp3", artist="Known", title="Complete"),
                row(root, "imports/Blank - Candidate.mp3"),
            ]
        )

        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(len(review), 1)
        self.assertEqual(result["outside_scope_count"], 3)
        with (result["out_dir"] / "artist-identification-outside-scope.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            outside = list(csv.DictReader(handle))
        self.assertEqual(
            {item["outside_scope_reason"] for item in outside},
            {
                "excluded_path_component: Imani Drops",
                "unsupported_eventual_apply_format: flac",
                "artist_and_title_already_present",
            },
        )

    def test_requested_chromaprint_without_fpcalc_records_failure_without_confidence(self):
        root = Path("/music")
        original = build_artist_identification_plan.shutil.which
        self.addCleanup(setattr, build_artist_identification_plan.shutil, "which", original)
        build_artist_identification_plan.shutil.which = lambda _name: None

        plan, review, _ = self.build(
            [
                row(root, "imports/Artist - Title.mp3"),
                row(root, "known/reference.mp3", artist="Artist", title="Title", sha256="d" * 64),
            ],
            chromaprint=True,
        )

        self.assertEqual(plan["chromaprint"]["status"], "FAILED")
        self.assertEqual(review[0]["status"], "NEEDS_CORROBORATION")
        self.assertEqual(review[0]["confidence"], "LOW")
        self.assertIn("fpcalc", review[0]["reasons"])

    def test_chromaprint_tagged_match_corroborates_for_review_but_never_approves(self):
        root = Path("/music")
        with (
            patch.object(build_artist_identification_plan.shutil, "which", return_value="/usr/bin/fpcalc"),
            patch.object(
                build_artist_identification_plan.audio_fingerprint,
                "fingerprint",
                side_effect=lambda path: [1] * 80 if "imports" in str(path) else [2] * 80,
            ),
            patch.object(
                build_artist_identification_plan.audio_fingerprint,
                "similarity",
                return_value=0.91,
            ),
        ):
            _, review, _ = self.build(
                [
                    row(root, "imports/Artist - Title.mp3"),
                    row(
                        root,
                        "known/reference.mp3",
                        artist="Artist",
                        title="Title",
                        sha256="f" * 64,
                    ),
                ],
                chromaprint=True,
            )

        self.assertEqual(review[0]["status"], "CORROBORATED_FOR_REVIEW")
        self.assertEqual(review[0]["confidence"], "MEDIUM")
        self.assertEqual(
            review[0]["corroboration"], "chromaprint_tagged_inventory_match"
        )
        self.assertEqual(review[0]["review_decision"], "")

    def test_partial_chromaprint_failure_discards_all_fingerprint_matches(self):
        root = Path("/music")

        def fingerprint(path: Path) -> list[int]:
            if path.name == "broken.mp3":
                raise RuntimeError("probe failed")
            return [1] * 80

        with (
            patch.object(build_artist_identification_plan.shutil, "which", return_value="/usr/bin/fpcalc"),
            patch.object(
                build_artist_identification_plan.audio_fingerprint,
                "fingerprint",
                side_effect=fingerprint,
            ),
            patch.object(
                build_artist_identification_plan.audio_fingerprint,
                "similarity",
                return_value=0.91,
            ),
        ):
            plan, review, _ = self.build(
                [
                    row(root, "imports/Artist - Title.mp3"),
                    row(
                        root,
                        "known/reference.mp3",
                        artist="Artist",
                        title="Title",
                        sha256="f" * 64,
                    ),
                    row(
                        root,
                        "known/broken.mp3",
                        artist="Other",
                        title="Identity",
                        sha256="1" * 64,
                    ),
                ],
                chromaprint=True,
            )

        self.assertEqual(plan["chromaprint"]["status"], "FAILED")
        self.assertEqual(review[0]["status"], "NEEDS_CORROBORATION")
        self.assertEqual(review[0]["confidence"], "LOW")

    def test_invalid_sha256_is_outside_scope_not_exact_hash_evidence(self):
        root = Path("/music")
        plan, review, result = self.build(
            [row(root, "Artist - Title.mp3", sha256="not-a-sha")]
        )

        self.assertEqual(plan["items"], [])
        self.assertEqual(review, [])
        self.assertEqual(result["outside_scope_count"], 1)
        with (result["out_dir"] / "artist-identification-outside-scope.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            outside = list(csv.DictReader(handle))
        self.assertEqual(outside[0]["outside_scope_reason"], "invalid_sha256")


if __name__ == "__main__":
    unittest.main()
