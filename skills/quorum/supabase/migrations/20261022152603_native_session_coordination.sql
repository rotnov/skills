-- Native listener health is separate from registration, delivery and task acceptance.
create table if not exists quorum_private.session_health (
 agent_id uuid primary key references quorum_private.agents(id),
 last_seen timestamptz not null default now(),
 protocol_version integer, skill_version text
);
create table if not exists quorum_private.session_connections (
 agent_id uuid references quorum_private.agents(id), connection_id uuid,
 primary key(agent_id,connection_id)
);
create table if not exists quorum_private.session_notices (
 message_id uuid primary key references quorum_private.messages(id),
 agent_id uuid not null references quorum_private.agents(id)
);
alter table quorum_private.session_health enable row level security;
alter table quorum_private.session_connections enable row level security;
alter table quorum_private.session_notices enable row level security;
revoke all on quorum_private.session_health, quorum_private.session_connections,
 quorum_private.session_notices from public, anon, authenticated;

-- Public release metadata only; require a real authenticated GitHub identity.
create or replace function public.session_requirements()
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 if auth.uid() is null or not exists(select 1 from auth.identities where user_id=auth.uid() and provider='github') then
  raise insufficient_privilege using message='github_identity_required'; end if;
 return jsonb_build_object('protocol_version',1,'skill_version','0.2.0');
end $function$;

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
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
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

-- A real delivery receipt is health activity too. This prevents a slow queue/ACK
-- round trip from reporting an actively delivering listener as unavailable.
create or replace function public.session_ack(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id) then
  raise insufficient_privilege using message='message_denied'; end if;
 insert into quorum_private.session_receipts(agent_id,message_id) values(p_agent_id,p_message_id) on conflict do nothing;
 insert into quorum_private.session_health(agent_id,last_seen) values(p_agent_id,now())
 on conflict(agent_id) do update set last_seen=excluded.last_seen;
 return jsonb_build_object('queued',true);
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
   and a.engine in ('codex','claude','grok')
  order by a.id limit 128
 ) x;
 return result;
end $function$;

create or replace function public.session_inbox(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body,a.role as sender_role,
   exists(select 1 from quorum_private.session_notices n where n.message_id=m.id) as notification
  from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  join quorum_private.agents a on a.id=m.sender_id
  where m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)
  order by d.created_at,m.conversation_id,m.sequence limit 1
 ) x;
 return result;
end $function$;
revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text), public.session_peers(uuid,text), public.session_requirements() from public,anon,authenticated;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text), public.session_peers(uuid,text), public.session_requirements() to authenticated;
