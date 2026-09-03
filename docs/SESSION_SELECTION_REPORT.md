# Step 4 session-selection report

The debug payload on each newly generated rowing session contains
`session_role`, `candidate_scores`, `archetype_id`, concrete work/recovery
parameters, `session_fingerprint`, progression dimension, preference effect,
and athlete-facing selection reason.

The deterministic test season demonstrates this sequence:

| Week purpose | Assigned roles | Example selected structure | History/progression effect |
| --- | --- | --- | --- |
| General preparation | aerobic base, technique, long aerobic, aerobic strength | UT2 progressive-duration work | First comparable exposure |
| General preparation | same role mix with distinct families | UT2 rate-controlled / long-repeat work | Piece duration advances one step |
| Race-specific preparation | race pace, threshold, long aerobic, technique | Head-race controlled simulation | Race-specific family is favored |
| Taper-volume week | race pace, long aerobic, technique | Shorter race-rhythm work | Fits the already-reduced weekly intent; no separate taper transform |

Candidate scoring is deterministic and includes role, duration, preference,
race fit, recent archetype/family repetition, and comparable-session history.
Preference is deliberately soft. A short-piece preference helps score short
families early but does not remove long aerobic work when the role requires it.

The tests cover repeat avoidance, purposeful deterministic re-selection,
novice safeguards, race-specific filtering and available-duration fit. This
report is developer-only and is not added to athlete-facing UI.
