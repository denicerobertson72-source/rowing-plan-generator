# Enable account protection

In Vercel's **rowing-plan-api** project, add these environment variables for Production and Preview:

- `SUPABASE_URL`: your Supabase Project URL
- `SUPABASE_PUBLISHABLE_KEY`: your `sb_publishable_...` key
- `REQUIRE_AUTH`: `true`

In Supabase, go to **Authentication → URL Configuration** and add your PWA Vercel URL to **Site URL** and **Redirect URLs**. Email confirmation is enabled by default for new email/password sign-ups.

Do not set `REQUIRE_AUTH=true` until these values are present and the client account flow has been deployed. The API assigns new athlete profiles to the verified Supabase user ID; it ignores a browser-supplied user ID.
