-- Minimal Supabase Auth/role scaffolding for disposable PostgreSQL only.
-- Application tables/functions come from the actual publishable bootstrap SQL.
create role anon; create role authenticated; create role service_role;
create schema auth;
create table auth.users(id uuid primary key, raw_user_meta_data jsonb);
create table auth.identities(id uuid primary key default gen_random_uuid(), user_id uuid references auth.users(id), provider text, provider_id text, identity_data jsonb);
create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
