#!/usr/bin/env python3
"""Read-only candidate time matching for independently verified DJ set/play data.

Requires dated actual DJ set windows, IANA zones, offset-aware play timestamps
and indexed local file identities. Outputs candidates, never proof or an apply.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ClockEvidenceError(ValueError):
    """A date/time or source relationship cannot be safely resolved."""


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or ("/" not in name and name != "UTC"):
        raise ClockEvidenceError("IANA time zone required; EST/EDT abbreviations are insufficient")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClockEvidenceError(f"unknown IANA timezone: {name!r}") from exc


def _local(value: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise ClockEvidenceError("full local date and time required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ClockEvidenceError(f"invalid ISO local datetime: {value!r}") from exc
    if parsed.tzinfo is not None:
        raise ClockEvidenceError("local set time must not contain a UTC offset")
    return parsed


def resolve_wall_clock(local_iso: str, zone: str, fold: int | None = None) -> datetime:
    """Refuse spring gaps and fall-back hours lacking an explicit fold."""
    wall = _local(local_iso)
    tz = _zone(zone)
    candidates = {}
    for side in (0, 1):
        resolved = wall.replace(tzinfo=tz, fold=side)
        instant = resolved.astimezone(timezone.utc)
        if instant.astimezone(tz).replace(tzinfo=None) == wall:
            candidates[instant] = resolved
    if not candidates:
        raise ClockEvidenceError(f"nonexistent spring-forward time: {local_iso} {zone}")
    if len(candidates) == 2:
        if type(fold) is not int or fold not in (0, 1):
            raise ClockEvidenceError(f"ambiguous fall-back time requires explicit fold: {local_iso}")
        result = wall.replace(tzinfo=tz, fold=fold)
        if result.astimezone(timezone.utc) not in candidates:
            raise ClockEvidenceError("DST fold did not resolve to a real instant")
        return result
    if fold not in (None, 0):
        raise ClockEvidenceError("fold=1 applies only to an ambiguous clock time")
    return next(iter(candidates.values()))


def event_window(event: dict) -> dict:
    if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not event["event_id"].strip():
        raise ClockEvidenceError("nonblank event_id required")
    if event.get("set_time_verified") is not True:
        raise ClockEvidenceError("actual DJ set start and end are unverified")
    zone = event.get("event_timezone")
    start = resolve_wall_clock(event.get("set_start_local"), zone, event.get("set_start_fold"))
    end = resolve_wall_clock(event.get("set_end_local"), zone, event.get("set_end_fold"))
    first, last = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if last <= first:
        raise ClockEvidenceError("end must occur after start in real elapsed time")
    if last - first > timedelta(hours=24):
        raise ClockEvidenceError("set longer than 24 hours requires review")
    return {"event_id": event["event_id"], "zone": zone,
            "performance_date": start.date().isoformat(),
            "start_utc": first, "end_utc": last}


def playback_instant(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ClockEvidenceError("playback requires explicit date, time and UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClockEvidenceError(f"invalid playback timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClockEvidenceError("playback timestamp lacks UTC offset")
    return parsed.astimezone(timezone.utc)


def preview(events: list[dict], plays: list[dict]) -> dict:
    """Only unique temporal matches with precise source identity become candidates."""
    if not isinstance(events, list) or not isinstance(plays, list):
        raise ClockEvidenceError("events and plays must both be arrays")
    windows, invalid, seen_events = [], [], set()
    for event in events:
        name = event.get("event_id") if isinstance(event, dict) else None
        if name in seen_events:
            raise ClockEvidenceError(f"duplicate event ID: {name!r}")
        seen_events.add(name)
        try:
            windows.append(event_window(event))
        except ClockEvidenceError as exc:
            invalid.append({"event_id": name, "reason": str(exc)})
    result, seen_plays = [], set()
    for entry in plays:
        if not isinstance(entry, dict) or not isinstance(entry.get("play_id"), str) or not entry["play_id"].strip():
            raise ClockEvidenceError("every playback record needs nonblank play_id")
        if entry["play_id"] in seen_plays:
            raise ClockEvidenceError(f"duplicate play ID: {entry['play_id']}")
        seen_plays.add(entry["play_id"])
        row = {"play_id": entry["play_id"], "state": "REVIEW",
               "candidate_event_ids": [], "reason": None, "file_id": None,
               "played_at_utc": None, "played_local_date": None,
               "performance_date": None}
        try:
            at = playback_instant(entry.get("played_at"))
        except ClockEvidenceError as exc:
            row["reason"] = str(exc)
            result.append(row)
            continue
        row["played_at_utc"] = at.isoformat()
        matches = [w for w in windows if w["start_utc"] <= at < w["end_utc"]]
        row["candidate_event_ids"] = [w["event_id"] for w in matches]
        if len(matches) != 1:
            row["reason"] = "overlapping set windows" if matches else "outside verified set windows"
        elif entry.get("played_status") != "PLAYED":
            row["reason"] = "missing/unplayed/manually uncertain play status"
        elif (entry.get("identity_status") != "EXACT_INDEXED_FILE"
              or type(entry.get("file_id")) is not int or entry["file_id"] < 1):
            row["reason"] = "no exact indexed track identity"
        elif not (isinstance(entry.get("source_artifact_sha256"), str)
                  and len(entry["source_artifact_sha256"]) == 64
                  and all(c in "0123456789abcdefABCDEF" for c in entry["source_artifact_sha256"])):
            row["reason"] = "missing source-history artifact fingerprint"
        elif not isinstance(entry.get("session_id"), str) or not entry["session_id"].strip():
            row["reason"] = "missing source history session ID"
        else:
            w = matches[0]
            row.update({"state": "CANDIDATE_ONLY",
                        "reason": "temporal plus indexed-file candidate; independent review required",
                        "file_id": entry["file_id"],
                        "played_local_date": at.astimezone(_zone(w["zone"])).date().isoformat(),
                        "performance_date": w["performance_date"]})
        result.append(row)
    return {"mode": "preview-only", "proof_of_play": False,
            "events_requiring_review": invalid,
            "verified_set_count": len(windows), "plays": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="private reviewed event set fixture JSON")
    parser.add_argument("--plays", type=Path, required=True, help="private normalized play fixture JSON")
    args = parser.parse_args(argv)
    try:
        outcome = preview(
            json.loads(args.events.read_text(encoding="utf-8")),
            json.loads(args.plays.read_text(encoding="utf-8")),
        )
    except (ClockEvidenceError, OSError, ValueError) as exc:
        parser.exit(2, f"REFUSED: {exc}\n")
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
