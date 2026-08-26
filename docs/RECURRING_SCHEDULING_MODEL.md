# Recurring scheduling model

`recurring_activities` supports `fixed`, `preferred`, and `flexible` commitments, frequencies, allowed/prohibited days, same-day rules, and race-week mobility. Legacy weekday availability is migrated into fixed strength/rest commitments until a rower edits the new schedule cards.

Candidate scoring selects one valid placement for each movable recurring activity. Fixed commitments never move; recovery/quality conflicts outrank preferences; planner moves carry reason codes and a human-readable explanation. The plan output records these decisions for the mobile client; legacy weekday availability remains the authoritative session generator while recurring-activity editing is introduced progressively.
