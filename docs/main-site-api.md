# Main-site identity and marketing contacts

This milestone connects `gt-esports/gtesports-website` to the central gateway.
Supabase remains the identity provider and the single database. Email/password
sign-in, Discord OAuth, refresh and sign-out still use the existing Supabase SDK.
The API does not issue tokens, migrate passwords, copy contacts, or send email.

## Trust and configuration

Set `MAIN_SITE_SUPABASE_URL` and `MAIN_SITE_SUPABASE_PUBLISHABLE_KEY` to the
**main site's** Supabase project, independently of `GAMEFEST_SUPABASE_*`.
Only publishable and legacy anonymous keys are accepted. A service-role/secret
key must not be used. Remote project URLs require HTTPS; HTTP is accepted only
for loopback development. No credentials belong in Git.

Add the exact main-site origin to `CORS_ALLOWED_ORIGINS`, for example:

```dotenv
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```

For production, explicitly list the approved HTTPS origins. Preview origins
must be configured individually. The browser sends an Authorization bearer
header, not cookies. The gateway allows GET, POST, PATCH, DELETE and OPTIONS.

Every private request calls the configured Supabase project's `/auth/v1/user`
to validate the access token, then reads that user's current `user_roles` row.
The verified Auth response supplies the identity; client-supplied IDs, role
headers, and editable user metadata never grant access. A missing role means
member; failed or malformed role lookups fail closed. Roles are not cached.
The configured project validates its own signing keys through Auth, so the API
does not need a JWT signing secret or a hard-coded signing algorithm.

All database requests use the same user bearer token plus the public project
key. Existing `auth.uid()` and `is_admin()` RLS checks remain active, including
at mutation time if permissions change between API authorization and SQL.
Anonymous users and members cannot access contacts. `/v1/me` is available to
any authenticated member. Main-site profiles, roles and contacts must already
have the reviewed migrations and RLS policies from the main-site repository
applied, including the explicit authenticated profile-read grant in
`20261026000100_grant_profile_reads.sql`. Deployment prerequisites and the
read-only smoke tool are in [the deployment runbook](deployment.md).
API startup does not apply migrations or alter database policies.

Private responses, including errors, carry `Cache-Control: no-store`.
Errors use the gateway's `{error: {code, message, request_id}}` envelope.
Application logs include route templates, method, status and request ID, not
tokens, email addresses, notes, payloads or raw upstream errors. Deployment
access logs must likewise omit headers, request bodies and query strings.

## HTTP contract

All endpoints below require `Authorization: Bearer <Supabase access token>`.

| Method and path | Input | Successful response |
| --- | --- | --- |
| `GET /v1/me` | None | `{user: {id, email}, profile: {id, full_name, created_at} \| null, role: "member" \| "admin"}` |
| `GET /v1/main/ready` | Administrator session | `{service: "main-site", status: "ready"}` after live identity/role and bounded contact schema/read checks |
| `GET /v1/main/marketing-contacts` | `offset` >= 0 (default 0), `limit` 1–500 (default 500) | `{contacts: [...], next_offset: number \| null}` |
| `POST /v1/main/marketing-contacts` | Contact input | 201 and the saved contact |
| `PATCH /v1/main/marketing-contacts/{id}` | All editable contact fields and positive integer `revision` | 200 and the saved contact with incremented revision |
| `DELETE /v1/main/marketing-contacts/{id}` | Positive integer `revision` query parameter | 204, no body |
| `POST /v1/main/marketing-contacts/import` | `{contacts: [...]}`, 1–500 contact inputs | `{imported: number, skipped: number}` |

Identity IDs are Supabase UUIDs. `user.email`, `profile.full_name` and
`profile.created_at` may be null. Profile identity must match the verified user.

Editable contact fields are `email`, `name`, `organization`, `category`,
`contact_group`, `status`, `last_contacted` and `notes`. Creation defaults blank
text fields, group `newsletter`, status `active`, and null `last_contacted`.
Updates require the full editable record to avoid silently clearing omitted
fields. Unknown fields (including client-controlled IDs/timestamps) are rejected.

