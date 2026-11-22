# Consequence Lab: final sprint research and engineering report

> Public source-snapshot note (November 22, 2026): the research evidence
> cutoff below remains November 8. Raw historical archives are excluded
> from this release. Source correspondence is documented in
> [PUBLICATION_PROVENANCE.json](../../PUBLICATION_PROVENANCE.json).
> The accompanying reproducibility appendix is publication-specific.

**Evidence cutoff: November 8, 2026.** This synthesis reviews published source
through `834501b563975a25a1dd8ec886e1024d81d80bc3`, existing audit records and
GitHub run metadata. It does not report a new experiment. Exact identities,
verification levels and retrieval instructions appear in the
[reproducibility appendix](REPRODUCIBILITY.md); a shorter
[executive summary](EXECUTIVE_SUMMARY.md) accompanies it.

## 1. Scientific purpose and evidentiary scope

Consequence Lab is a reusable experimental platform for investigating how
agent-generated structures form, persist and influence subsequent action across
task environments. Rooms, an office resource-allocation environment, is the first
substantive application. Scheduling provides inspectable opportunities for
instrumental organization; it does not define the platform's research scope.
The focus is change in agent-level organization under a fixed model configuration,
rather than changes to model weights.

The motivating Consequential Relationality hypothesis concerns transitions from
pursuing assigned outcomes to constructing intermediate desired states and,
potentially, maintaining structures that regulate later decisions. Such structures
might be expressed in plans, artifacts, communication or relationships among
agents. An external record could be a functional part of a decision process;
its mere existence does not make it a representation or an autonomous regulator.
Interactions might stabilize organizational patterns, but neither that transition
nor endogenous agency is established or expected in the initial pilot.

Four levels must remain separate:

| Level | Current meaning |
|---|---|
| Theoretical hypothesis | Instrumental representations can arise during goal pursuit; some may acquire persistent or partly independent regulatory influence. |
| Operational proposal | Identify prospective intermediate desired states, trace their reuse, and compare matched continuations after controlled changes. |
| Implemented measurement | Exact supplied inputs and returned exchanges; artifact/message provenance; state reconstruction; restricted artifact interventions and branching. |
| Empirical evidence | Small autonomous Rooms comparisons, extensive authored offline controls and engineering fixtures; no demonstrated autonomous regulatory representation. |

The project currently establishes experimental tractability more strongly than
any substantive theory of subgoals. No literature claims or citations are added
here: the broader theoretical framing and literature review remain in development.

## 2. Reusable architecture, assessed against source and tests

The kernel in `src/consequence_lab` separates agent identity, task assignment,
history and world state. `World` specifies validation, observations, resources,
actions, task completion and measurement. `ScheduledWorld` adds environment
events, visible time, tools and decision-opportunity semantics. `Runner` selects
context, records a pending request before transport, persists returned responses
or errors before domain dispatch, applies transactional transitions and maintains
checkpoints and accounting. Scripted fixtures and reviewed Responses backends
share this execution path; model profiles and restoration factories are explicitly
registered, rather than unrestricted plug-ins.

| Component | Demonstrated capability | Boundary |
|---|---|---|
| Agent execution and task continuity | Persistent identities/history, task reassignment, normalized actions and nonfatal rejected operations; tests use 1, 3 and 8 mock actors. | These tests establish execution invariants, not agent competence or scaling of social behavior. |
| Context selection | Recent complete cycles, budget eviction, inclusion/exclusion records and exact messages/tools/request hashes. Investigator records retain the full history. | Selected context is intentionally incomplete. Input estimation is not a verified token bound; exposure does not establish attention or use. |
| Artifacts and communication | Agent-controlled private/shared files, revisions, reads, deletion and access controls; private messages with sender identity, visibility and quotas. | Permissions are configured rules. Direct-message integration and some continuation conventions are currently tied to scheduled Rooms worlds. |
| Provenance | Revision/source identities, retrieval receipts, exposure maps and exact model-facing inputs connect potential sources to decisions. | Receipts alone do not exhaust copied text in old writes, messages or quotations. Exact input is the authority for what was supplied. |
| Archival and restoration | Lossless compact patches, periodic full anchors, hash chains, complete indexes/checkpoints, portable export and byte-equal reconstruction. | Integrity is conditional on retaining the files and manifests. The lost original executor datasets illustrate the difference between reconstructibility and durable storage. |
| Auditing | Separate core consistency checks and source-domain replay; selected independent domain-rule checks and a separately written compact decoder. | General domain replay reuses the production world. A second independent simulator has not checked every rule in every trajectory. |
| Counterfactual branching | Historical logical prefixes, declared artifact changes, matched multi-file artifact forks and restored continuation. | Arbitrary message/context-family edits, model activations and deterministic neural replay are not supported. |

