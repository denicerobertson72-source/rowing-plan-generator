# Current session-library audit

Date: 2026-09-03. This audit covers `data/session_library.json` before the
Step 3 archetype catalog. The existing 25 original app templates are retained
and continue to be the only library consumed by `session_selector.py`.

| Area | Existing templates | Effective structural choice | Provenance |
| --- | --- | --- | --- |
| UT3 / recovery | REC-01, TECH-01, RACE-REC-01 | Three broad options; REC-01 and RACE-REC-01 overlap in easy-row purpose but differ by race-recovery tag. | S009, S010, S018, S019 |
| UT2 | UT2-01, UT2-02, UT2-03 | Three interval/rate-control options; no continuous, progressive-duration, or novice-specific option. | S010, S011, S018 |
| UT1 | UT1-01, UT1-02 | Two repeat lengths only. | S010, S020 |
| AT | AT-01, AT-02 | Two structures; both are short/medium repeat formats. | S010, S019, S020 |
| TR | TR-01..03 | Three options, but only one long race-rhythm option. | S014, S015, S019 |
| AN | AN-01..03 | Three duration classes; no eligibility/recovery metadata. | S016 |
| PP | PP-01 | One effective option. | S012, S013 |
| Head race | HEAD-01..04 | Four useful but partly overlapping rate/rhythm structures. | S009, S010, S015 |
| Sprint | SPRINT-01..03 | Three useful sprint structures. | S012, S013, S015, S016 |
| Strength / alternate cardio | XL-UT2-01 | One alternate-cardio option; strength is generated directly by the scheduler. | S017 |

## Duplicate and coverage findings

- No byte-identical templates were found. REC-01 and RACE-REC-01 are close in
  wording and purpose, but their phase tags make the distinction meaningful.
- The selector's deterministic first-match behavior means many categories have
  only one *effective* option for a given band, duration, phase and mode.
  PP has one option outright; UT1 and AT have only two each.
- Current records contain no explicit session role, athlete eligibility,
  parametric work/recovery range, preference-fit matrix, high-load recovery
  metadata, or original-wording flag.
- Existing templates remain untouched. `rowing_plan/session_archetypes.py` is
  a separate, non-selected Step 3 library for later planning work.
