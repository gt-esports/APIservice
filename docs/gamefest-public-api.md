# Public GameFest Hall of Fame API

## Contract

`GET /v1/gamefest/past-winners` is anonymous and read-only. It returns up to 20
inactive seasons, ordered by `created_at DESC, id ASC`, with up to three players
per season ordered by `points DESC NULLS LAST, id ASC`.

```json
{
  "seasons": [
    {
      "id": "9a1f58f7-4f6b-4b4a-94e4-e3944dcf9cc8",
      "name": "GameFest 2026",
      "slug": "gamefest-2026",
      "winners": [
        {"rank": 1, "display_name": "Example Player", "points": 120}
      ]
    }
  ]
}
```

The example above is synthetic. Ranks are ordered display positions 1–3, as on
the existing page; tied scores use player UUID order. Points are nonnegative
integers (Postgres int range). Null legacy points display as zero. Display names
use trimmed first/last name, then username, then `GameFest Player`.

The output excludes player/user UUIDs, profile records, email, registration and
staff data. Season UUIDs are public identifiers. Unknown upstream fields are
discarded; malformed required data produces an unavailable response rather than
a partial or misleading result. Missing/hidden profile relationships retain the
existing fallback display name.

- No inactive seasons: `200 {"seasons": []}`.
- An inactive season without players: `200` with `"winners": []` for that season.
- Configuration, permissions, HTTP, network, or upstream schema errors: `503`.
- Writes to the results endpoint: `405`; no upstream write is performed.
- Unexpected errors: generic `500` without exception details.

Every gateway response has `X-Request-ID`; errors use the same ID in the body:

```json
{
  "error": {
    "code": "gamefest_unavailable",
    "message": "GameFest results are temporarily unavailable.",
    "request_id": "d2d54476-0327-4ad7-9ecf-70fbf4d28f18"
  }
}
```

Successful results advertise `Cache-Control: public, max-age=30`. Unavailable
responses have `Retry-After: 30` and no public cache directive. One bounded
upstream read is made per uncached API request; there is no process-wide cache or
rate limiter in this milestone. Each upstream HTTP operation has a five-second
timeout. The frontend also bounds the complete fetch/read operation to ten seconds.

## Data source and compatibility

The adapter sends this single GET to the configured project's `/rest/v1/seasons`:

```text
select=id,name,slug,players(id,points,users(username,fname,lname))
is_active=eq.false
order=created_at.desc,id.asc
limit=20
players.order=points.desc.nullslast,id.asc
players.limit=3
```

Nested embeds use the existing `players.season_id -> seasons.id` and
`players.user_id -> public.users.id` foreign keys. See
[PostgREST resource embedding](https://postgrest.org/en/stable/references/api/resource_embedding.html)
for embedded ordering and limits. The adapter sends only the configured public
API key, never a caller's Authorization header; existing anonymous grants and
RLS apply. Supabase verifies the configured key. See
[Supabase key types](https://supabase.com/docs/guides/getting-started/api-keys).

The existing GameFest migrations define the required relations and public read
policies. This PR does not apply migrations or widen access. An installation
missing anonymous access must be reviewed separately; do not substitute a
service-role key to make the readiness probe pass.

`is_active=false` intentionally matches the old page: it can include an inactive
future season. This API is not an independent event lifecycle model. Historical
names and scores still reflect the existing live records; no snapshots are added.

## Release and verification

1. Review the APIservice PR and the paired GameFest PR. GameFest requires
   `@longxiangchen` review before merge. Neither PR should bypass branch rules.
2. Configure a staging/API deployment with the same public data source and the
   exact allowed frontend origin. No new database migration is required.
3. Check `/health`, `/ready`, `/docs` and the results endpoint. Compare the season
   and score ordering to the existing page. Confirm inactive seasons with no
   winners and an empty archive are valid states.
4. Build a GameFest preview with `VITE_ESPORTS_API_URL` pointing to that API.
   Check the Hall of Fame and its network request. A 503/unreachable API should
   produce the friendly retry state, with no browser Supabase query from this
   component. Other GameFest components still use Supabase.
5. After API readiness and the preview succeed, promote the reviewed releases.
   Keep the existing frontend release available for rollback. Rolling back the
   frontend requires no database rollback, because this milestone performs no
   data migration or writes.

`python -m pytest -q` verifies the adapter and gateway with synthetic HTTP
fixtures, including public-field projection, bounded query, errors, readiness,
CORS, request tracing and the isolated development JWT flow. GameFest has
separate client/component tests and a production build check. Fixture tests alone
do not prove live Supabase connectivity or deployment success.