A genuine non-Rooms test in `tests/test_audit.py` supplies a two-actor
`CounterWorld`, increments separate counters, audits two mock decisions and
restores through a supplied world registry without changing record/checkpoint
formats. This supports contract reuse. Most substantive world tests, however,
cover calendar/resource scheduling and its Rooms descendants. The Inspect adapter
provides tested mock hosting, not universal live-model validation across domains.
An opt-in continuation policy named `rooms_v3_recoverable_v1`, explicit backend
registries and scheduled message integration are concrete residual couplings.

A substantially different world would need its own state schema, initialization,
observations/privacy, action tools, completion and measurement rules, event
ordering, restoration registration, source identity and independent reference
tests. A market would additionally require ownership, exchange, pricing,
information and payoff semantics. Existing execution, artifact and archive
infrastructure could be reused; economic or organizational scientific validity
would have to be established anew.

## 3. Inception and continuous sprint development

The researcher's account dates conceptualization to **Thursday, November 5,
2026**, immediately before the sprint, and first testing to **Friday, November
6**, the official opening day. The records support a continuous, rapidly developing
research effort across these days, but do not independently verify those exact
conceptual milestones. They also contain a testing-date discrepancy.

Dates below use EST (UTC−05:00); the appendix preserves exact UTC identities.
Commit timestamps date recorded changes, not necessarily when work began.

| Date | Verified record and interpretation |
|---|---|
| November 4, 22:39 | Initial repository commit `24b5e06`. Scaffolding predates the stated conception date; it does not independently establish the conceptual inception of the research. |
| November 5, morning | Commits introduce offline execution, calendar rules, versioned artifacts, checkpoints, audit and CI. Tag `pre-sprint-baseline-2026-11-05` identifies `6f72b2d`. GitHub CI run `42051962084` executes core unittest suites and the Inspect test successfully at about 10:08–10:09. |
| November 5, later | Integrity defects are reproduced and repaired; recurring/scheduled-world and provider work follows. Tag `pre-hackathon-baseline-2026-11-06` identifies `5a62670`. Early passing tests did not establish a mature or defect-free platform. |
| November 6 | Successful paid calibration run `42237954349` starts at 09:53 after an earlier unsuccessful credential launch. Autonomous experimentation and diagnostic refinement proceed. The repository's midnight bookkeeping cutoff is not independent evidence of the organizer's exact opening time. |
| November 7, evening | Frozen-source Rooms v3 comparisons complete through the established Actions pathway. Long-horizon successor commits `2626008` and `8b8eeeb` are recorded at 20:06 and 20:14; independent review follows. Some records fall on November 8 in UTC. |
| November 7, 23:41–November 8 | Isolated mixed-horizon prototype, assurance repair, stronger controls, discoverability/coalition calibration, durable evidence verification and neutral pilot preparation. Published preparation ends at `834501b` on November 8, 09:38. |

Thus “first testing on November 6” cannot describe the earliest automated tests
visible in GitHub. It may refer to a different research milestone, but that
interpretation is not established. The discrepancy should be clarified before a
paper; this report retains both the researcher account and server evidence.
The November 5 baseline is a provenance reference, not a way to backdate later
experiments, independent audits or longitudinal validation. Conversely, verified
pre-opening code and tests should not be attributed to the event itself.

## 4. Rooms as an experimental design case study

### Autonomous short-horizon observations

Four final 12-day Rooms v3 trajectories cross seeds 0/3 with medium/high reasoning
on frozen scientific source `237399f`. GitHub independently records all four runs
as successful. Behavioral totals below are the retained reported results;
this synthesis has not independently decoded the paid raw archives.

