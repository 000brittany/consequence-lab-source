# Consequence Lab architecture

Consequence Lab separates reusable agent execution and measurement from world
rules. The execution kernel preserves external decision evidence; each world
defines what actions mean and what task fulfillment requires.

## Execution and world contracts

`World` supplies validation, observations, resources, actions, task completion
and measurement. `ScheduledWorld` adds event progression, visible time and
decision opportunities. `Runner` selects actor context, reserves call resources,
records pending requests, saves returned responses/errors, validates proposals
and commits transitions. Registered factories restore supported worlds/backends.

Agent identity and histories can persist across tasks. The ordinary context
policy selects recent complete decision cycles and records evictions; it does
not automatically summarize history or retrieve old artifacts. The investigator
record retains information excluded from the current agent input.

The model interface supports authored mocks and reviewed Responses adapters.
Transport is explicit. Unknown usage retains conservative reservations;
unresolved request outcomes do not authorize silent duplicate requests.

## Artifacts and communication

Private/shared artifacts have access rules, revisions, reads and tombstones.
Private messages carry authenticated sender identity, delivery information and
quotas. Agents can use these facilities without being rewarded for their mere
use. A path marked private describes agent-level access, not confidentiality of
an exported research record.

Exposure records connect revisions/retrievals to selected inputs. Exact supplied
messages remain authoritative: earlier writes and copied text can expose content
beyond a current retrieval receipt. Exposure is not proof of attention or causal
use.

## Archives and auditing

Complete Runner records preserve supplied model inputs, returned exchanges,
observations, operations, artifact/message histories and state transitions.
Compact archives use exact patches, anchors and hash chains. Checkpoints,
portable export and byte-equal reconstruction support continuity checks.

The core auditor independently reconstructs histories and selected inputs, but
ordinary domain replay reuses production world rules. Separately authored domain
checks and a second compact decoder provide additional independence in selected
validations. Neither hashes nor replay certify scientific construct validity.

Fast authored-policy traces are different: they reconstruct domain trajectories
and observations, but do not snapshot optimizer internals or contain model
histories. No model was invoked in those controls.

## Counterfactual boundaries

Historical logical prefixes and allowlisted artifact-content interventions are
tested, including matched multi-file artifact forks. Supplied text can be changed
while unrelated world/objective state remains fixed. Authored read-sensitive
fixtures validate that wiring; they do not establish autonomous representation
effects.

Copies can survive in messages, old write arguments, selected context or peers'
files. Full semantic-family identification and transformations across those
locations require further work. Means substitution, controlled value reversal
and autonomous restoration/rescue assays are methodological proposals, not
completed scientific results. Model activations, unreturned reasoning and exact
stochastic neural replay are outside current records.

## Reuse beyond Rooms

A small two-actor counter-world test exercises execution, auditing and restoration
without changing the record schema. Substantive experiments remain concentrated
in calendar/resource scheduling. A different world needs its own observations,
privacy, actions, state, outcomes, event semantics, restoration registration and
independent domain tests. Markets or persistent organizational environments are
future applications, not implemented scientific demonstrations.
