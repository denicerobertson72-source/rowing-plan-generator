# Session archetype model (Step 3)

`SessionArchetype` is a versioned, original app-authored parameter envelope.
It is not a fixed workout and is not currently read by the scheduler.

Each record supplies role, primary/secondary bands, objectives, eligibility,
phase/race/environment fit, duration/work/repetition/recovery/rate ranges,
preference compatibility, novice/taper flags, progression dimensions, load and
recovery metadata, strength compatibility, and provenance.

The catalog lives in `rowing_plan/session_archetypes.py`. Its `developer_report`
function is the developer-facing browser; it groups the technical fields in a
readable table without exposing them in athlete UI. `eligible_archetypes` is a
filtering/query helper only: it never ranks, instantiates, or schedules an
archetype.

Ranges are conservative app-defined coaching envelopes. Research supports
varied structures, progressive training, mostly low-intensity rowing and
limited high-intensity work; it does not validate one universal interval
prescription. Preference is advisory (`preferred`, `good`, `acceptable`, or
`poor_fit`), never an exclusion rule.

Non-rowing strength and alternate-aerobic records have no rowing primary band.
Alternate UT2 is explicitly cardiovascular-only, so it cannot be counted as
rowing-zone volume. Coached records state that coach instructions take priority.

The validator rejects unknown bands/roles/eligibility, inverted ranges,
high-load records without recovery guidance, novice access to AT/TR/AN/PP,
race-role records without race fit, rowing bands on non-rowing records, and
missing provenance/original-wording flags.
