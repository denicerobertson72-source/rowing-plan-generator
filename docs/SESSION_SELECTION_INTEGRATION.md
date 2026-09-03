# Session-selection integration (Step 4)

Before Step 4, `scheduler._session` chose the first compatible record returned
by `session_selector.select_session`. That legacy JSON library remains intact
as the fallback boundary.

For a newly generated ordinary rowing session, the scheduler now performs the
minimal replacement:

`WeeklyTrainingIntent -> deterministic day role -> archetype candidates ->
score -> concrete parameters -> session fingerprint/explanation`.

`session_selection.py` contains role assignment, hard candidate filtering,
scoring, parametric instantiation and lightweight in-plan history. It has no
random source or database-order dependency. Existing band targets, watts/split
conversion, recurring commitments, ordinary-row-date cap, races, rest,
strength, coaching, locked sessions, plan persistence and legacy fallback all
remain in the scheduler path.

Selection weights are app-defined coaching rules; evidence informs the
categories and safeguards, not an exact validated scoring equation. Workout
preference is a score adjustment only. Candidate scores, reasons, progression
dimension and fingerprint are stored on generated sessions for developer
inspection; they are not athlete-facing UI.