| Seed | Reasoning | Completed | Failed | Direct messages | Persistent files |
|---|---|---:|---:|---:|---:|
| 0 | Medium | 17 | 5 | 16 | 0 |
| 0 | High | 21 | 1 | 18 | 0 |
| 3 | Medium | 21 | 2 | 9 | 0 |
| 3 | High | 23 | 0 | 0 | 0 |

The seed-3 high run reportedly had only one rejected action. Completed-plus-failed
totals omit cancellations and should not be treated as the full request
denominator. With four trajectories, these are descriptive observations, not
a reliable estimate of a reasoning-treatment effect.

There is no compelling positive demonstration of durable agent-generated
regulatory representations. File absence does not imply absence of intermediate
goals: ordinary context, actions or messages may contain candidates. Neither a
systematic representation-family analysis nor causal autonomous interventions
established their presence or absence. Earlier protocol and credential problems
were engineering diagnostics, not scientific treatment effects.

Good fulfillment, including a successful almost message-free run, suggested the
short environment could often be handled as local scheduling. It offered limited
evidence that persistent external records or repeated accommodation were useful.
This motivated richer dependencies, rather than arbitrary rewards for file use
or deliberately hidden necessities.

### Longitudinal engineering and mixed-horizon redesign

The first v4 successor extended the horizon and archive capacity while retaining
short demand blocks. Its original validation bytes were lost during executor
migration. Independently reproduced **188-generation scheduling** and
**540-generation file-capacity** fixtures subsequently passed reconstruction,
portable export and separate domain checks. They are new engineering evidence,
not recovered original bytes or autonomous performance.

The separately versioned mixed prototype in `src/consequence_lab_mixed` retains
three agents and 120 requests: 44 advance, 44 intermediate and 32 emergency
requests. Requests can arrive on day 1 for service near day 60. They have
individualized assurance deadlines, service alternatives and resource needs.
Assurance means a confirmed reservation, not an indefinite hold; provisional
client information and later notifications remain distinct. Amendments and
cancellations can occur before actual service, including after an originally
permitted early service date. Agents see only legitimately released client
information; generated future events remain investigator-only until release.
An imperfect routine/busy institutional process supplies correlated demand;
state persistence, signal strength and associated-request proportion were varied
in offline discoverability checks, rather than assumed learnable from 60 days.

Twelve shared five-day accounts replace the old rollover cash pool. Advance
bookings encumber the destination service period; releases restore open-period
funds; cross-period rescheduling updates allocations atomically; service converts
encumbrance into irreversible spending; unused allocations expire. External
rental remains $70 including support, charged to the same account. Old $230/$235
rollover recommendations do not apply to this changed workload and accounting.

Long-range commitments now constrain later options; private flexibility can make
another agent's voluntary move useful; changes create repair opportunities.
Nevertheless, future client events are pre-generated and exogenous. Most effects
of past decisions operate through commitments, money, resource availability,
messages and artifacts—not endogenous changes in client trust, institutional
relationships or demand. Longer duration is not itself evidence of deep adaptive
organizational dynamics.

### Offline calibration and the limits of social opportunity

All mixed results below come from **researcher-authored offline policies**.
There has been **no autonomous mixed-horizon LLM evaluation**. The final study
includes 924 complete trajectories, with development seeds separate from 16
evaluation seeds, and funding of $200/$225/$250 per period. Instantaneous
assignment optimizers solve their subproblems; these are not hindsight-optimal
whole-trajectory or optimal causal policies.

| Period allocation | Reactive | Stronger individual | Three-way information | Information + accommodation | Privileged pooled information |
|---|---:|---:|---:|---:|---:|
| $200 | 95.31 | 98.75 | 98.94 | 98.81 | 99.81 |
| $225 | 99.31 | 102.31 | 102.06 | 102.13 | 102.50 |
| $250 | 102.63 | 104.69 | 104.69 | 104.75 | 105.31 |

Entries are mean completed clients out of 120 potential requests. Stronger
individual planning improves on reactive booking by 3.44/3.00/2.06 clients;
descriptive paired bootstrap intervals exclude zero. Information and
accommodation increments against matched stronger controls do not. Cancellation
counts can depend on service timing and are not silently discarded.

