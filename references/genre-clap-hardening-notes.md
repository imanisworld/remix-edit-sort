# Genre CLAP hardening follow-up

Status: implementation scaffolding only; no learner default or production behavior changed.

## Proven during the September 6 controlled experiments

- The merged genre experiment harness is read-only toward music files, Serato, and taxonomy.
- Transformers API shape differs across installed versions: the CLAP processor may expose `audio` or `audios`, and audio features may be returned directly as a tensor or wrapped with `pooler_output`.
- `laion/larger_clap_music` produced degenerate embeddings during the controlled experiment and must not be selected as a default without an explicit passing sanity preflight.
- A working replacement checkpoint is still under comparison; do not choose one in this follow-up until the seed-stability/model comparison is reviewed.

## Hardening prepared on this branch

`genre_clap_support.py` provides dependency-light helpers for:

- Transformers 4/5 processor keyword compatibility;
- tensor vs pooled-output unwrapping;
- feature-cache identity including checkpoint revision and feature implementation version;
- Torch/Transformers runtime-version capture;
- a tiny embedding-diversity preflight that fails closed on zero, non-finite, inconsistent, or collapsed embeddings.

Synthetic tests require no Torch, Transformers, network access, music files, Serato data, or external drives.

## Deliberately not integrated yet

Do not wire these helpers into `genre_learner_experiments.py`, change `CLAP_DEFAULT_MODEL`, or promote CLAP into the normal learner until the ongoing `larger_clap_music_and_speech` vs `larger_clap_general` comparison is complete and reviewed.
