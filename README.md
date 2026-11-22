# Consequence Lab

Consequence Lab is an experimental platform for investigating the formation,
persistence and causal influence of agent-generated structures. It records what
agents observe, what they return, what they change and which earlier artifacts or
messages reach later decisions. Rooms is its first substantive application.

This source snapshot is prepared for BlueDot review and prospective research
collaboration. It derives from original source commit
`fe4017fceae41940450953c39ef559159f6af7d5`, without importing its Git history.
[Publication provenance](PUBLICATION_PROVENANCE.json) records file correspondence,
exclusions and packaging changes. The original scientific identities remain
unchanged in the separately retained research repository.

## Research and evidence

The motivating questions concern intermediate desired states generated during
goal pursuit, their persistence, and whether they constrain subsequent decisions.
These are hypotheses, not established findings about endogenous agency.

Four short autonomous Rooms trajectories provide preliminary observations. The
later mixed-horizon environment has extensive offline policy and engineering
validation, but no autonomous LLM evaluation in the evidence covered by this
snapshot. Better individual planning helps in the tested controls; strong gains
from learned forecasting, negotiation or coalitions remain unestablished.

Start with the [scientific status](docs/SCIENTIFIC_STATUS.md),
[sprint executive summary](docs/sprint-2026-11/EXECUTIVE_SUMMARY.md) and
[full sprint report](docs/sprint-2026-11/REPORT.md). Historical findings are dated;
new snapshot tests validate software, not those empirical claims.

## Architecture

The reusable kernel includes agent execution, model interfaces, bounded context,
versioned artifacts, private messaging, complete external interaction records,
checkpoints, auditing and restricted counterfactual branches. `World` and
`ScheduledWorld` separate domain rules from execution. Substantive validation is
concentrated in scheduling; a small counter-world test checks contract reuse.

[Architecture](docs/ARCHITECTURE.md) describes tested boundaries. Full semantic
representation-family interventions across artifacts, messages and retained
context are not implemented. Records do not contain unreturned model reasoning
or activations.

## Install and run offline tests

Python 3.12 is the preparation validation environment. The core requires Python
3.11 or later; this snapshot has not been freshly tested on every supported
Python version.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[provider,inspect,calibration]'
EIG_REQUIRE_PROVIDER=1 EIG_REQUIRE_INSPECT=1 PYTHONPATH=src:. \
  .venv/bin/python -m unittest discover -s tests -v
```

These tests use authored policies, mocked provider responses and local fixtures.
They require no API key. Dependency installation needs internet access, but the
test protocol makes no paid model calls. Fresh results and their scope appear in
[SNAPSHOT_VALIDATION.json](SNAPSHOT_VALIDATION.json).

A small mock demonstration is available through the existing CLI:

```bash
.venv/bin/consequence-mock run --agents 3 --decisions 12 --output runs/demo
.venv/bin/consequence-mock verify runs/demo
```

Use a new output directory. Generated records belong to the local run, not this
publication. The demonstration is software validation, not an agent-performance
benchmark.

## Included environments and tools

- `src/consequence_lab/`: execution infrastructure and calendar/Rooms worlds.
- `src/consequence_lab_mixed/`: separately versioned mixed-horizon prototype.
- `tests/`: complete exported regression suite, with four test files adapted only
  to read relocated legacy workflow fixtures.
- `experiments/`: selected synthetic manifests, neutral instructions and authored
  offline results used as regression fixtures.
- `tools/`: source for analysis, domain checking, exports and historical launch
  guards. Source-pinned historical paid launchers are not a supported launch path
  for this new publication identity.

The original workflow files are inert fixtures in
`tests/fixtures/legacy-workflows/`. There are no active Actions workflows and no
repository secrets in this snapshot. Publishing or reading the source grants no
authorization to launch an experiment.

## Reproducibility and reuse

The snapshot preserves scientific Python source bytes. Its new publication
commit will have a different identity from original research commits. Historical
archives, API journals, checkpoints, workflow logs and Git metadata are excluded.
Those omissions limit independent reconstruction of historical experiments;
they do not prevent local software testing. See
[reproducibility](docs/REPRODUCIBILITY.md) and
[publication notes](docs/PUBLICATION_NOTES.md).

Source availability and permissions: This repository is published for research evaluation. You may view, download, and run the software locally to evaluate the work. No permission is granted for redistribution, modification, or other uses without the author's authorization. All other rights reserved.

Dependency attribution is in [THIRD_PARTY.md](THIRD_PARTY.md).
