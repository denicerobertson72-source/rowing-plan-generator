# Codex update prompt — upgrade v0.1 to v0.2

Update the existing Rowing Plan Generator implementation to use the v0.2 **Multi-Duration Rowing Power Profile**.

## Primary change

Replace all `profile_only` treatment of short-, 60-second-, and 30-minute rate-capped tests with active, transparent planning behavior defined in `docs/POWER_PROFILE_SPEC.md`.

The tests must affect plans through session-specific power anchors, plausibility checks, goal-relevant session selection, and longitudinal comparison. They must not produce an unlicensed predicted 2k or silently redefine LT1/LT2.

## Required code changes

1. Add `rowing_plan/power_profile.py`.
2. Implement models matching `schemas/power_profile.schema.json`.
3. Update athlete input models to support `multi_duration_power_tests` and protocol metadata.
4. Preserve legacy fields only for migration; convert them to the new structure on load.
5. Remove `profile_only` from generated outputs and intensity method enums.
6. Add session fields for `power_target_method`, source anchor, target watts, equivalent split, confidence, and assumptions.
7. Add a feature-flagged ratio classifier. It must remain inactive when no explicit reference bands are configured.
8. Add longitudinal comparison for two or more valid batteries.
9. Add a Power Profile UI panel and workbook sheet.
10. Add all v0.2 tests from `tests/ACCEPTANCE_TESTS.md`.

## Migration rules

Map legacy fields as follows:

- `seven_stroke_peak_watts` → `multi_duration_power_tests.short_peak.value_watts`, protocol `seven_stroke`;
- `sixty_second_avg_watts` → `multi_duration_power_tests.one_minute.value_watts`;
- `thirty_minute_r20_avg_watts` → `multi_duration_power_tests.rate_capped_sustained.value_watts`, duration 1800 seconds, rate cap 20.

Do not discard legacy values. Add a migration warning if test dates or protocol metadata are absent.

## Planning behavior

Use the measured values as follows:

- very short PP sessions may use the short peak test as an anchor;
- 30–120 second AN sessions may use the 60-second result as an anchor;
- rate-capped sustained sessions may use the 30-minute result as a reference and plausibility check;
- low-intensity UT band boundaries still come from measured thresholds, coach bands, 2k configuration, or HRR/RPE fallback;
- when no 2k or thresholds exist, do not pretend that the 30-minute test precisely identifies UT2 or LT1;
- explain all profile-driven changes in the plan.

## Rights and claims

Use the public feature name **Multi-Duration Rowing Power Profile**. Use original wording. Do not mention a third-party coach in the user-facing application unless separate written permission is supplied. Do not copy the source spreadsheet or exact formula.

## Completion criteria

The update is complete only when:

- JSON schemas validate;
- the sample athlete produces the expected ratios and active power anchors;
- no predicted 2k is generated;
- locked weeks remain unchanged after a new battery is entered;
- future weeks show a change summary;
- the workbook contains a Power Profile sheet;
- all v0.2 acceptance tests pass.
