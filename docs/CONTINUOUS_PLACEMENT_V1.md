# Continuous placement V1

This is the replacement planner's parallel, abstract calendar-placement layer.
It does not route live V2, persist plans, or instantiate workouts.

The finite horizon and beam width are software configuration
(`PlacementSearchConfig`), not training-policy rules.  Candidate dates come
only from explicit date/weekday availability, capacity, restrictions, and
preference metadata.  The layer contains no Monday--Sunday template.

The deterministic objective is applied in this order:

1. hard constraint violations;
2. authoritative commitment preservation;
3. recovery and spacing;
4. race and taper readiness;
5. high-priority fulfillment;
6. protected aerobic and strength minima;
7. progression quality;
8. excessive density;
9. athlete preference;
10. plan stability; and
11. canonical chronological `(date, placement_id)` tie-break.

The M6 sequence evaluator remains the single source of physiological
eligibility.  The placer only enumerates actual dates, calls M6, accounts for
capacity, and records its selected decision.  Empty dates are valid; a need
may remain deferred or unplaced with a typed diagnostic.

Long aerobic rows retain their own physical placement and may state that they
can contribute to a dedicated-UT2 need through `served_need_ids`; this is
traceability only, never a duplicated session or automatic physiological
credit.
