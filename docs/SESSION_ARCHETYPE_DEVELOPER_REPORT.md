# Session archetype developer report

Generate the complete browser with:

```bash
python3 -c "from rowing_plan.session_archetypes import developer_report; print(developer_report())"
```

It groups every record by band and shows name, role, structure family,
experience eligibility, race/environment fit, load class and source IDs.
This is developer-only metadata; it is not surfaced to normal athletes.

## Representative catalog examples

| Category | Archetypes (three examples) |
| --- | --- |
| UT3 | Technical interval reset; Drill and easy connection; Easy continuous movement |
| UT2 | Short aerobic repeats; Medium aerobic repeats; Continuous aerobic row |
| UT1 | Controlled shorter endurance repeats; Medium endurance repeats; Long sustained endurance blocks |
| AT | Short threshold repeats; Medium threshold repeats; Long threshold blocks |
| TR | Race-development intervals; Race-rate blocks; Mixed-rate pieces |
| AN | Thirty-second power repetitions; Sixty-second repeat power; Ninety-second capacity pieces |
| PP | Stroke-count maximal work; Start practice; Short acceleration work |
| 5K | Head aerobic-strength rhythm; Head race-rate blocks; Head controlled simulation |
| 2K | Two-k sustained threshold; Two-k rate development; Two-k partial simulation |
| 1K | One-k start development; One-k high-rate technique; One-k broken-race finish |
| Novice | Technique with very short easy rowing; Manageable continuous rowing; Progressive-duration novice row |

Query examples (again: filtering only, no automatic selection):

```python
eligible_archetypes(experience="experienced", role="aerobic_base")
eligible_archetypes(experience="novice", role="aerobic_base")
eligible_archetypes(experience="experienced", race_type="head_5k")
eligible_archetypes(experience="competitive", race_type="sprint_1k")
```
