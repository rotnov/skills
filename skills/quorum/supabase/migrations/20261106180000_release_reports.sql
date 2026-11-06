-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106170000_delivery_statuses.sql.
-- No required-release change, table grant, policy, task authority or model launch. (#168)
--
-- Phase one of identifying a release by its content hash. A listener reports the
-- content SHA-256 of the code it runs, and a member reads those reports next to the
-- fleet target. Reports are self-reports and facts, never grants: nothing here or
-- anywhere else reads one to decide a permission or to refuse a listener, and being
-- behind the target is only shown. No existing function body changes, so
-- session_heartbeat, session_peers, session_requirements and every older client
-- behave exactly as before. In particular the required release stays as it is, and a
-- client that still sends the fixed version keeps passing its check.
--
-- A report names the run that made it. The view believes it only while that run is
-- still the one the agent's latest heartbeat named, so a listener replaced by a client
-- that does not report reads as unreported instead of as its predecessor's hash. An
-- older listener can still be seen through the newest runtime report it made for an
-- owner update, under the same run rule.
--
-- The fleet target is the newest target an owner requested for this quorum. It says
-- what the owner last asked for, not that any newer release exists. Hashes have no
-- order, so `behind` means only "not on that target".
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The binding check both new functions rely on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The only writer of the update operations and targets read for the fleet target.
 'public.session_update_request(uuid,text,uuid,integer,text,text,text)','07fbf7a1bb01ea3d1ee575750381a52cea638989cbf058bcdf6655de605dc5a1',
 -- The only writer of runtime_evidence, read as the older listener's report.
 'public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)','abce7c4012a3221f7ddafebb202c6468ed0bf90e5795f33babdfbb4a3af68060',
 -- The mandatory release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regclass('quorum_private.session_release') is not null then
  raise exception 'release_reports_present'; end if;
 if to_regclass('quorum_private.delivery_statuses') is null then
  raise exception 'delivery_statuses_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create table quorum_private.session_release(
 agent_id uuid primary key references quorum_private.agents(id),
 content_sha256 text not null check(content_sha256~'^[0-9a-f]{64}$'),
 client_run_id uuid,
 reported_at timestamptz not null default now());
alter table quorum_private.session_release enable row level security;
revoke all on quorum_private.session_release from public,anon,authenticated,service_role;

-- One row per agent: the latest report replaces the one before it.
create function public.session_release_report(p_agent_id uuid,p_session_ref text,p_content_sha256 text,
 p_client_run_id uuid default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 perform quorum_private.native_session(p_agent_id,p_session_ref);
 if p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'release_report_invalid'; end if;
 insert into quorum_private.session_release(agent_id,content_sha256,client_run_id,reported_at)
 values(p_agent_id,p_content_sha256,p_client_run_id,now())
 on conflict(agent_id) do update set content_sha256=excluded.content_sha256,client_run_id=excluded.client_run_id,
  reported_at=excluded.reported_at;
 return jsonb_build_object('recorded',true);
end $function$;

-- Without an operation: the agents session_peers shows this caller. With one: a page of
-- that operation's recipients, for its initiator alone, as session_update_status does.
create function public.session_release_status(p_agent_id uuid,p_session_ref text,p_operation_id uuid default null,
 p_after_agent uuid default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; project text; target jsonb; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select project_ref into project from quorum_private.agents where id=p_agent_id;
 if p_operation_id is not null and not exists(select 1 from quorum_private.update_operations o
  join quorum_private.update_targets t on t.id=o.target_id
  where o.id=p_operation_id and o.initiator_id=p_agent_id and t.quorum_id=q) then
  raise insufficient_privilege using message='update_status_denied'; end if;
 select jsonb_build_object('protocol_version',t.protocol_version,'content_sha256',t.content_sha256,'requested_at',o.created_at)
  into target from quorum_private.update_operations o join quorum_private.update_targets t on t.id=o.target_id
  where t.quorum_id=q order by o.created_at desc,o.id desc limit 1;
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select a.id as agent_id,
   case k.via when 'heartbeat' then r.content_sha256 when 'update_report' then report.content_sha256 end as content_sha256,
   case k.via when 'heartbeat' then r.reported_at when 'update_report' then report.reported_at end as reported_at,
   k.via as reported_via
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_status s on s.agent_id=a.id
  left join quorum_private.session_release r on r.agent_id=a.id
  -- The newest runtime self-report of this exact agent; written only by session_update_report.
  left join lateral (
   select n.runtime_evidence->>'content_sha256' as content_sha256,(n.runtime_evidence->>'run_id')::uuid as run_id,
    (n.runtime_evidence->>'reported_at')::timestamptz as reported_at
   from quorum_private.update_notices n
   where n.recipient_id=a.id and n.runtime_evidence ? 'run_id' and n.runtime_evidence ? 'reported_at'
   order by (n.runtime_evidence->>'reported_at')::timestamptz desc limit 1
  ) report on true
  cross join lateral (
   select case when r.agent_id is not null and r.client_run_id is not distinct from s.client_run_id then 'heartbeat'
    when report.run_id=s.client_run_id then 'update_report' end as via
  ) k
  where a.quorum_id=q and case when p_operation_id is null
    then a.project_ref=project and not a.revoked and not d.revoked and not m.revoked and a.engine in ('codex','claude','grok')
    else exists(select 1 from quorum_private.update_recipients u where u.operation_id=p_operation_id and u.recipient_id=a.id)
     and (p_after_agent is null or a.id>p_after_agent) end
  order by a.id limit case when p_operation_id is null then 128 else 64 end
 ) x;
 return jsonb_build_object('target',target,'agents',result);
end $function$;

revoke all on function public.session_release_report(uuid,text,text,uuid),
 public.session_release_status(uuid,text,uuid,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_release_report(uuid,text,text,uuid),
 public.session_release_status(uuid,text,uuid,uuid) to authenticated;
