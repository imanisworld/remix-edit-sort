"""Tests for the genre atlas snapshot and master-Genre crosswalk."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_atlas  # noqa: E402

MINI_REGIONS = [
    {"id": "electronic", "name": "Electronic & Dance", "note": "n", "genres": [
        {"n": "House", "o": "Chicago", "y": "1984", "d": "d", "rt": "Disco", "l": "Frankie Knuckles",
         "c": [{"n": "Ghetto House", "d": "d", "c": [{"n": "Juke", "d": "d"}]}]},
        {"n": "Dubstep", "d": "d", "c": [{"n": "Riddim", "d": "d"}]},
    ]},
]


class CommittedAtlasTests(unittest.TestCase):
    def test_committed_atlas_files_are_consistent(self):
        self.assertEqual(genre_atlas.check(), [])

    def test_policy_flags_forbid_track_evidence_and_training(self):
        provenance = json.loads(genre_atlas.PROVENANCE.read_text(encoding="utf-8"))
        policy = provenance["use_policy"]
        self.assertTrue(policy["never_evidence_for_a_track_tag"])
        self.assertTrue(policy["never_learner_training_data"])
        self.assertEqual(provenance["evidence_grade"], "unknown")

    def test_master_riddim_is_not_linked_to_dubstep_riddim(self):
        curated = json.loads(genre_atlas.CURATED.read_text(encoding="utf-8"))
        riddim = curated["genres"]["Riddim"]
        self.assertEqual(riddim["match"], "none")
        self.assertEqual(riddim.get("atlas", []), [])


class AtlasBehaviorTests(unittest.TestCase):
    def test_parse_extracts_embedded_data_block(self):
        html = "<script>const DATA = " + json.dumps(MINI_REGIONS) + ";\nrender();</script>"
        self.assertEqual(genre_atlas.parse_atlas_html(html), MINI_REGIONS)

    def test_parse_rejects_page_without_data(self):
        with self.assertRaises(ValueError):
            genre_atlas.parse_atlas_html("<p>no data</p>")

    def test_paths_cover_nested_cards_and_regions(self):
        index = genre_atlas.index_paths(MINI_REGIONS)
        self.assertIn("electronic:House > Ghetto House > Juke", index)
        self.assertIn("region:electronic", index)

    def test_build_rejects_drift_from_master(self):
        curated = {"genres": {"House": {"match": "exact", "atlas": ["electronic:House"]}}}
        with self.assertRaisesRegex(ValueError, "no curated entry"):
            genre_atlas.build(MINI_REGIONS, curated, ["House", "Juke"])
        with self.assertRaisesRegex(ValueError, "not in the master"):
            genre_atlas.build(MINI_REGIONS, curated, [])

    def test_build_rejects_unresolved_path_and_bad_grades(self):
        bad_path = {"genres": {"House": {"match": "exact", "atlas": ["electronic:Housee"]}}}
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            genre_atlas.build(MINI_REGIONS, bad_path, ["House"])
        none_with_path = {"genres": {"House": {"match": "none", "atlas": ["electronic:House"]}}}
        with self.assertRaisesRegex(ValueError, "must not list"):
            genre_atlas.build(MINI_REGIONS, none_with_path, ["House"])
        exact_without = {"genres": {"House": {"match": "exact", "atlas": []}}}
        with self.assertRaisesRegex(ValueError, "at least one"):
            genre_atlas.build(MINI_REGIONS, exact_without, ["House"])

    def test_check_detects_edited_snapshot_and_stale_crosswalk(self):
        with tempfile.TemporaryDirectory() as directory:
            atlas_dir = Path(directory)
            for path in (genre_atlas.SNAPSHOT, genre_atlas.PROVENANCE, genre_atlas.CURATED, genre_atlas.CROSSWALK):
                shutil.copy(path, atlas_dir / path.name)
            self.assertEqual(genre_atlas.check(atlas_dir), [])

            crosswalk = atlas_dir / genre_atlas.CROSSWALK.name
            crosswalk.write_text(crosswalk.read_text(encoding="utf-8").replace("Detroit", "Detroyt", 1), encoding="utf-8")
            self.assertTrue(any("stale" in p for p in genre_atlas.check(atlas_dir)))

            snapshot = atlas_dir / genre_atlas.SNAPSHOT.name
            snapshot.write_bytes(snapshot.read_bytes().replace(b"Detroit", b"Detroyt", 1))
            self.assertTrue(any("PROVENANCE" in p for p in genre_atlas.check(atlas_dir)))

    def test_extract_writes_snapshot_matching_provenance_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = root / "atlas.html"
            html.write_text("<script>const DATA = " + json.dumps(MINI_REGIONS) + ";</script>", encoding="utf-8")
            provenance = genre_atlas.extract(html, "https://example.invalid/atlas", "2026-10-13", root / "out")
            snapshot = (root / "out" / genre_atlas.SNAPSHOT.name).read_bytes()
            self.assertEqual(provenance["snapshot_sha256"], genre_atlas.sha256_bytes(snapshot))
            self.assertEqual(provenance["stats"]["cards_total"], 5)


if __name__ == "__main__":
    unittest.main()
