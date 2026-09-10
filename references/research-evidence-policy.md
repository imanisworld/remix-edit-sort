# Research evidence policy

Use this policy for release years, artist/title cleanup, event/date reconciliation, genre evidence, and other external lookups.

## Search execution

- Batch independent lookups in one search request when the search tool supports it.
- Do not serialize unrelated lookups one by one.
- For obscure/local/DJ-pool material that is unlikely to have a public release page, make one well-formed search attempt. If no credible evidence surfaces, record it as `unconfirmable` and stop. Do not burn repeated searches trying to force an answer.
- Never convert absence of search results into a guessed fact.

## Evidence grades

Every externally researched value must carry one of these statuses:

- `confirmed_primary` — official artist/label/store/catalog/event source directly supports the value.
- `confirmed_secondary` — credible independent source supports the value when a primary source is unavailable.
- `local_evidence` — filename, folder, Serato crate/history, embedded metadata, or another local artifact supports the value.
- `inference` — evidence supports a conclusion but does not explicitly state it. Must remain reviewable.
- `unconfirmable` — a reasonable search was attempted and no credible source established the value.
- `unknown` — not researched or insufficient evidence.

`memory` is not an evidence grade. A remembered fact can seed a search, but it cannot authorize a tag write.

## Paper trail

Confirmed values must be reusable. Record them in a small reference registry rather than researching the same item again later. For release years use `references/year-evidence.json`.

Store years are not automatically original-release years. Route store hits through
`scripts/guard_reissue_years.py`: reissue/compilation/live title markers, a store year later
than the embedded year, or disagreement among stores must remain `unknown` with no selected
year until reviewed. Preserve the store candidates and URLs even when the guard holds them.

Each record should contain:

- stable lookup key;
- artist/title or other subject;
- proposed value;
- evidence grade;
- source type;
- source URL when web-derived;
- human-readable evidence note;
- date checked.

A value may have multiple sources. Preserve them rather than replacing prior evidence.

## Apply boundary

Research produces proposals only. Before writing Genre, Year, Grouping, Artist, Title, or version labels:

1. generate the proposed label/value breakdown;
2. show counts and unresolved items;
3. review ambiguous splits explicitly;
4. obtain approval;
5. apply through the verified mutation engine only.

This review is required for metadata-only passes too; a tags-only run is still a mutation.
