"""Unit tests for reviewed external Genre evidence handling.

No test touches the real library, Serato data, network, or external drives.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_genre_external_manifest as builder  # noqa: E402
import genre_learner  # noqa: E402


class GenreExternalManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = genre_learner.load_rules(REPO_ROOT / "references/genre-learner-rules.json")
        cls.a_path = str(Path("/tmp/genre-manifest-test/a.mp3").resolve())
        cls.b_path = str(Path("/tmp/genre-manifest-test/b.mp3").resolve())
        cls.targets = {
            cls.a_path: {"path": cls.a_path, "artist": "Artist A", "title": "Song A"},
            cls.b_path: {"path": cls.b_path, "artist": "Artist B", "title": "Song B"},
        }

    def accepted(self, **overrides):
        row = {
            "path": self.a_path,
            "artist": "Artist A",
            "title": "Song A",
            "genre": "Afrobeats",
            "source": "Bandcamp",
            "source_url": "https://artist.bandcamp.com/track/song-a",
            "label_confidence": "reviewed",
            "accepted": True,
            "evidence_kind": "track_tag",
        }
        row.update(overrides)
        return row

    def test_alias_is_normalized_to_current_canonical_genre(self):
        examples, report = builder.build_manifest([self.accepted()], self.targets, self.rules)
        self.assertEqual(report["rejected_rows"], 0)
        self.assertEqual(report["conflicting_paths"], 0)
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["genre"], "Afrobeat")
        self.assertEqual(examples[0]["source"], "bandcamp")

    def test_unique_artist_title_can_match_when_path_is_absent(self):
        row = self.accepted(path="")
        examples, report = builder.build_manifest([row], self.targets, self.rules)
        self.assertEqual(report["rejected_rows"], 0)
        self.assertEqual(examples[0]["path"], self.a_path)

    def test_spotify_is_research_only_and_never_enters_manifest(self):
        row = self.accepted(
            source="Spotify",
            source_url="https://open.spotify.com/track/example",
        )
        examples, report = builder.build_manifest([row], self.targets, self.rules)
        self.assertEqual(examples, [])
        self.assertEqual(report["rejected_rows"], 1)
        self.assertEqual(report["rejected"][0]["reason"], "spotify_policy_disallows_ml_training")

    def test_show_mix_playlist_and_platform_category_are_too_weak(self):
        for evidence_kind in ("show_tag", "mix_tag", "playlist_tag", "platform_category"):
            with self.subTest(evidence_kind=evidence_kind):
                row = self.accepted(evidence_kind=evidence_kind)
                examples, report = builder.build_manifest([row], self.targets, self.rules)
                self.assertEqual(examples, [])
                self.assertEqual(
                    report["rejected"][0]["reason"],
                    f"weak_evidence_kind:{evidence_kind}",
                )

    def test_conflicting_accepted_sources_hold_track_instead_of_guessing(self):
        bandcamp = self.accepted()
        soundcloud = self.accepted(
            genre="Gqom",
            source="SoundCloud",
            source_url="https://soundcloud.com/artist-a/song-a",
        )
        examples, report = builder.build_manifest(
            [bandcamp, soundcloud], self.targets, self.rules
        )
        self.assertEqual(examples, [])
        self.assertEqual(report["conflicting_paths"], 1)
        self.assertEqual(
            report["conflicts"][0]["genres"],
            ["Afrobeat", "Gqom"],
        )

    def test_two_sources_agree_and_provenance_is_preserved(self):
        bandcamp = self.accepted(genre="Gqom")
        soundcloud = self.accepted(
            genre="Gqom",
            source="SoundCloud",
            source_url="https://soundcloud.com/artist-a/song-a",
        )
        examples, report = builder.build_manifest(
            [bandcamp, soundcloud], self.targets, self.rules
        )
        self.assertEqual(report["conflicting_paths"], 0)
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["genre"], "Gqom")
        self.assertEqual(
            {source["source"] for source in examples[0]["sources"]},
            {"bandcamp", "soundcloud"},
        )

    def test_context_label_never_becomes_training_genre(self):
        row = self.accepted(genre="Lounge")
        examples, report = builder.build_manifest([row], self.targets, self.rules)
        self.assertEqual(examples, [])
        self.assertEqual(report["rejected"][0]["reason"], "research_label_is_context_not_genre")

    def test_unaccepted_or_low_confidence_evidence_is_rejected(self):
        for row, reason in (
            (self.accepted(accepted=False), "not_explicitly_accepted"),
            (self.accepted(label_confidence="medium"), "label_confidence_is_not_high_or_reviewed"),
        ):
            with self.subTest(reason=reason):
                examples, report = builder.build_manifest([row], self.targets, self.rules)
                self.assertEqual(examples, [])
                self.assertEqual(report["rejected"][0]["reason"], reason)


if __name__ == "__main__":
    unittest.main()
