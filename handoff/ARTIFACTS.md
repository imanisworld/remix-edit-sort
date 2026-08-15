# Tracked working artifacts

This private repository mirrors the text-based files used to plan, apply, verify, undo, and hand off the DJ-library cleanup.

Tracked under `handoff/session-2026-08-15/survey/`:

- Python audit, planning, apply, verification, and undo implementations
- Markdown previews, summaries, and completion reports
- JSON plans, inventories, duplicate analysis, results, and preview data
- JSONL undo manifests
- CSV inventories and review queues

Never track:

- DJ audio or video files
- `_Serato_` databases or crates
- Full folder backups
- Tag-file backup directories containing original media
- Python caches, secrets, or credentials

The source `.codex` session workspace remains the recovery source for large media backups. Git is the continuation and audit source for code plus text artifacts.

Historical apply scripts contain absolute local paths. Use them for evidence and recovery, not as generic entrypoints. Promote reusable logic into the root `scripts/` directory only after parameterizing paths and testing dry-run behavior.