Social opportunities are operationally reachable but their incentive strength is
weak. At $225, two B+C profiles improve both members while reducing A's outcomes
against the selected independent policy. Both witnesses disappear against
alternative independent timing controls. No evaluated case makes both members
beat all four independent controls while the excluded agent loses against all
four. Repeated bidirectional help occurs in seven pair–seed cases, but none gives
both participants strictly more completions than the neither-accommodates arm.
These results do not establish reciprocal or mutually conditional coalitional
advantage, nor prove that better strategies cannot find it. Whole-profile
comparisons are not equilibrium or unilateral-deviation tests.

Communication has costs: at $250, the three-way information policy averages 141.4
messages and 704.4 implied decisions versus 563.3 for individual planning. Limits
are respected, but controller computation and working memory are free; additional
LLM reasoning, retrieval and file operations are not measured.

History-based learning is also unestablished. Within-trajectory comparisons of
public proxies, individual history, pooled history and a law-knowing approximation
do not demonstrate consistent prediction and scheduling improvement. Twelve
periods provide few transitions. The public proxy is weak and the law-knowing
approximation is not exact Bayesian inference. A perfect emergency-count feature
does not improve the tested policy. With a stronger planner, exact own request
cards seven days ahead improve completions by 0.75 at $250, with a descriptive
interval of [0.19, 1.38]. This privileged witness is not a learnable signal.
Current client affiliations are sampled per request, so stable relationship
learning cannot be inferred from their labels.

The recommendation of **$250** preserves alternatives under pressure, rather
than maximizing difficulty or completion. A $70 fallback consumes 28% of a
period's allocation. Lower budgets create more conditional financial blocks
without robust social gains; at $250 the selected planner still averages 8.25
failures and 7.06 cancellations. Physical capacity, current financial feasibility,
operational reachability and feasibility conditional on earlier decisions are
different quantities. No complete exact legal hindsight frontier or optimal
causal certificate establishes that every remaining failure is unavoidable.

## 5. Identifying candidates and testing their influence

Three properties are independent:

| Property | Distinction |
|---|---|
| Origin | Assigned requirements versus agent-generated intermediate desired states. Copying a client constraint is not generating a subgoal; adopting another agent's convention differs from inventing it. |
| Persistence | A transient proposal versus reuse or maintenance across separated decisions/tasks. A surviving file alone demonstrates storage, not continuing influence. |
| Regulatory independence | Useful instrumental guidance versus a possible orientation maintained partly independently of the original assignment. Causal influence alone does not demonstrate this independence. |

The observational protocol should identify prospective desired states distinct
from the terminal assignment, their stated or inferable means–end relation, and
later evidence of use, maintenance or decision gating. Each candidate needs an
exact span, actor, event/decision ID, revision, task linkage, state identity and
later supporting or contradicting evidence. Code copied requirements,
retrospective explanation, templates and repeated action patterns as alternative
explanations. Two independent coders and recorded disagreements are recommended;
no completed dual-coder analysis is claimed. Technical censoring and inaccessible
internal representations must not become negative psychological findings.

| Method | Tested implementation | Remaining scientific work |
|---|---|---|
| Exposure tracing | Reads/revisions, selected-input reconstruction, retrieval/source maps and eviction tests. | Establish whether supplied content actually guides a decision; exposure is not uptake. |
| Representation-family identification | Complete external records support inventory; matched two-file artifact forks are tested. | Identify semantic paraphrases/copies across files, messages, retained context and agents. No general full-family transformer is validated. |
| Semantic substitution | Declared artifact-content replacement and matched control rewrites preserve unrelated state. | Validate meaning-preserving and desired-state-changing treatments with factual/placebo controls. String editing is not semantic construct validation. |
| Means substitution | Existing scheduling actions allow alternative routes. | A controlled intervention preserving an end while altering its means is not yet a validated experiment. |
| Restoration | Revision history and artifact replacement support reinstatement primitives. | Run matched restoration/rescue continuations; no autonomous rescue result exists. |
| Reversal of instrumental value | Natural world events can change usefulness. | Controlled devaluation requires a separately reviewed world/objective treatment beyond the current artifact-only branch allowlist. |

