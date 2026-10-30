import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from survey_new_music_intake import load_vibe_rules, propose_vibe  # noqa: E402

RULES = {
    "vibes": ["Bounce", "Bouncy Chill"],
    "rules": [
        {"vibe": "Bounce", "artist_contains": ["Kaytranada"]},
        {"vibe": "Bouncy Chill", "artist_contains": ["Sango"]},
    ],
}


def test_single_match_proposes_vibe():
    assert propose_vibe("KAYTRANADA ft Syd", "x.mp3", [], RULES) == ("Bounce", None)


def test_existing_vibe_is_left_alone():
    assert propose_vibe("Kaytranada", "x.mp3", ["Energy: Peak", "Vibe: Hazy Rap"], RULES) == (None, None)


def test_energy_does_not_block_vibe():
    assert propose_vibe("Kaytranada", "x.mp3", ["Energy: Peak"], RULES) == ("Bounce", None)


def test_conflicting_rules_go_to_review():
    vibe, reason = propose_vibe("Sango & Kaytranada", "x.mp3", [], RULES)
    assert vibe is None and "disagree" in reason


def test_no_rules_file_means_no_proposals(tmp_path):
    assert propose_vibe("Kaytranada", "x.mp3", [], load_vibe_rules(None)) == (None, None)


def test_unknown_vibe_name_is_rejected(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"vibes": ["Bounce"], "rules": [{"vibe": "Typo"}]}))
    try:
        load_vibe_rules(p)
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")


def test_repo_vibe_rules_load():
    load_vibe_rules(Path(__file__).resolve().parents[1] / "references" / "vibe-rules.json")
