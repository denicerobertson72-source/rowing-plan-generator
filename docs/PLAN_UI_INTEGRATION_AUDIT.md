# Plan UI integration audit

Today reads `/today` with a same-plan local cache fallback. Week reads the
saved plan ID then `/week`; Season reads `/season`, `/plans/{id}`, and
`/calendar`; Workout Detail reads `/sessions/detail`. Profile regeneration
updates the saved plan ID and cache.

Today/Week consume session title, band, structure and duration. Season had a
separate summary builder and could show a generic `No session` for an active
calendar day. Workout Detail uses API detail fields. Legacy template wording is
still available through older PlanVersions, but current PlanVersion sessions
are the source of truth. Step 6A introduces a shared daily-summary helper so
Today, Week and Season use the final structured session rather than phase as a
workout label.
