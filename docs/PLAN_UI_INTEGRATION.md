# Plan UI integration

All plan views use the saved current PlanVersion ID. Regeneration replaces both
the saved ID and cached plan. `daily-summary.ts` derives athlete-facing labels
from final session fields, including transformed strength and taper status;
phase remains contextual. It never exposes candidate scores or provenance.

Calendar color indicates session category while phase remains a separate
ribbon/context label. Intentional empty states display Off, Unavailable, or
Optional recovery rather than generic No Session. Existing responsive grid and
cards are retained for mobile widths.
