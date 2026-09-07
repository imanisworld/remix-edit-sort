# Genre CLAP hardening follow-up

Status: read-only CLAP experiment hardening is integrated through a separate wrapper; the production learner and taxonomy remain unchanged.

## Proven during the September 6 controlled experiments

- The merged genre experiment harness is read-only toward music files, Serato, and taxonomy.
- Baseline DSP reproduced on the same 729-track frozen evaluation set.
- Three-window CLAP is the proven representation choice. Across seeds 42 / 7 / 13, `laion/larger_clap_general` won every priority metric over `laion/larger_clap_music_and_speech`.
- `larger_clap_general` three-seed means: 0.668 accuracy, 0.641 balanced accuracy, 0.642 macro F1.
- `larger_clap_music_and_speech` three-seed means: 0.656 accuracy, 0.628 balanced accuracy, 0.626 macro F1.
- General won every seed on every priority metric by roughly +0.008 to +0.024 and had the tighter accuracy spread (0.665 to 0.669 versus 0.650 to 0.661).
- Three-window aggregation uses deterministic 20% / 50% / 80% positions.
- `laion/larger_clap_music` produced degenerate embeddings at the tested revision and is explicitly excluded.

## Pinned checkpoint revisions

- Default: `laion/larger_clap_general@ada0c23a36c4e8582805bb38fec3905903f18b41`
- Fallback: `laion/larger_clap_music_and_speech@195c3a3e68faebb3e2088b9a79e79b43ddbda76b`
- Excluded degenerate revision: `laion/larger_clap_music@a0b4534a14f58e20944452dff00a22a06ce629d1`

The experiment used `pytorch_model.bin` from the pinned Hub revisions. Safetensors fetched through PR refs were not the evaluated weights.

## Compatibility findings

- Transformers API shape differs across installed versions: the CLAP processor may expose `audio` or `audios`.
- Audio features may be returned directly as a tensor or wrapped with `pooler_output`.
- A model name alone is insufficient cache provenance because Hub revisions can move.

## Hardening on this branch

`genre_clap_support.py` provides dependency-light helpers for:

- Transformers 4/5 processor keyword compatibility;
- tensor vs pooled-output unwrapping;
- feature-cache identity including checkpoint revision and feature implementation version;
- Torch/Transformers runtime-version capture;
- embedding-diversity sanity checks that fail closed on zero, non-finite, inconsistent, or collapsed embeddings.

`genre_learner_clap_hardened.py` provides a read-only entrypoint that:

- defaults to the three-seed-winning `larger_clap_general` checkpoint at its exact pinned revision;
- keeps the three-seed-tested `music_and_speech` revision as the approved fallback;
- refuses alternate revisions for those approved checkpoint names;
- requires an explicit revision for any unapproved checkpoint;
- refuses the known degenerate `larger_clap_music` revision;
- loads both processor and model at the exact revision;
- runs a three-signal synthetic-audio checkpoint sanity preflight before library feature extraction;
- defaults CLAP to the proven three-window representation while preserving explicit comparison overrides;
- preserves normalized three-window embedding averaging;
- isolates cache entries by checkpoint revision and implementation version;
- records checkpoint/runtime provenance in the experiment feature spec.

Synthetic tests require no Torch, Transformers, network access, music files, Serato data, or external drives.

No external evidence collection, tag writes, music moves, Serato changes, taxonomy changes, or learner-assisted bulk tagging are authorized by this follow-up.
