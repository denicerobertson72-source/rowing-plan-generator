# Coach/admin race postings

Shared race postings are separate from an athlete's private season calendar.
They may be created or edited after a race has completed and are filtered for
the athlete's profile `experience_level`: `beginner`, `intermediate`, or
`advanced`.

To authorize a coach or administrator, set the API environment variable
`COACH_ADMIN_USER_IDS` to a comma-separated list of Supabase Auth user IDs.
Only those users can use the admin posting endpoints. Everyone else can only
read postings for their own experience level.