- Email is trimmed, lowercased and validated (maximum 254 characters).
- Name/organization allow 200 characters, category 100, notes 5,000.
- Groups: `newsletter`, `alumni`, `both`.
- Statuses: `active`, `unsubscribed`, `do_not_contact`.
- Last contacted: a valid `YYYY-MM-DD` date or null.
- Response-only fields: `id`, `revision`, `created_at`, `updated_at`.
- Contact JSON request bodies are limited to 4 MiB, including streamed bodies.

Listing orders by email then ID. Exact PostgREST ranges determine the next
offset, even if its configured row cap is below the requested limit. The main
site loads all pages before applying existing search, filters and CSV export.
Pagination is not a transaction snapshot; refresh after concurrent edits.

PATCH and DELETE include `id` and `revision` in the same database operation.
An absent/stale row returns 409 `revision_conflict`. Duplicate email returns
409 `duplicate_contact`. Database triggers remain responsible for revision and
timestamps. A mid-request permission revocation can also produce an empty RLS
result; it never allows a write or private data read.

Import validates every row before a **single** PostgREST bulk insert. Duplicate
normalized emails within a request keep the first occurrence; existing emails
use `resolution=ignore-duplicates` on the unique email constraint. Existing
opt-outs, groups, notes and revisions are preserved. `count=exact` with
`return=minimal` reports the inserted count without returning private records.
`skipped` includes request duplicates and existing rows. CSV parsing/preview,
the 1 MiB CSV file limit and export formula protection remain in the main site.

## Failures and rollout

Missing/invalid sessions return 401; insufficient permissions return 403;
invalid fields return 422; oversized bodies return 413. Transport/upstream
failures return sanitized 503/504. No failed request falls back to browser
database access. Each Supabase call has a five-second timeout (three seconds
to connect); there are up to three sequential calls per API request. Writes
are never automatically retried. After a failed or timed-out write, the outcome
may be unknown: refresh before retrying, rather than assuming nothing changed.

1. Review and deploy the API PR first. Configure the main-site project and exact
   origins alongside the existing GameFest settings. Do not enable development
   `/login`. `/health` tests liveness; `/ready` checks GameFest only.
   `/v1/main/ready` checks administrator identity/role and contact read readiness;
   it returns no private contact or identity values. Run the read-only staging
   smoke command in the deployment runbook before frontend rollout.
2. In a controlled staging environment, verify `/v1/me` and contact operations
   with an existing test administrator and a member. Confirm anonymous/member
   denial, revocation on the next request, stale revision conflicts, duplicate
   import opt-out preservation, and CORS from the intended frontend origin.
3. Build the paired main-site PR with `VITE_ESPORTS_API_URL` set to the deployed
   HTTPS gateway. Retain its existing Supabase public settings. The variable is
   embedded at build time. Verify account switching, refresh/sign-out, CSV and
   permissions through the browser. Missing API configuration fails closed.
4. Merge only after review and the deployment checks. Main-site README requires
   `@longxiangchen` review. These PRs do not authorize an automatic merge.
5. For rollback, redeploy the previous main-site build. There is no dual-write
   or database copy to reconcile: all versions use the same Supabase records.

Run `python -m pytest -q` for offline tests. Mocked upstream tests verify the
HTTP contract, identity/role checks, caller context, validation, errors, no-store,
and bounded queries/mutations. They do not prove production configuration or
execute the database policies. The opt-in
[database integration suite](../tests/integration/README.md) applies the paired
main-site migrations and SQL permission tests to disposable local GoTrue,
PostgREST, and PostgreSQL services. It verifies real sessions, current-role
revocation, direct database RLS, optimistic concurrency, capped pagination,
duplicate import counts, opt-out preservation, and atomic import rollback.
Hosted API-key enforcement, CORS, OAuth, and real-account behavior still need
the staging checks above.

References: [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts),
[PostgREST pagination](https://docs.postgrest.org/en/stable/references/api/pagination_count.html),
[PostgREST insert and upsert](https://docs.postgrest.org/en/stable/references/api/tables_views.html).
