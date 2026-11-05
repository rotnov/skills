-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261105140000_role_assignment_and_owner_retire.sql (which requires
-- 20261030140000_owner_update_maintenance.sql) and 20261103180000_hello_roster_visibility.sql.
-- No required-release change, table grant, policy, task authority or model launch. (#113, #104)
--
-- 1. session_heartbeat gains four trailing defaulted arguments: the sender's
--    awaiting-owner count (0..32), an explicit reception hold deadline (kept only
--    while in the future and clamped to now()+30 minutes), a client run
--    generation UUID and that run's start time (clamped to now()). Every
--    successful heartbeat overwrites the one private status row of its agent, so
--    the row always describes the latest heartbeat; an old client's call stores
--    NULLs and gets the unchanged response. A release mismatch stores nothing.
-- 2. session_peers adds, per peer, `awaiting_owner`, `reception_hold_until` and
--    `client_run_reported`, and two derived states:
--    - availability `awaiting_owner` replaces `online` when the peer's last
--      status heartbeat is under 60 seconds old and its count is above zero; a
--      held offer that is overdue keeps `unavailable` with reason
--      `reception_hold` (instead of `receipt_timeout`) until the hold ends;
--    - availability `client_restarted_unrecovered` (reason
--      `run_changed_without_heartbeat`) replaces `unavailable` only when the
--      peer's health is stale (at least 60 seconds), its last heartbeat named a
--      run generation, and its newest owner-update runtime report names a
--      different run and was recorded after that heartbeat: another process of
--      the same binding reported it was running, and no heartbeat followed.
--    Peers see only counts, timestamps and that boolean, never a run ID, a
--    message ID or a body. Every other row keeps the reviewed result exactly.
--
-- The reviewed heartbeat (the Hello wrapper) moves to a private function and is
-- called unchanged. The reviewed session_peers body moves to a private function
-- kept for rollback; the new body is that query plus a status and report join,
-- so health, pending receipts, status and runtime reports are read in one
-- statement snapshot. Heartbeat writes take the core's health row lock first,
-- then the status row, the same order for every writer. Reads take no locks.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The Hello wrapper (180000) moved to private and called unchanged.
 'public.session_heartbeat(uuid,text,uuid,integer,text)','3fa1dc12649d4b162720b0cc5af8ada060a4b30ea95095087c83eadd15ad8e41',
 -- The reviewed core the wrapper calls (152603), as 180000 pinned it.
 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','944211db94640dca91d75e007a4c5de8a1ee28511ea6c78a5b3fb2f7d96768c5',
 -- The dispatcher the wrapper sends Hellos through: the role-assignment wrapper (20261105140000).
 'quorum_private.call(text,jsonb,text)','c7e71c9dcc515b6f0eb1f5b64b0e4cb431c94422b1bb763fc2687f5c2b86c99a',
 -- The peer view whose body is copied below (152603).
 'public.session_peers(uuid,text)','e6bef8353f48cb6322f76dcb024eeb4972d2881eeb65b28c62023e2d2f8b31cd',
 -- The binding check the copied body relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The only writer of runtime_evidence (run_id, reported_at) that the restart rule reads.
 'public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)','abce7c4012a3221f7ddafebb202c6468ed0bf90e5795f33babdfbb4a3af68060',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regclass('quorum_private.session_status') is not null
    or to_regprocedure('quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)') is not null
    or to_regprocedure('quorum_private.session_peers_before_status(uuid,text)') is not null then
  raise exception 'peer_status_present'; end if;
 if to_regprocedure('quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)') is null then
  raise exception 'hello_extension_required'; end if;
 if to_regprocedure('quorum_private.update_session_locked(uuid,text,uuid)') is null then
  raise exception 'owner_update_maintenance_required'; end if;
 if to_regprocedure('quorum_private.call_role_core(text,jsonb,text)') is null then
  raise exception 'role_assignment_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- The last heartbeat's self-reported status, one row per agent. Private; no client reads it.
create table quorum_private.session_status (
 agent_id uuid primary key references quorum_private.agents(id),
 reported_at timestamptz not null,
 awaiting_owner integer check(awaiting_owner between 0 and 32),
 reception_hold_until timestamptz,
 client_run_id uuid,
 runtime_started_at timestamptz
);
alter table quorum_private.session_status enable row level security;
revoke all on quorum_private.session_status from public,anon,authenticated,service_role;

alter function public.session_heartbeat(uuid,text,uuid,integer,text) set schema quorum_private;
alter function quorum_private.session_heartbeat(uuid,text,uuid,integer,text) rename to session_heartbeat_before_status;
revoke all on function quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;
alter function public.session_peers(uuid,text) set schema quorum_private;
alter function quorum_private.session_peers(uuid,text) rename to session_peers_before_status;
revoke all on function quorum_private.session_peers_before_status(uuid,text) from public,anon,authenticated,service_role;

-- Trailing defaulted arguments: current clients send the old argument set and
-- get the reviewed response. Only one public signature exists, so calls stay unambiguous.
create function public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text, p_awaiting_owner integer default null,
 p_reception_hold_until timestamptz default null, p_client_run_id uuid default null,
 p_runtime_started_at timestamptz default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb;
begin
 -- Refused before the core runs, so an invalid status changes no health, connection or Hello.
 if p_awaiting_owner is not null and p_awaiting_owner not between 0 and 32 then
  raise exception 'invalid_peer_status'; end if;
 -- The reviewed heartbeat validates the exact owned binding and the release, and locks
 -- the agent's health row before this status write.
 result:=quorum_private.session_heartbeat_before_status(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 insert into quorum_private.session_status(agent_id,reported_at,awaiting_owner,reception_hold_until,
  client_run_id,runtime_started_at)
 values(p_agent_id,now(),p_awaiting_owner,
  case when p_reception_hold_until>now() then least(p_reception_hold_until,now()+interval '30 minutes') end,
  p_client_run_id,
  case when p_runtime_started_at is not null then least(p_runtime_started_at,now()) end)
 on conflict(agent_id) do update set reported_at=excluded.reported_at,awaiting_owner=excluded.awaiting_owner,
  reception_hold_until=excluded.reception_hold_until,client_run_id=excluded.client_run_id,
  runtime_started_at=excluded.runtime_started_at;
 return result;
end $function$;

-- The reviewed 152603 query; added lines read the status row and the newest runtime report.
create function public.session_peers(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; project text; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select project_ref into project from quorum_private.agents where id=p_agent_id;
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select a.id as agent_id,a.role,a.engine,a.project_ref,h.last_seen,
   jsonb_build_object('protocol_version',h.protocol_version,'skill_version',h.skill_version) as client,
   public.session_requirements() as required,
   (h.protocol_version is distinct from (public.session_requirements()->>'protocol_version')::integer
    or h.skill_version is distinct from public.session_requirements()->>'skill_version') as update_required,
   case when k.restarted then 'client_restarted_unrecovered'
        when pending.oldest<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen is null then 'unknown'
        when k.current and s.awaiting_owner>0 then 'awaiting_owner' else 'online' end as availability,
   case when k.restarted then 'run_changed_without_heartbeat'
        when pending.oldest<=now()-interval '60 seconds' then
         case when h.last_seen>now()-interval '60 seconds' and k.current and s.reception_hold_until>now()
          then 'reception_hold' else 'receipt_timeout' end
        when h.last_seen<=now()-interval '60 seconds' then 'heartbeat_timeout'
        when h.last_seen is null then 'heartbeat_not_supported' else null end as reason,
   case when k.current then s.awaiting_owner end as awaiting_owner,
   case when k.current and s.reception_hold_until>now() then s.reception_hold_until end as reception_hold_until,
   s.client_run_id is not null as client_run_reported
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_health h on h.agent_id=a.id
  left join quorum_private.session_status s on s.agent_id=a.id
  left join lateral (
   select min(dv.created_at) as oldest from quorum_private.deliveries dv
   where dv.recipient_id=a.id and dv.state='queued'
    and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=a.id and r.message_id=dv.message_id)
  ) pending on true
  -- The newest runtime self-report of this exact agent; written only by session_update_report.
  left join lateral (
   select (n.runtime_evidence->>'run_id')::uuid as run_id,(n.runtime_evidence->>'reported_at')::timestamptz as reported_at
   from quorum_private.update_notices n
   where n.recipient_id=a.id and n.runtime_evidence ? 'run_id' and n.runtime_evidence ? 'reported_at'
   order by (n.runtime_evidence->>'reported_at')::timestamptz desc limit 1
  ) report on true
  cross join lateral (
   select coalesce(s.reported_at>now()-interval '60 seconds',false) as current,
    coalesce(h.last_seen<=now()-interval '60 seconds' and s.client_run_id is not null
     and report.run_id<>s.client_run_id and report.reported_at>s.reported_at,false) as restarted
  ) k
  where a.quorum_id=q and a.project_ref=project and not a.revoked and not d.revoked and not m.revoked
   and a.engine in ('codex','claude','grok')
  order by a.id limit 128
 ) x;
 return result;
end $function$;

revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz),
 public.session_peers(uuid,text) from public,anon,authenticated,service_role;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz),
 public.session_peers(uuid,text) to authenticated;
