# Source correspondence and reproducibility

The export source is original commit
`fe4017fceae41940450953c39ef559159f6af7d5`, tree
`3283c032a0156153a8a1c848f23f6ebfe4c9f544`. The new publication will start with
its own root commit. No original Git objects, branch histories, tags or PR records
are included.

[PUBLICATION_PROVENANCE.json](../PUBLICATION_PROVENANCE.json) inventories every
original tracked path and its disposition. Included files have original Git blob
identities and source/publication SHA-256 values. Renames, changed documentation,
four test-path adaptations and added publication files are explicit. Excluded
file contents are not part of the package. The manifest does not publish original
author/committer email metadata.

## Scientific identities

| Component | Original identity |
|---|---|
| Final frozen Rooms v3 source | `237399fc30760af9012659c8fdb1ea658c51dea4` |
| Recovered original Rooms v4 checkpoint | `8b8eeeb143d3813f3626153d67f8078181d60aa2` |
| Mixed assurance-boundary hardening | `77030aabe798db170a95a9a80edfe72099fba3cd` |
| Final offline calibration report | `008d7302c5c438681f9008d39471c3c6f4a5396e` |
| Proposed pilot executable source | `ef4c4c8bd9255e6243bb4df4bcaa18012db8de99` |

The exported code is the selected later source tree, not a claim that every file
equals every historical revision. Frozen component identities and their original
records remain authoritative for those earlier experiments.

All `src/` Python files are copied unchanged from the export commit. Their current
implementation fingerprints are:

- Core: `e12ae5c55613673744b9f6d7c177e2ae24ebc21cd3af144caf2ecb04edea2e42`.
- Mixed: `610fdaac154d8f950ef4b2c129b11fac34074e285b2a81ecd79ed24b2c568587`.

These source fingerprints, a Git commit ID and a public package SHA-256 describe
different objects. A fresh publication commit is not a new scientific freeze.

## What readers can reproduce

Readers can inspect source, install dependencies, run the complete exported
regression suite, generate synthetic manifests, run authored offline controllers
and inspect newly generated local archives. Synthetic manifest/result fixtures
needed by tests are retained. Historical workflow configurations are inert test
fixtures; four tests change only the location from which they read those bytes.

Historical paid records, raw API/model journals, compact run archives, checkpoints,
workflow logs, private evidence branches and most internal handoff/launch packets
are excluded. Software tests do not independently re-establish the reported paid
outcome totals or all historical calibration results. Historical reconstruction
requires the correct original source and matching retained datasets through
separately approved access or a later curated data release.

Do not use source-pinned launchers by substituting a new commit ID or disabling
their guards. They deliberately target original scientific/operational identities.
No launch workflow or credential is supplied by this publication.

## Validation and integrity

The fresh validation report distinguishes current offline test results from
historical reports. It includes dependency versions and scope; raw local test
logs are not published. Root `SHA256SUMS` verifies publication file bytes, including
the provenance manifest. The manifest excludes itself from its embedded file
inventory to avoid a circular hash; the root checksum list covers it instead.

New model outputs are stochastic and the provider alias is not an immutable
weight snapshot. Exact logical reconstruction is a different claim from exact
neural reproduction. Full semantic-family causal interventions remain future
work even where artifact fork primitives are tested.
