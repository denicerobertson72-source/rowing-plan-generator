# Replacement Planner Shadow Comparison

`REPLACEMENT_PLANNER_SHADOW_ENABLED` is default-off. When enabled for an
authenticated V2 generation with an explicit planning-date boundary, the API
runs the in-memory replacement candidate after the authoritative V2 result.
V2 remains the only planner whose plan is returned or persisted.

The shadow receives the same normalized profile and its availability, races,
performance anchors, serialized locks/user authority, prior plan, completed
actual history, intensity profile, power profile, and planning date as the V2
request. It records only aggregate `planner_shadow` telemetry; it does not log
profile or session payloads.

Fresh public generation is not shadowed because legacy V2 has no explicit
historical planning-date boundary there. Comparing it with a V3 current-date
candidate would be materially invalid. V3 currently does not consume V2's
legacy rolling-target diagnostics or V2's internal solver scoring vector; the
comparison treats those as implementation detail rather than fabricated input
parity.

The comparison evaluates typed history, authority, race/recovery, low-intensity
and quality/strength intent, target-credit accounting, materialization, and
continuous (non-weekly) policy contracts. Flexible date differences are
descriptive only. Promotion blockers include history or authority regression,
materialization failure, duplicate credit, race/taper regression, recovery
violation, and a research-contract failure.
