# Acceptance tests — v0.2

## A. Conversion tests

1. A 2k time of 8:00 produces a 2:00/500m split and approximately 202.5 W.
2. Converting that watt value back to split returns 2:00/500m within rounding tolerance.
3. Invalid zero or negative watts create validation errors.

## B. Intensity hierarchy

1. Measured LT1/LT2 values override all fallback calculations.
2. Coach-defined bands override 2k and HRR defaults.
3. A 2k profile is not used when disabled unless the user selects it.
4. The sample athlete receives low-confidence provisional low-intensity guidance because no 2k or thresholds are available.
5. The Multi-Duration Rowing Power Profile does not silently create LT1/LT2 values.

## C. Multi-Duration Rowing Power Profile

Using the sample athlete:

1. `one_minute_retention` equals `220 / 287` within floating-point tolerance.
2. `rate_capped_endurance_retention` equals `143.8 / 220`.
3. `sustained_to_peak_ratio` equals `143.8 / 287`.
4. Status is `partial` or `complete` according to the implemented completeness rule, but it is usable for active anchors.
5. A PP anchor is created from the 287 W short peak result.
6. An AN anchor is created from the 220 W one-minute result.
7. A rate-capped sustained reference is created from 143.8 W.
8. No predicted 2k watts or time is created.
9. Default `anchors_only` mode creates no population weakness label.
10. The provisional low-intensity watt rule remains disabled.
11. HRR/RPE remains the primary low-intensity control.
12. A warning is produced when drag factor or protocol metadata required for comparison is missing.
13. If short peak ≤ one-minute watts or one-minute ≤ sustained watts, the app requests confirmation and suppresses automatic modifiers.
14. Every anchor includes source test, formula/config percentage, confidence, and assumptions.
15. A generated PP target may not exceed short peak by default.
16. A generated AN target may not exceed the short peak ceiling.

## D. Optional classifier

1. The ratio classifier is disabled when `reference_bands` is null.
2. Injecting a test reference set produces deterministic domain flags.
3. Classifier output records reference-set ID, version, population, and uncertainty.
4. A planning modifier cannot shift more quality minutes than the configured maximum.
5. A modifier cannot create a third hard rowing session or violate taper rules.

## E. Longitudinal profile

1. Add a second comparable battery and calculate change for all available measures.
2. Changes below the configured noise threshold are labeled inconclusive/stable, not meaningful improvement or decline.
3. A goal-relevant decline may suggest at most one added domain exposure in the next block.
4. The user sees the reason for the suggestion.
5. Locked weeks remain byte-for-byte unchanged.

## F. Sample-athlete schedule

1. No rowing is scheduled on Monday or Friday.
2. Monday and Friday may contain heavy lifting plus treadmill/elliptical UT2.
3. Wednesday is the fixed coached on-water row unless it is a race/travel day.
4. Saturday contains no training.
5. Sunday is normally a longer low-intensity row and is not the routine maximal session.
6. Tuesday is the preferred primary quality-row slot.
7. Thursday is optional or low priority and is the first session removed when weekly load is reduced.
8. September 26 is C priority with minimal reduction.
9. October 17 is B priority with moderate reduction.
10. November 7–8 is one A event with two race days and three expected races.
11. A taper is larger than B, and B is larger than C.
12. No ordinary workout is scheduled on race days.
13. A warning appears if two hard rows plus three heavy lifting days are forced.

## G. Session-library tests

1. A head-race build week can select HEAD or TR templates.
2. A 1k sprint build can select SPRINT, AN, or PP templates.
3. A 50-minute UT2 slot returns only templates allowing 50 minutes.
4. On-water templates do not require watts.
5. Every selected template has a source-basis ID and `rights_status=original_app_template`.
6. No session text is generated outside approved template fields.
7. Profile-aware erg sessions record a target method and source anchor.

## H. Workbook tests

1. Export creates all required sheets, including POWER PROFILE.
2. Dates are true spreadsheet dates.
3. Source URLs are plain text or hyperlinks; no abstracts are copied.
4. Workbook contains no external links to source workbooks.
5. Workbook opens with openpyxl after save.
6. No formula cell contains a standard Excel error.
7. Filename is sanitized and ends in `.xlsx`.
8. The Power Profile sheet states that ratios are descriptive and no predicted 2k was generated.
9. Algorithm and config versions appear in the workbook.

## I. Regeneration tests

1. Lock the first two weeks, add a new 2k or power-profile battery, and regenerate.
2. Locked weeks are byte-for-byte unchanged in plan JSON.
3. Future guidance and erg targets update where applicable.
4. Fixed races, lifting days, Wednesday coaching, and Saturday rest remain unchanged.
5. The app displays a change summary identifying test inputs and sessions affected.
