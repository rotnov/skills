-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261108120000_hash_only_release.sql.
-- No required-release change, response shape, table grant, policy, task authority or model launch. (#196)
--
-- A new listener connection no longer sends a Hello message to every same-project peer.
-- Its acknowledged heartbeat is the one presence event: session_health.last_seen, which
-- every peer already reads through session_peers (availability and last_seen).
--
-- session_heartbeat is a thin wrapper. Before the hash-only heartbeat runs unchanged, a
-- heartbeat that opens a new connection on the required protocol records every
-- same-project peer in session_hello_sent at now(). The reviewed core (120000) skips a
-- peer recorded within 30 minutes and at or after the sender's current streak start, and
-- the streak trigger writes that start in the same transaction at the same now(), so the
-- core greets no one. No chain function is copied.
--
-- Compatibility: the response keeps its shape. The Hello wrapper (150000) still counts
-- every peer and reports `hello.peers` = `hello.skipped`, the available peers and their
-- roles, and still writes the newcomer's own quiet roster notice. A deployed client
-- therefore reports "Hello sent to 0 peers, N already greeted" and keeps working; it
-- needs no change. Hellos already queued stay queued and every client still receipts them
-- quietly. A client that never sees this server keeps getting the server's Hellos.
--
-- session_hello_sent now means "last announced to this peer", by Hello or by presence.
-- A peer that joins between the wrapper's insert and the core's peer loop can still get
-- one Hello; nothing else does. Lock order for a new connection becomes: the sender's
-- session_hello_sent rows in peer order, then the health row, then the connection row.
-- Only this sender's heartbeats write those rows, so concurrent heartbeats of one agent
-- take them in the same order.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The hash-only heartbeat (120000 of 2026-11-08), moved and called unchanged.
 'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','019b882f0824fe1ff0491bcb3c3f0d80171eedf2a7d16c1d116d4e6a5d1ed725',
 -- The chain below it, which this wrapper relies on and does not change.
 'quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','2957604dce5af878d0da3713b17545e11a745a95c4fc1aad95c222f6aa5394a2',
 -- The Hello wrapper (150000): counts skipped peers and writes the roster notice.
 'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','be24c31bc064a0f4dc70bb4d98086525a49738e27749bb1eb53aa5ce40dc6c76',
 -- The core (120000) whose per-peer skip predicate this wrapper satisfies.
 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','b3791a5b3b5a93433a6a3dd854af5ffed93e9843bc905b811e201b20d55873a5',
 -- The streak trigger that sets the start the skip predicate compares with.
 'quorum_private.session_health_streak()','8881904b8a68daff482f04eb034f32a4e1b8a39649628d5e4559ef0a13ce7032',
 -- The binding check this wrapper calls first.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The hash-only release; unchanged here.
 'public.session_requirements()','e276e4392bc8b8782eb4e5f4ff9b14eb9a82aed803082edbf778ea36de523b8a');
begin
 if to_regprocedure('quorum_private.session_heartbeat_before_presence(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)') is not null then
  raise exception 'presence_without_hello_present'; end if;
 if to_regprocedure('quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)') is null then
  raise exception 'hash_only_release_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

alter function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz) set schema quorum_private;
alter function quorum_private.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)
 rename to session_heartbeat_before_presence;
revoke all on function quorum_private.session_heartbeat_before_presence(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)
 from public,anon,authenticated,service_role;

-- The same public signature and defaults; the moved heartbeat does everything else.
create function public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text default null, p_awaiting_owner integer default null,
 p_reception_hold_until timestamptz default null, p_client_run_id uuid default null,
 p_runtime_started_at timestamptz default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; own quorum_private.agents%rowtype;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 -- #196: a new connection on the required protocol is announced by presence alone, so the
 -- reviewed core counts every same-project peer as already greeted and sends no Hello.
 if p_connection_id is not null
    and p_protocol_version is not distinct from (public.session_requirements()->>'protocol_version')::integer
    and not exists(select 1 from quorum_private.session_connections c
     where c.agent_id=p_agent_id and c.connection_id=p_connection_id) then
  select * into own from quorum_private.agents where id=p_agent_id;
  insert into quorum_private.session_hello_sent(sender_id,recipient_id,sent_at)
  select p_agent_id,a.id,now() from quorum_private.agents a
  where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
  order by a.id
  on conflict(sender_id,recipient_id) do update set sent_at=excluded.sent_at;
 end if;
 return quorum_private.session_heartbeat_before_presence(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version,p_awaiting_owner,p_reception_hold_until,p_client_run_id,p_runtime_started_at);
end $function$;
revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)
 from public,anon,authenticated,service_role;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)
 to authenticated;
