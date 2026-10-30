# Training policy v3

`rowing_plan.training_policy.DEFAULT_TRAINING_POLICY` is the auditable policy
vocabulary for the replacement planner.  It is descriptive only: no live V2
route, placement rule, session generator, or persistence path consumes it in
this milestone.

Every rule has a version, class, rationale, overrideability, and source IDs.
The source IDs below are resolved against `sources/source_register.csv`; an
`scheduler_v2:*` label is an internal implementation provenance label, not a
research citation.

| Rule | Class | Value / range | Source(s) | Rationale | Overrideable? | Used by future layer |
| --- | --- | --- | --- | --- | --- | --- |
| generic bands are not measured thresholds | research constrained | configuration is not an athlete test | S020 | avoids treating generic bands as measured thresholds | coach | phase/dose interpretation |
| completed work drives history | research constrained | only completed work becomes completed load | S018 | separates prescription from actual physiological credit | no | state reduction |
| low intensity remains predominant | evidence-informed heuristic | no universal intensity ratio | S010, S011 | distribution varies by athlete and context | coach | multi-week dose validation |
| ordinary need horizon | evidence-informed heuristic | 14 continuous days | existing app policy | horizon is not a display-week duty | coach | undated need windows |
| load target range | evidence-informed heuristic | 80% minimum / 110% maximum of individualized target | S010 | range avoids a brittle universal minute prescription | coach | continuous 7/14-day load targets |
| low-intensity minimum | evidence-informed heuristic | 70% of target as an app starting range | S010, S011 | protects predominant low intensity without claiming a universal 80/20 rule | coach | residual aerobic generation |
| progression guard | evidence-informed heuristic | 15% increase; 65% baseline floor | S010 | recent load tempers abrupt change while one low week does not erase baseline | coach | target derivation |
| residual aerobic split | software implementation choice | 30-minute minimum / 60-minute preferred exposure | internal implementation | converts a meaningful continuous residual into placeable work without a weekly count | coach | residual aerobic generation |
| baseline fallback | evidence-informed heuristic | experience-specific app defaults only if profile has no baseline | existing app policy | fallback is explicitly not a research-derived universal volume | coach | target derivation |
| continuous distribution target | software implementation choice | dose-derived exposure/gap preference | internal implementation | distinguishes avoidable clustering from justified recovery without a weekly stencil | coach | continuous placement |
| taper frequency retention | research constrained | reduce volume while retaining useful frequency where recovery permits | S009 | a taper is not automatically a cessation of movement | coach | V3 taper sequence interpretation |
| second unexplained taper OFF day | evidence-informed heuristic | strong soft penalty outside final 48h | internal implementation | favors low-cost continuity only when recovery, authority, availability, and dose allow it | coach | V3 continuous placement |
| taper low-intensity partition | evidence-informed heuristic | partition an approved generic residual into catalog-feasible physical exposures | S009 | preserves movement frequency without increasing the approved dose | coach | V3 taper redistribution |
| partition search and tie-break | software implementation choice | catalog minima, bounded deterministic search, credit-conservation validation | internal implementation | execution mechanics only; not a physiological prescription | no | V3 taper redistribution |
| dedicated UT2 / long aerobic starting credit | evidence-informed heuristic | 90 / 75 minutes | existing app policy | athlete capacity may reduce either starting magnitude | coach | aerobic need generation |
| AT/TR/AN/PP are categories, not protocols | evidence-informed heuristic | bounded categories | S014–S016, S020 | avoids copied universal exposure protocols | coach | quality-need generation |
| quality starting credit | evidence-informed heuristic | 35 minutes | existing app policy | phase and athlete response may reduce or defer it | coach | quality-need generation |
| concurrent work is individualized | research constrained | account for recovery/interference | S017 | strength/endurance interaction is contextual | coach | strength need generation |
| rolling 14-day strength target | evidence-informed heuristic | target 4, minimum 3 | S017; existing app policy | explicit app default, not a biological law | coach | strength dose validation |
| taper strength continuity | evidence-informed heuristic | target 1, minimum 0 | S017 | taper may retain reduced strength exposure; exact count is not universal | coach | strength need generation |
| race excludes new hard work | evidence-informed heuristic | quality/strength protected after a race | S009, S019 | recovery protects against automatic hard additions | coach | recovery constraints |
| race-priority recovery span | evidence-informed heuristic | A/B/C spans are configurable | existing app policy | numerical spans are not universal prescriptions | coach | recovery constraints |
| soft race-recovery transition | evidence-informed heuristic | A/B/C configurable spans; 0.5 reduced-credit suggestion | existing app policy | lower-load transition remains distinct from hard exclusion | coach | sequence constraints |
| multiple-race additional recovery | evidence-informed heuristic | one additional day | existing app policy | repeated race load can extend the recovery consequence | coach | sequence constraints |
| reduce volume; retain useful intensity/frequency | research constrained | taper principle | S009 | taper does not mean stopping training | coach | taper need generation |
| exact taper windows | evidence-informed heuristic | A/B/C configurable windows | existing app policy | exact timing remains context-specific | coach | taper configuration |
| taper volume multiplier | evidence-informed heuristic | 0.6 starting multiplier | existing app policy | no universal percentage is claimed | coach | undated taper needs |
| race stress is counted once | software implementation choice | normalized one-record stress | internal `scheduler_v2:*` | protects de-duplication, not physiology | no | history normalization |
| no-next-day quality/strength | software implementation choice | continuous implementation guard | `scheduler_v2:*` | current guard is not a physiological duration claim | coach | candidate validation |
| 2K is an integrated anchor | research constrained | actual 2K remains primary | S012, S013, S026, S029 | integrated rowing-performance signal | athlete test update | progression inputs |
| short power is supplemental | research constrained | does not replace 2K | S021–S023, S026–S028 | refines within-athlete profile | athlete test update | progression inputs |
| athlete-relative development focus | evidence-informed heuristic | valid 2K, 30R20, 60-second and 7-stroke relationships only | S030, S021–S023, S026–S028 | ranks compatible workout form using the athlete's own tests; it is not a universal weakness classifier | coach | V3 selector only |
| individualized coaching emphasis | coach or athlete preference | explicit focus may override heuristic focus | S030 | documented coaching principle supports individualized emphasis, not proprietary algorithms | coach or athlete | V3 selector only |
| availability, commitments, bands, and user fixes | coach or athlete preference | athlete-specific | athlete or coach input | authority/preferences are outside physiology | athlete or coach | constraint interpretation |
| tie breaking, search width, serialization | software implementation choice | deterministic execution | internal implementation | execution property only | no | solver/audit infrastructure |

