-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261105160000_peer_status_visibility.sql (which requires the Hello reporting,
-- owner-maintenance and role-assignment extensions).
-- No required-release change, table grant, policy, task authority or model launch. (#126)
--
-- A listener that reconnects every few minutes used to send a new Hello to every
-- peer on every new connection ID. After this release a new connection still
-- announces itself, except to a peer that already got a Hello from this sender
-- within the last 30 minutes of the sender's current presence streak:
--  - the first connection of an agent, and a reconnect after a real absence (the
--    sender's health was at least 60 seconds stale), announce to every peer;
--  - a reconnect inside the window with no absence since that Hello sends that
--    peer nothing. The Hello result and the newcomer's roster still count that
--    peer, so the roster reports the same peers as before.
-- A presence streak starts whenever any writer of session_health (heartbeat or
-- receipt) finds the previous health at least 60 seconds old, or finds none.
-- An agent known only from before this release starts its streak at its next
-- health write, so its next reconnect announces once (old behaviour) and later
-- reconnects are deduplicated. Unknown state therefore always announces.
--
-- The reviewed core (152603) and the Hello wrapper (180000) move to private
-- functions kept for rollback; the new bodies are those bodies plus the lines
-- marked "#126". The public peer-status heartbeat (160000) is pinned and called
-- unchanged: it still calls quorum_private.session_heartbeat_before_status.
-- Lock order stays health row first (the core's upsert, which now also writes
-- the agent's streak row from a trigger), then connection, then per-peer Hello
-- rows in peer order, then the Hello and status rows. Reads take no locks.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The peer-status heartbeat (160000), kept public and called unchanged.
 'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','2957604dce5af878d0da3713b17545e11a745a95c4fc1aad95c222f6aa5394a2',
 -- The Hello wrapper (180000) the peer-status heartbeat calls; its body is copied below.
 'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','3fa1dc12649d4b162720b0cc5af8ada060a4b30ea95095087c83eadd15ad8e41',
 -- The reviewed core (152603) that sends the Hellos; its body is copied below.
 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','944211db94640dca91d75e007a4c5de8a1ee28511ea6c78a5b3fb2f7d96768c5',
 -- The dispatcher both copied bodies send through (role-assignment wrapper, 20261105140000).
 'quorum_private.call(text,jsonb,text)','c7e71c9dcc515b6f0eb1f5b64b0e4cb431c94422b1bb763fc2687f5c2b86c99a',
 -- The binding check both copied bodies rely on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The other session_health writer; the streak trigger reads its writes.
 'public.session_ack(uuid,text,uuid)','2ddc386ed6b7cce66088d8e331129a24158853f587c946160cd12dee889f47f7',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regclass('quorum_private.session_streaks') is not null
    or to_regclass('quorum_private.session_hello_sent') is not null
    or to_regprocedure('quorum_private.session_heartbeat_core_before_dedupe(uuid,text,uuid,integer,text)') is not null
    or to_regprocedure('quorum_private.session_heartbeat_hello_before_dedupe(uuid,text,uuid,integer,text)') is not null
    or to_regprocedure('quorum_private.session_health_streak()') is not null then
  raise exception 'hello_dedupe_present'; end if;
 if to_regclass('quorum_private.session_status') is null
    or to_regprocedure('quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)') is null then
  raise exception 'peer_status_required'; end if;
 if to_regclass('quorum_private.session_hellos') is null then
  raise exception 'hello_extension_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- Start of each agent's current presence streak. Private; no client reads it.
create table quorum_private.session_streaks (
 agent_id uuid primary key references quorum_private.agents(id),
 online_since timestamptz not null
);
alter table quorum_private.session_streaks enable row level security;
revoke all on quorum_private.session_streaks from public,anon,authenticated,service_role;

-- Last Hello each sender sent each recipient through this release. Private.
create table quorum_private.session_hello_sent (
 sender_id uuid not null references quorum_private.agents(id),
 recipient_id uuid not null references quorum_private.agents(id),
 sent_at timestamptz not null,
 primary key(sender_id,recipient_id)
);
alter table quorum_private.session_hello_sent enable row level security;
revoke all on quorum_private.session_hello_sent from public,anon,authenticated,service_role;

-- Runs inside every session_health write, under that row's lock, so no writer can skip it.
create function quorum_private.session_health_streak()
returns trigger language plpgsql security definer set search_path=pg_catalog as $function$
begin
 if tg_op='INSERT' or old.last_seen is null or new.last_seen>=old.last_seen+interval '60 seconds' then
  -- No previous health, or a real absence: a new streak starts at this write.
  insert into quorum_private.session_streaks(agent_id,online_since) values(new.agent_id,new.last_seen)
  on conflict(agent_id) do update set online_since=excluded.online_since;
 else
  -- Continuous presence keeps the streak; an agent first seen by this release starts one now.
  insert into quorum_private.session_streaks(agent_id,online_since) values(new.agent_id,new.last_seen)
  on conflict(agent_id) do nothing;
 end if;
 return null;
end $function$;
revoke all on function quorum_private.session_health_streak() from public,anon,authenticated,service_role;
create trigger session_health_streak after insert or update of last_seen on quorum_private.session_health
 for each row execute function quorum_private.session_health_streak();

alter function quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)
 rename to session_heartbeat_core_before_dedupe;
revoke all on function quorum_private.session_heartbeat_core_before_dedupe(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;
alter function quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)
 rename to session_heartbeat_hello_before_dedupe;
revoke all on function quorum_private.session_heartbeat_hello_before_dedupe(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;

-- The reviewed 152603 core; lines marked #126 add the per-recipient reconnect dedupe.
create function quorum_private.session_heartbeat_before_hello(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; own quorum_private.agents%rowtype; peer record; fresh integer; sent jsonb; required jsonb;
 since timestamptz; skipped jsonb:='[]'::jsonb; -- #126
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 required:=public.session_requirements();
 if p_protocol_version is distinct from (required->>'protocol_version')::integer
  or p_skill_version is distinct from required->>'skill_version' then
  return jsonb_build_object('code','skill_update_required','required',required);
 end if;
 if p_connection_id is null then raise exception 'connection_id_required'; end if;
 select * into own from quorum_private.agents where id=p_agent_id;
 insert into quorum_private.session_health(agent_id,last_seen,protocol_version,skill_version)
 values(p_agent_id,now(),p_protocol_version,p_skill_version)
 on conflict(agent_id) do update set last_seen=excluded.last_seen,
  protocol_version=excluded.protocol_version,skill_version=excluded.skill_version;
 insert into quorum_private.session_connections values(p_agent_id,p_connection_id) on conflict do nothing;
 get diagnostics fresh=row_count;
 if fresh=1 then
  -- #126: the streak row was written by the health trigger above, under the health row lock.
  select online_since into since from quorum_private.session_streaks where agent_id=p_agent_id;
  for peer in
   select a.id from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
   order by a.id limit 128
  loop
   -- #126: already greeted within 30 minutes of this unbroken streak; a missing streak never skips.
   if since is not null and exists(select 1 from quorum_private.session_hello_sent s
     where s.sender_id=p_agent_id and s.recipient_id=peer.id
      and s.sent_at>now()-interval '30 minutes' and s.sent_at>=since) then
    skipped:=skipped||to_jsonb(peer.id);
    continue;
   end if;
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
    'body','Hello! Listener connected: '||own.role||' (project '||own.project_ref||'). Transport notice; no reply required.'),
    'hello:'||p_agent_id||':'||p_connection_id||':'||peer.id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   -- #126
   insert into quorum_private.session_hello_sent(sender_id,recipient_id,sent_at) values(p_agent_id,peer.id,now())
   on conflict(sender_id,recipient_id) do update set sent_at=excluded.sent_at;
  end loop;
 end if;
 return jsonb_build_object('online',true,'new_connection',fresh=1,'last_seen',now(),'required',required)
  -- #126: private to the Hello wrapper below, which removes it before anything returns to a client.
  ||case when fresh=1 then jsonb_build_object('hello_skipped',skipped) else '{}'::jsonb end;
end $function$;
revoke all on function quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;

-- The reviewed 180000 Hello wrapper; lines marked #126 count a deduplicated peer as announced.
create function quorum_private.session_heartbeat_before_status(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer; skipped jsonb; -- #126
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 -- #126: peers the core did not greet again on this connection; never returned to a client.
 skipped:=coalesce(result->'hello_skipped','[]'::jsonb);
 result:=result-'hello_skipped';
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles)
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb)
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and (exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
     or skipped@>to_jsonb(array[a.id::text])) -- #126
   ) y
  ) x
  on conflict do nothing;
  get diagnostics created=row_count;
  if created=1 then
   select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
   select string_agg(r.value#>>'{}',', ' order by r.ordinality) into listing
    from jsonb_array_elements(hello.available_roles) with ordinality r;
   -- A self-addressed transport notice: never a task, never a reply chain.
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',p_agent_id,
    'conversation_id',md5('quorum-roster:'||p_agent_id||':'||p_connection_id)::uuid,'reply_to',null,
    'body',case when hello.available=0 then 'Quorum roster: no other native session is available in project '
      ||own.project_ref||'. Hello reached '||hello.peers||' registered peer(s). Transport notice; no reply required.'
     else 'Quorum roster: '||hello.available||' peer(s) available in project '||own.project_ref||': '||listing
      ||case when hello.available>16 then ', ...' else '' end
      ||'. Transport notice; no reply required.' end),
    'roster:'||p_agent_id||':'||p_connection_id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   update quorum_private.session_hellos set roster_message_id=(sent->>'message_id')::uuid
    where agent_id=p_agent_id and connection_id=p_connection_id;
  end if;
 end if;
 select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
 if not found then return result||jsonb_build_object('hello',null); end if;
 return result||jsonb_build_object('hello',jsonb_build_object('project_ref',own.project_ref,
  'peers',hello.peers,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles,
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $function$;
revoke all on function quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;
