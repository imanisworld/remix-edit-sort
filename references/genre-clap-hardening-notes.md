# Genre CLAP hardening follow-up

Status: read-only CLAP experiment hardening is integrated through a separate wrapper; the production learner and taxonomy remain unchanged.

## Proven during the September 6 controlled experiments

- The merged genre experiment harness is read-only toward music files, Serato, and taxonomy.
- Baseline DSP reproduced on the same 729-track frozen evaluation set.
- Across seeds 42 / 7 / 13, three-window `laion/larger_clap_music_and_speech` clearly beat baseline DSP and enhanced DSP. Mean metrics were 0.656 accuracy, 0.628 balanced accuracy, and 0.626 macro F1.
- `laion/larger_clap_general` also passed the checkpoint sanity check and led the seed-42 comparison at 0.669 accuracy, 0.643 balanced accuracy, and 0.643 macro F1, but has not yet received the same three-seed confirmation.
- `laion/larger_clap_music` produced degenerate embeddings and is not an acceptable default without an explicit passing sanity preflight.
- Three-window aggregation at deterministic 20% / 50% / 80% positions is the proven representation choice.

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

- requires `--clap-revision` for CLAP runs;
- loads both processor and model at that exact revision;
- runs a three-signal synthetic-audio checkpoint sanity preflight before library feature extraction;
- preserves normalized three-window embedding averaging;
- isolates cache entries by checkpoint revision and implementation version;
- records checkpoint/runtime provenance in the experiment feature spec;
- provisionally defaults to `laion/larger_clap_music_and_speech`, the checkpoint already confirmed across three seeds.

Synthetic tests require no Torch, Transformers, network access, music files, Serato data, or external drives.

## Pending model-selection decision

Do not promote `laion/larger_clap_general` to the default solely from the single seed-42 comparison. If seed 7 and 13 reruns preserve its advantage, update the provisional default and pin the exact winning snapshot revision. Otherwise retain the already three-seed-confirmed `music_and_speech` checkpoint.

No external evidence collection, tag writes, music moves, Serato changes, taxonomy changes, or learner-assisted bulk tagging are authorized by this follow-up.