## Deliberate boundaries

- There is no default Monday–Sunday physiology, required weekly OFF day, or
  athlete-specific calendar embedded in policy.
- Exact dose counts, exposure counts, recovery spans, and taper lengths that
  are not directly established by a registered source remain heuristics or
  preferences.
- No sex, menstrual-cycle, contraception, menopause, or age multiplier is
  automatically applied without athlete-specific evidence.
- Load targets are continuous decision budgets: each combines a trailing
  completed window with the following same-length prospective interval. They
  are not a pure future-only quota and never infer Monday–Sunday identity.
- Known coaching commitments may count toward total rowing envelope when their
  duration is known, but do not receive dedicated-UT2 or quality credit unless
  their actual content supports it.
- When taper and an active recovery constraint overlap, they modify the same
  volume budget non-compoundingly: the stronger applicable reduction wins.
- Distribution targets are soft chronological preferences derived from dose and
  practical session capacity. They do not prescribe named weekdays or a fixed
  sessions-per-week template.
- Taper frequency retention may repartition only residual low-intensity credit
  whose provenance explicitly identifies it as a load top-up. Categorical
  dedicated-UT2 credit is not silently converted to UT3. Partitioned children
  must conserve their parent's total credit and remain inside every applicable
  rolling load maximum.
- A second consecutive empty available taper date is a soft sequence signal,
  not a universal rest-day prohibition. Recovery, fixed rest, authority,
  unavailability, high load, and the final 48-hour race window may all justify
  leaving it empty.
- V3's performance limiter is a versioned app heuristic based only on valid
  within-athlete relationships. It does not predict a 2K, infer lactate
  threshold from 30R20, or apply population/age/sex labels. Missing evidence
  yields `unknown`, not an invented limiter.

## Audit contract

`validate_training_policy` rejects a research rule without a registered source,
software rules presented as research, non-preference rules that prescribe a
named weekday, numeric operational rules without a rationale/classification,
and duplicate keys in a version.  `TrainingPolicy.to_dict()` sorts rules within
each section so snapshots are deterministic regardless of construction order.
