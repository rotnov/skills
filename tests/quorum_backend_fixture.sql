-- Minimal Supabase Auth/role scaffolding for disposable PostgreSQL only.
-- Application tables/functions come from the actual publishable bootstrap SQL.
create role anon; create role authenticated; create role service_role;
create schema auth;
create table auth.users(id uuid primary key, raw_user_meta_data jsonb);
create table auth.identities(id uuid primary key default gen_random_uuid(), user_id uuid references auth.users(id), provider text, provider_id text, identity_data jsonb);
create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
-- Realtime transport boundary only. Production-owned Realtime objects are
-- never created or altered by the application migration.
create schema realtime;
create table realtime.messages(topic text, event text, payload jsonb,
                                private boolean, extension text default 'broadcast');
alter table realtime.messages enable row level security;
grant usage on schema realtime to anon, authenticated;
grant select, insert on realtime.messages to anon, authenticated;
create function realtime.topic() returns text language sql as $$
 select current_setting('realtime.topic',true)
$$;
create function realtime.send(payload jsonb,event text,topic text,private boolean default true)
returns void language plpgsql security definer as $$
begin
 if current_setting('quorum_test.broadcast_failed',true)='yes' then
  raise exception 'synthetic_broadcast_unavailable';
 end if;
 insert into realtime.messages(topic,event,payload,private) values(topic,event,payload,private);
end $$;
