\set ON_ERROR_STOP on
\getenv fixture_database_password POSTGRES_PASSWORD

create role anon nologin nosuperuser nobypassrls;
create role authenticated nologin nosuperuser nobypassrls;
create role service_role nologin nosuperuser bypassrls;
create role authenticator login noinherit nosuperuser nobypassrls;
select format('alter role authenticator password %L', :'fixture_database_password') \gexec
grant anon, authenticated to authenticator;

create extension if not exists pgcrypto;
create extension if not exists "uuid-ossp";
create schema auth;

revoke create on schema public from public;
grant usage on schema public, auth to anon, authenticated, service_role;

-- Both the repository SQL checks and PostgREST exercise this same identity.
-- GoTrue also installs this helper through its own versioned auth migrations.
create function auth.uid()
returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid;
$$;
revoke all on function auth.uid() from public;
grant execute on function auth.uid() to anon, authenticated, service_role;

-- Intentionally grant no default table privileges. Each application migration
-- must explicitly expose its tables, including authenticated profile reads.