Tests show that authored read-sensitive policies respond to changed artifacts and
that altered family text reaches subsequent fixture inputs. They validate causal
wiring, not autonomous representation effects. Copies may survive in old write
arguments, selected history, inboxes or peer artifacts; artifact-only removal is
not full-family ablation. Match access, rewrite counts, revisions and unrelated
facts, and use sham/no-touch controls. Preserve historical originals rather than
rewriting the evidence to manufacture a clean treatment.

Logical pre-decision state reconstruction is tested. Provider stochasticity,
model alias changes, hidden activations and unreturned reasoning prevent exact
neural replay. Future authorized studies need multiple matched continuations.
Persistence after devaluation could reflect uncertainty, beliefs, switching costs
or hysteresis; it would not by itself establish regulatory independence.

## 6. Findings, limitations and next milestones

| Evidentiary status | Conclusion |
|---|---|
| Autonomous observations | Four completed short Rooms comparisons, reported differences in fulfillment/message use and no persistent files; no positive causal subgoal demonstration. |
| Offline environmental/policy results | Long commitments and repair operations are feasible; better individual optimization helps; tested learning, negotiation, reciprocity and alliance gains are weak or unestablished. |
| Validated machinery | Checkpoints, exact logical reconstruction, compact archival/export, selected independent audits, exposure records and restricted artifact forks support inspectable experiments. |
| Proposed extensions | Full semantic-family interventions, regulatory-independence assays, stable institutional signals, markets and persistent organizational worlds remain unvalidated. |

Archive integrity has two distinct levels. Full Runner fixtures preserve provided
model-interface requests/responses, observations, actions, files, messages and
state histories. Fast offline policy traces preserve manifests, actions/results
and reconstructible domain observations/state, but do not snapshot optimizer
internals or complete model histories; no model existed in those runs. Neither
format recovers activations or unreturned provider reasoning. Lost originals
remain unrecoverable even though new reproductions are preserved and audited.

The minimum next engineering milestone is an **offline-rehearsed adaptation of
the existing GitHub Actions infrastructure**, followed by separate approval for
only the first 32 cumulative decisions. Existing paid v3 workflows already use
the repository API secret and pinned scientific source. There is no established
technical reason to require a new VM. The initial 32 decisions are practical for
a bounded hosted job; the full 60 days should use approved resumable chunks,
rather than assume one long job will finish.

Before launch, the adapted pathway must pin source and launch identities, prevent
duplicate starts/concurrent continuations, preserve pending requests and returned
responses durably before further calls, stop safely on uncertain request outcomes,
and retain cumulative spend/time/call limits across jobs. Independently download,
checksum, audit and restore a second durable copy after decision 32. End-of-job
upload alone is insufficient protection against abrupt executor loss. Actions
artifacts require retention management; a separate private evidence ref/copy can
provide durability beyond the job and artifact expiry. Existing v3 concurrency
and upload steps do not establish that this new pathway is already ready.

Accepted configuration is seed **470021**, 120 requests, **$250 per period**,
**GPT-6 Luna/high**, neutral instructions, **six recent cycles**, and a
**$20 cumulative nominal token-charge cap**. Source `ef4c4c8` specifies 2,160
calls, 10 active hours and a 60-second request timeout. The timeout may be short
for high reasoning; real latency is unmeasured and the current guard limits it.
A reviewed technical adjustment may be needed, without altering the scientific
condition or raising the cap. Hosted job limits, orderly interruption, unknown
usage accounting and maximum-output memory/storage still need operational checks.
Mock wire fixtures validate serialization and recovery paths, not live account
access, latency or cost distributions. No paid launch is authorized.

The subsequent scientific milestones are separate: observe a candidate
agent-generated intermediate desired state; establish persistence; demonstrate
causal influence under matched interventions; only then investigate relative
regulatory independence. The literature review and measured affordances should
guide future environment selection. Markets or continuing organizations may
offer richer feedback and relationships, but Rooms should neither be presumed
the final domain nor replaced merely to force the desired behavior.

This report changes no scientific rules, implements no features and launches no
experiment. Frozen Rooms v3, the reviewed v4 baseline, all retained evidence and
draft PR #14 remain preserved; the mixed scientific design remains unfrozen.
