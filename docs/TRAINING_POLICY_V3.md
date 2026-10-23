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
| dedicated UT2 / long aerobic starting credit | evidence-informed heuristic | 90 / 75 minutes | existing app policy | athlete capacity may reduce either starting magnitude | coach | aerobic need generation |
| AT/TR/AN/PP are categories, not protocols | evidence-informed heuristic | bounded categories | S014–S016, S020 | avoids copied universal exposure protocols | coach | quality-need generation |
| quality starting credit | evidence-informed heuristic | 35 minutes | existing app policy | phase and athlete response may reduce or defer it | coach | quality-need generation |
| concurrent work is individualized | research constrained | account for recovery/interference | S017 | strength/endurance interaction is contextual | coach | strength need generation |
| rolling 14-day strength target | evidence-informed heuristic | target 4, minimum 3 | S017; existing app policy | explicit app default, not a biological law | coach | strength dose validation |
| race excludes new hard work | evidence-informed heuristic | quality/strength protected after a race | S009, S019 | recovery protects against automatic hard additions | coach | recovery constraints |
| race-priority recovery span | evidence-informed heuristic | A/B/C spans are configurable | existing app policy | numerical spans are not universal prescriptions | coach | recovery constraints |
| reduce volume; retain useful intensity/frequency | research constrained | taper principle | S009 | taper does not mean stopping training | coach | taper need generation |
| exact taper windows | evidence-informed heuristic | A/B/C configurable windows | existing app policy | exact timing remains context-specific | coach | taper configuration |
| taper volume multiplier | evidence-informed heuristic | 0.6 starting multiplier | existing app policy | no universal percentage is claimed | coach | undated taper needs |
| race stress is counted once | software implementation choice | normalized one-record stress | internal `scheduler_v2:*` | protects de-duplication, not physiology | no | history normalization |
| no-next-day quality/strength | software implementation choice | continuous implementation guard | `scheduler_v2:*` | current guard is not a physiological duration claim | coach | candidate validation |
| 2K is an integrated anchor | research constrained | actual 2K remains primary | S012, S013, S026, S029 | integrated rowing-performance signal | athlete test update | progression inputs |
| short power is supplemental | research constrained | does not replace 2K | S021–S023, S026–S028 | refines within-athlete profile | athlete test update | progression inputs |
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
- The policy has no TrainingNeed generation, placement, solver, routing,
  PlanVersion, persistence, or UI effect yet.

## Audit contract

`validate_training_policy` rejects a research rule without a registered source,
software rules presented as research, non-preference rules that prescribe a
named weekday, numeric operational rules without a rationale/classification,
and duplicate keys in a version.  `TrainingPolicy.to_dict()` sorts rules within
each section so snapshots are deterministic regardless of construction order.
