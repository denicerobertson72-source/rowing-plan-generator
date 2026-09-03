# Save interaction audit

| Location | Persistence action | Previous feedback | Step 6B behavior |
| --- | --- | --- | --- |
| Profile | `updateAthlete` | Inline status only | Shared confirmed-success/error toast; form remains intact on failure. |
| Race editor | `updateAthlete` | Mutated list before save | Local modal draft; only confirmed save updates/sorts the list. |
| Recurring schedule | `updateAthlete` | Inline status only | Shared toast; modal stays open if saving fails. |
| Testing history | `updateAthlete` | Inline status only | Shared toast; entered values remain for retry. |
| Plan refresh | `generateAthletePlan` | Inline status only | Shared “Plan updated” confirmation after generation. |
| Workout log | `logWorkout` | Sheet-local text | Shared toast and duplicate-submit guard; sheet stays open on failure. |
| Weekly temporary change | direct POST | Page-local status | Migrated to shared feedback with duplicate-submit guard. |
| Onboarding/profile creation | create/update + generation | Page-local status | Active first-use flow; its existing combined creation/generation status remains because it is a single blocking workflow rather than an in-product edit save. |
| Race postings | `saveRacePosting` | Page-local status | Active only for coach/admin users; retained its dedicated admin feedback flow. |
| Private check-in | direct POST | Page-local status | Deprecated/unlinked: no current navigation points to `/check-in`; intentionally not migrated or resurfaced. |

The final three are separate flows not changed by this focused profile/race correction; they retain their values after unsuccessful requests.
