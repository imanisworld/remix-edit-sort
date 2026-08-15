"""Locate optional reference files that other skills already curated.

music-library-sort ships an artist->genre map and a casing-override dictionary.
Reusing them beats re-deriving the same knowledge. They live in an app-managed
folder whose UUIDs change, so search rather than hardcode. Everything here is
optional — the skill degrades gracefully when nothing is found.
"""
import os, glob, json

APP_SUPPORT = os.path.expanduser(
    "~/Library/Application Support/Claude/local-agent-mode-sessions")
USER_SKILLS = os.path.expanduser("~/.claude/skills")


def find_reference(skill_name, filename):
    """Return the newest matching reference file path, or None."""
    patterns = [
        os.path.join(USER_SKILLS, skill_name, "references", filename),
        os.path.join(APP_SUPPORT, "skills-plugin", "*", "*", "skills",
                     skill_name, "references", filename),
        os.path.join(APP_SUPPORT, "*", "*", "*", "outputs",
                     skill_name, "references", filename),
    ]
    hits = []
    for p in patterns:
        hits.extend(glob.glob(p))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def load_reference(skill_name, filename, default=None):
    path = find_reference(skill_name, filename)
    if not path:
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default
