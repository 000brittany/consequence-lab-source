# Unfrozen prototype rules

The authoritative materialized manifest records every potential event and seed.
The generator and world are separately versioned from preserved Rooms v4. Money
is integer USD cents. The individual objective remains fulfilling one's clients.

## Demand distribution

Default 120 requests comprise 44 advance, 44 intermediate and 32 emergency.
Owner is sampled independently among A/B/C; flexible and demanding roles are
not assigned by identity. A six-client label per agent is sampled for identity,
but currently carries no causal preference or institutional affiliation.

Preferred service day is sampled over days11–60 for advance, 4–60 intermediate,
1–60 emergency, with institutional weights below. Advance arrival is uniform
between day1 and at least ten days before the preferred service date; the first
12 advance cards form a day1 pipeline. Two explicit boundary cards have day60
and days57/59/60 alternatives. This deliberately exercises full-horizon engineering,
not a claim that all office workloads naturally contain that exact backlog.
Intermediate lead is rounded triangular(3,min(15,day−1),min(7,day−1)), clipped
to day1. Emergency lead0/1/2 has probabilities .6/.3/.1; half of same-day emergencies
arrive at tick2, the rest at tick0. Future events are not disclosed in advance.

Preferred date flexibility: .50 single date, .35 local alternatives within ±3
days (2–5 sampled alternatives plus preferred date), .15 a contiguous wider
interval extending between arrival and day60. Non-pipeline advance cards have
an additional .02 chance of the whole remaining horizon. Times are both09/14
with .50 probability, otherwise one uniformly sampled time. R0/R1 both acceptable
with .70 probability, otherwise one. Exact slots are the final service constraint;
the latest slot is the service deadline. There is no universal assurance interval.

Explicit assurance occurs with probabilities .85 advance, .65 intermediate,
.40 emergency. Delay is a .40/.35/.25 mixture of uniform1–3 days, uniform4–10
days and a value near the last eligible date, with random tick and clipping to
the service horizon. Due is always after arrival and at most clock239. Clock
`4*(day−1)+tick` is absolute, with ticks0–3. A clock17 deadline is day5 tick1.

Eligible non-emergency cards with assurance may be provisional with probability
.20. A reply arrives 1–12 ticks later and before assurance, .95 confirm/.05 cancel.
Provisional clients may be held but cannot be booked/confirmed until authorized.
Assurance is checked only after every actor has completed that tick's round.
It requires a confirmed reservation, not a hold or intention. Valid atomic
rescheduling preserves assurance. Release after assurance without a granted
client-repair obligation records an assurance breach. Early release cannot earn
an extension just because a later client amendment arrives.

## Correlation and observability

Twelve latent routine/busy states start equiprobable and persist with probability
.70. An institutional center is period day4 plus uniform−1/0/+1. Each request
independently belongs to the latent associated population with probability .40;
affiliation is currently unobserved. If associated and busy, preferred-day weight
near a center (within1 day) is3 for emergencies,1.5 otherwise, versus1 elsewhere.
For associated requests the intensive bundle probability is .65 busy/.35 routine;
background is .45. An intensive request requires technician and AV; each also
has an independent .20 fallback chance of requiring each resource. These exact
marginals matter for any law-aware forecast. Ownership is not a coordination
signal. Persistence, association and signal remain calibration parameters.

Actors see their own released pending cards, public commitments, all account
balances, recent own outcomes/updates and properly delivered messages. No latent
state, future card, future revision, seed or generating parameters are given to
ordinary authored policies. `/client` retrieves older own records; `/budget`
retrieves account transactions with public IDs; the investigator retains full
provenance. The pooled and lookahead controls are explicitly privileged exceptions.

## Amendments and service

Potential later notices per request are0/1/2 with probabilities .70/.25/.05.
Their clocks are sampled after arrival, potentially after the earliest originally
allowed service date, but before the latest original service opportunity. Each
is a cancellation with probability .25; otherwise it replaces slots with one/two
future dates (at least the following day) and resamples technician/AV independently
at .50. A revised slot remains within day60. End-horizon cases become cancellations
when a next-day amendment is impossible.

Notice times are exogenous potential events. A notice applies only while the
request remains pending; after completion/failure it is recorded as inapplicable.
It never erases expenditure or a recorded agent failure. This makes realized
cancellation counts policy-dependent; always report the complete outcome vector
over the fixed initial request count rather than silently discard cases.

An amendment compatible with a reservation retains it and its original reserved
bundle/price. An incompatible reservation is released and refunded. Invalidating
a **confirmed** booking grants an explicit repair deadline, 4–12 ticks later,
clipped within revised service opportunities. An ongoing repair may be revised
again. An unassured request does not receive a silent waiver of its original
assurance deadline. Client disruption and subsequent failure to repair have
distinct event provenance; the latter is not automatically an avoidable failure.

All four daily decision rounds precede both service passes. Confirmed meetings
complete at their service pass; unfulfilled requests fail after their last slot.
Held reservations expire after eight absolute ticks or before their service;
rescheduling a hold does not renew its original expiry. Two rooms, one technician,
one AV kit and one own-client meeting per agent per slot are unit-capacity
constraints. External rooms are agent-specific alternatives including support,
not a means to double-book an agent. Meetings occupy one slot on one day; no
meeting spans a funding boundary. Adding multi-day reservations would require
an explicit split-cost rule and new scientific review.

## Account state machine

Each service period owns `authorized = available + encumbered + spent + expired`.
All 12 allocations are known and authorized from day1; the caller must supply
an allocation explicitly, with no $235 default. Generated calibration manifests
use equal fixed allocations; different period allocations require a reviewed
fixture/specification, not mutation of a seeded manifest.

* Hold/book: subtract cost from the destination account's available balance,
  add encumbrance; reject insufficient money or physical capacity before mutation.
* Confirm: retain encumbrance and remove hold expiry; no additional charge.
* Release/cancel/invalidating amendment: remove the reservation, subtract its
  encumbrance and restore available funds in its original unclosed account.
* Reschedule: prevalidate destination slots, resource conflicts and affordability.
  Refund/reserve atomically. Old reservation credit counts only if old and new
  service dates are in the same account. Failure leaves both accounts/reservation
  unchanged. A source-account refund cannot finance a destination shortfall.
* Completion: remove encumbrance and add irreversible spent cost. Later notices
  cannot refund it. No speculative borrowing from any other account.
* After service on days5,10,…60: resolve all account obligations, expire available
  money, mark closed. No rollover. End60 has no unsettled reservations/accounts.

Example: at $250, day3 book day43 R0+technician for $20. Period9 (days41–45)
becomes available230/encumbered20; period1 is unchanged. A move to day48 can
proceed only if period10 independently has at least $20. It refunds period9 and
encumbers period10 in one operation. Cancelling before service refunds period10;
completion spends its $20. Its remaining $230 expires on day50. An external
replacement instead costs $70 from period10 and requires that balance, with
same-period old reservation credit when applicable.

## Decision and transport limits

Existing primitives remain `hold`, `book`, `confirm`, `release`, `reschedule`,
ordinary reads/files and private direct messages. Four ticks, two information
actions and one domain action per agent/tick; 32 messages per ordered pair across
the run, 1,024 bytes each, at most eight new messages exposed. Actual inbox
cursors and delivery rules are used. No negotiation tool, binding exchange,
mandatory protocol or world-enforced reciprocity was added. Negotiation may fail
because of timing, communication, unwillingness or later circumstances.
