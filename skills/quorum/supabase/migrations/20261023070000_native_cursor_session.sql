-- Owner-applied engine extension. No new table, policy, grant or dispatcher.
-- Run in one transaction, after the close serialization (180000).
-- Installation, invitation acceptance and the listener never apply this file.
-- Each body below is its reviewed predecessor with only engine 'cursor' added.
do $migration$
declare
 reviewed constant jsonb:=jsonb_build_object(
  -- Close serialization (180000) and the mandatory 0.3.0/2 release are applied.
  'quorum_private.call(text,jsonb,text)','ca718afbcda8682ade19ea6b246ccc2f1bfd5523eb7d1076cf4e9583e24f3ab7',
  'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef',
  -- The bodies this file replaces; a drifted or already extended one fails closed.
  'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
  'quorum_private.receive_wakeup(text)','2f82e7bad7cedc81609fab91f8b4d680fe40a71216ce89c90fa66317ac24eb64',
  'public.session_heartbeat(uuid,text,uuid,integer,text)','944211db94640dca91d75e007a4c5de8a1ee28511ea6c78a5b3fb2f7d96768c5',
  'public.session_peers(uuid,text)','e6bef8353f48cb6322f76dcb024eeb4972d2881eeb65b28c62023e2d2f8b31cd');
 f text;
begin
 for f in select jsonb_object_keys(reviewed) loop
  if to_regprocedure(f) is null or encode(sha256(convert_to((select prosrc from pg_proc
     where oid=to_regprocedure(f)),'UTF8')),'hex')<>reviewed->>f then
   raise exception 'reviewed_dispatcher_changed: %', f; end if;
 end loop;
end $migration$;

alter table quorum_private.agents drop constraint agents_engine_check;
alter table quorum_private.agents add constraint agents_engine_check
 check (engine in ('claude','codex','grok','cursor'));

create or replace function quorum_private.native_session(p_agent_id uuid, p_session_ref text)
 returns uuid
 language plpgsql
 security definer
 set search_path to 'pg_catalog'
as $function$
declare q uuid;
begin
 if auth.uid() is null or not quorum_private.verified_actor(auth.uid()) then
  raise insufficient_privilege using message='github_identity_required'; end if;
 q:=quorum_private.owned_agent(p_agent_id,auth.uid());
 if not exists(select 1 from quorum_private.agents where id=p_agent_id and engine in ('codex','claude','grok','cursor') and session_ref=p_session_ref) then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 return q;
end $function$;
revoke all on function quorum_private.native_session(uuid,text) from public,anon,authenticated,service_role;

create or replace function quorum_private.receive_wakeup(p_topic text)
returns boolean language sql stable security definer set search_path=pg_catalog as $$
 select auth.uid() is not null
  and quorum_private.verified_actor(auth.uid())
  and exists(
   select 1 from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where p_topic='quorum:agent:'||a.id::text
    and a.owner_id=auth.uid() and d.owner_id=auth.uid()
    and a.engine in ('codex','claude','grok','cursor') and not a.revoked and not d.revoked and not m.revoked
  )
$$;
revoke all on function quorum_private.receive_wakeup(text) from public,anon,authenticated,service_role;
grant execute on function quorum_private.receive_wakeup(text) to authenticated;

create or replace function public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; own quorum_private.agents%rowtype; peer record; fresh integer; sent jsonb; required jsonb;
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
  for peer in
   select a.id from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
    and a.engine in ('codex','claude','grok','cursor') and not a.revoked and not d.revoked and not m.revoked
   order by a.id limit 128
  loop
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
    'body','Hello! Listener connected: '||own.role||' (project '||own.project_ref||'). Transport notice; no reply required.'),
    'hello:'||p_agent_id||':'||p_connection_id||':'||peer.id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
  end loop;
 end if;
 return jsonb_build_object('online',true,'new_connection',fresh=1,'last_seen',now(),'required',required);
end $function$;

create or replace function public.session_peers(p_agent_id uuid,p_session_ref text)
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
   case when pending.oldest<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen is null then 'unknown' else 'online' end as availability,
   case when pending.oldest<=now()-interval '60 seconds' then 'receipt_timeout'
        when h.last_seen<=now()-interval '60 seconds' then 'heartbeat_timeout'
        when h.last_seen is null then 'heartbeat_not_supported' else null end as reason
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_health h on h.agent_id=a.id
  left join lateral (
   select min(dv.created_at) as oldest from quorum_private.deliveries dv
   where dv.recipient_id=a.id and dv.state='queued'
    and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=a.id and r.message_id=dv.message_id)
  ) pending on true
  where a.quorum_id=q and a.project_ref=project and not a.revoked and not d.revoked and not m.revoked
   and a.engine in ('codex','claude','grok','cursor')
  order by a.id limit 128
 ) x;
 return result;
end $function$;
revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text), public.session_peers(uuid,text) from public,anon,authenticated;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text), public.session_peers(uuid,text) to authenticated;
