-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after 180000.
-- No required-release change, table access, model launch, installation or lifecycle grant.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 'public.session_inbox(uuid,text)','93b2f7f232bc3b318114192dac7a597bf8ab700cd0a4158daf081f94337f3e79',
 'public.session_ack(uuid,text,uuid)','122d58111a0e99120a547c2ccc48fd6c3589c193b52a30ad884f1e89c5c0585c',
 'quorum_private.call(text,jsonb,text)','ca718afbcda8682ade19ea6b246ccc2f1bfd5523eb7d1076cf4e9583e24f3ab7',
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.session_inbox_before_updates(uuid,text)') is not null then
  raise exception 'update_extension_present'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create table quorum_private.update_targets(
 id uuid primary key default gen_random_uuid(), quorum_id uuid not null references quorum_private.quorums(id),
 protocol_version integer not null, skill_version text not null, content_sha256 text not null,
 unique(quorum_id,content_sha256), check(protocol_version>0),
 check(skill_version~'^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'), check(content_sha256~'^[0-9a-f]{64}$')
);
create table quorum_private.update_operations(
 id uuid primary key default gen_random_uuid(), initiator_id uuid not null references quorum_private.agents(id),
 target_id uuid not null references quorum_private.update_targets(id), request_id text not null,
 queued integer not null default 0, existing integer not null default 0, failed integer not null default 0,
 unaddressable_members integer not null default 0, created_at timestamptz not null default now(),
 unique(initiator_id,request_id), check(length(request_id) between 1 and 128)
);
create table quorum_private.update_notices(
 id uuid primary key default gen_random_uuid(), target_id uuid not null references quorum_private.update_targets(id),
 recipient_id uuid not null references quorum_private.agents(id), message_id uuid not null unique references quorum_private.messages(id),
 queued_at timestamptz not null default now(), received_at timestamptz, refreshed_at timestamptz,
 active_at timestamptz, runtime_evidence jsonb, blocked_code text, unique(target_id,recipient_id)
);
create table quorum_private.update_recipients(
 operation_id uuid not null references quorum_private.update_operations(id), recipient_id uuid not null references quorum_private.agents(id),
 notice_id uuid references quorum_private.update_notices(id), failure_code text,
 primary key(operation_id,recipient_id), check((notice_id is null)<>(failure_code is null))
);
create table quorum_private.update_reports(
 notice_id uuid not null references quorum_private.update_notices(id), request_id text not null,
 event text not null, evidence jsonb not null, primary key(notice_id,request_id),
 check(length(request_id) between 1 and 128)
);
alter table quorum_private.update_targets enable row level security;
alter table quorum_private.update_operations enable row level security;
alter table quorum_private.update_notices enable row level security;
alter table quorum_private.update_recipients enable row level security;
alter table quorum_private.update_reports enable row level security;
revoke all on quorum_private.update_targets,quorum_private.update_operations,quorum_private.update_notices,
 quorum_private.update_recipients,quorum_private.update_reports from public,anon,authenticated,service_role;

-- Call only after the quorum row and protocol-specific blocking locks. A
-- completed revoke must be seen after a wait; later revokes wait for this RPC.
-- All maintenance writers take agent -> device -> membership in that order.
create function quorum_private.update_session_locked(p_agent_id uuid,p_session_ref text,p_quorum_id uuid)
returns void language plpgsql security definer set search_path=pg_catalog as $function$
declare actor quorum_private.agents%rowtype;
begin
 select * into actor from quorum_private.agents
  where id=p_agent_id and owner_id=auth.uid() and quorum_id=p_quorum_id for share;
 if not found then raise insufficient_privilege using message='session_binding_denied'; end if;
 perform 1 from quorum_private.devices where id=actor.device_id and owner_id=actor.owner_id for share;
 perform 1 from quorum_private.memberships where quorum_id=p_quorum_id and user_id=actor.owner_id for share;
 if quorum_private.native_session(p_agent_id,p_session_ref)<>p_quorum_id then
  raise insufficient_privilege using message='session_binding_denied'; end if;
end $function$;
revoke all on function quorum_private.update_session_locked(uuid,text,uuid) from public,anon,authenticated,service_role;

create function public.session_update_request(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_protocol_version integer,p_skill_version text,p_content_sha256 text,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; target quorum_private.update_targets%rowtype; op quorum_private.update_operations%rowtype;
 peer record; nid uuid; sent jsonb; failure text; queued_count integer:=0; reused integer:=0; failed_count integer:=0;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Match close's lock order before ownership, membership, target and message locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege using message='session_binding_denied'; end if;
 if p_protocol_version is null or p_protocol_version<1 or p_skill_version is null
  or p_skill_version!~'^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
  or p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'update_target_invalid'; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 perform pg_advisory_xact_lock(hashtextextended('update-request:'||p_agent_id||':'||p_request_id,0));
 select * into op from quorum_private.update_operations where initiator_id=p_agent_id and request_id=p_request_id;
 if found then
  perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
  select * into target from quorum_private.update_targets where id=op.target_id;
  if target.quorum_id<>q or target.protocol_version<>p_protocol_version or target.skill_version<>p_skill_version
   or target.content_sha256<>p_content_sha256 then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('operation_id',op.id,'queued',op.queued,'existing',op.existing,'failed',op.failed);
 end if;
 perform pg_advisory_xact_lock(hashtextextended('update-target:'||q||':'||p_content_sha256,0));
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 insert into quorum_private.update_targets(quorum_id,protocol_version,skill_version,content_sha256)
 values(q,p_protocol_version,p_skill_version,p_content_sha256) on conflict do nothing;
 select * into target from quorum_private.update_targets where quorum_id=q and content_sha256=p_content_sha256;
 if target.protocol_version<>p_protocol_version or target.skill_version<>p_skill_version then
  raise exception 'update_target_conflict'; end if;
 insert into quorum_private.update_operations(initiator_id,target_id,request_id)
 values(p_agent_id,target.id,p_request_id) returning * into op;
 -- This snapshot includes offline and same-role peers in every project/engine;
 -- unlike session_peers it has no health filter or arbitrary 128-participant cap.
 for peer in select a.id from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  where a.quorum_id=q and a.id<>p_agent_id and not a.revoked and not d.revoked and not m.revoked order by a.id
 loop
  select id into nid from quorum_private.update_notices where target_id=target.id and recipient_id=peer.id;
  failure:=null;
  if nid is not null then reused:=reused+1;
  else
   begin
    sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
     'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
     'body','Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
      ||'no installation, lifecycle or task permission is granted. Target protocol '||p_protocol_version
      ||', skill '||p_skill_version||', sha256 '||p_content_sha256||'.','priority','normal'),
     'update:'||target.id||':'||peer.id);
    update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
    insert into quorum_private.update_notices(target_id,recipient_id,message_id)
    values(target.id,peer.id,(sent->>'message_id')::uuid) returning id into nid;
    queued_count:=queued_count+1;
   exception when insufficient_privilege then failure:='recipient_unavailable'; failed_count:=failed_count+1;
    when raise_exception then
     if sqlerrm<>'recipient_unavailable' then raise; end if;
     failure:='recipient_unavailable'; failed_count:=failed_count+1;
   end;
  end if;
  insert into quorum_private.update_recipients values(op.id,peer.id,nid,failure);
 end loop;
 update quorum_private.update_operations set queued=queued_count,existing=reused,failed=failed_count,
  unaddressable_members=(select count(*) from quorum_private.memberships m where m.quorum_id=q and not m.revoked
   and not exists(select 1 from quorum_private.agents a join quorum_private.devices d on d.id=a.device_id
    where a.quorum_id=q and a.owner_id=m.user_id and not a.revoked and not d.revoked))
 where id=op.id;
 return jsonb_build_object('operation_id',op.id,'queued',queued_count,'existing',reused,'failed',failed_count);
end $function$;

create function public.session_update_status(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_operation_id uuid default null,p_notice_id uuid default null,p_after_agent uuid default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; target quorum_private.update_targets%rowtype; n quorum_private.update_notices%rowtype;
 op quorum_private.update_operations%rowtype; items jsonb; cursor uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id or (p_operation_id is null)=(p_notice_id is null) then
  raise insufficient_privilege using message='update_status_denied'; end if;
 if p_notice_id is not null then
  select * into n from quorum_private.update_notices where id=p_notice_id and recipient_id=p_agent_id;
  if not found then raise insufficient_privilege using message='update_notice_denied'; end if;
  select * into target from quorum_private.update_targets where id=n.target_id and quorum_id=q;
  if not found then raise insufficient_privilege; end if;
  return jsonb_build_object('notice_id',n.id,'recipient_agent_id',p_agent_id,'quorum_id',q,
   'target',jsonb_build_object('protocol_version',target.protocol_version,'skill_version',target.skill_version,
    'content_sha256',target.content_sha256),'queued_at',n.queued_at,'received_at',n.received_at,
   'refreshed_at',n.refreshed_at,'active_at',n.active_at,'runtime_report',n.runtime_evidence,'blocked_code',n.blocked_code,
   'state',case when n.blocked_code is not null then 'blocked' when n.active_at is not null then 'active_reported' when n.refreshed_at is not null then 'refreshed'
    when n.received_at is not null then 'owner_action_required' else 'queued' end);
 end if;
 select * into op from quorum_private.update_operations where id=p_operation_id and initiator_id=p_agent_id;
 if not found then raise insufficient_privilege using message='update_status_denied'; end if;
 select * into target from quorum_private.update_targets where id=op.target_id and quorum_id=q;
 if not found then raise insufficient_privilege; end if;
 select coalesce(jsonb_agg(to_jsonb(x) order by x.agent_id),'[]'::jsonb) into items from (
  select r.recipient_id as agent_id,notice.id as notice_id,r.failure_code,notice.queued_at,notice.received_at,notice.refreshed_at,notice.active_at,notice.blocked_code,
   notice.runtime_evidence as runtime_report,
   case when r.failure_code is not null then 'unavailable' when notice.blocked_code is not null then 'blocked' when notice.active_at is not null then 'active_reported'
    when notice.refreshed_at is not null then 'refreshed' when notice.received_at is not null then 'owner_action_required' else 'queued' end as state
  from quorum_private.update_recipients r left join quorum_private.update_notices notice on notice.id=r.notice_id
  where r.operation_id=op.id and (p_after_agent is null or r.recipient_id>p_after_agent)
  order by r.recipient_id limit 64
 ) x;
 if jsonb_array_length(items)=64 then
  cursor:=(items->63->>'agent_id')::uuid;
  if not exists(select 1 from quorum_private.update_recipients where operation_id=op.id and recipient_id>cursor) then cursor:=null; end if;
 end if;
 return jsonb_build_object('operation_id',op.id,'target',jsonb_build_object('protocol_version',target.protocol_version,
  'skill_version',target.skill_version,'content_sha256',target.content_sha256),'participants',items,'next_after_agent',cursor,
  'unaddressable_members',op.unaddressable_members,'all_participants_upgraded',false);
end $function$;

create function public.session_update_report(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_notice_id uuid,p_event text,p_evidence jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; n quorum_private.update_notices%rowtype; target quorum_private.update_targets%rowtype;
 expected jsonb; previous quorum_private.update_reports%rowtype;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Keep close's quorum-first ordering before the notice and authority row locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege; end if;
 select * into n from quorum_private.update_notices where id=p_notice_id and recipient_id=p_agent_id for update;
 if not found then raise insufficient_privilege using message='update_notice_denied'; end if;
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 select * into target from quorum_private.update_targets where id=n.target_id and quorum_id=q;
 if not found then raise insufficient_privilege; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 if p_event not in ('refreshed','active','blocked') or p_event is null then raise exception 'update_event_invalid'; end if;
 expected:=jsonb_build_object('protocol_version',target.protocol_version,'skill_version',target.skill_version,
  'content_sha256',target.content_sha256);
 if p_event='blocked' then
  if p_evidence->>'code' is null or p_evidence->>'code' not in ('update_tool_missing','update_fetch_failed',
   'update_invalid_copy','update_validation_failed','update_cache_conflict','update_target_changed',
   'update_in_progress','update_unsafe_cache') then raise exception 'update_evidence_invalid'; end if;
  expected:=jsonb_build_object('code',p_evidence->>'code');
 end if;
 if p_event='active' then
  if p_evidence->>'run_id' is null or p_evidence->>'run_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
   raise exception 'update_evidence_invalid'; end if;
  expected:=expected||jsonb_build_object('run_id',p_evidence->>'run_id');
  if public.session_requirements()<>expected-'content_sha256'::text-'run_id'::text then raise exception 'skill_update_required'; end if;
 end if;
 if p_evidence is distinct from expected then raise exception 'update_evidence_invalid'; end if;
 select * into previous from quorum_private.update_reports where notice_id=n.id and request_id=p_request_id;
 if found then
  if previous.event<>p_event or previous.evidence<>p_evidence then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('recorded',true);
 end if;
 insert into quorum_private.update_reports values(n.id,p_request_id,p_event,p_evidence);
 update quorum_private.update_notices set received_at=coalesce(received_at,now()),
  refreshed_at=case when p_event='refreshed' then coalesce(refreshed_at,now()) else refreshed_at end,
  active_at=case when p_event='active' then now() else active_at end,
  runtime_evidence=case when p_event='active' then p_evidence||jsonb_build_object('reported_at',now()) else runtime_evidence end,
  blocked_code=case when p_event='blocked' then p_evidence->>'code' else null end
 where id=n.id;
 return jsonb_build_object('recorded',true);
end $function$;

-- Wrap rather than copy the reviewed inbox/receipt bodies. Later metadata wrappers
-- can compose this contract without changing message selection or receipt meaning.
alter function public.session_inbox(uuid,text) set schema quorum_private;
alter function quorum_private.session_inbox(uuid,text) rename to session_inbox_before_updates;
revoke all on function quorum_private.session_inbox_before_updates(uuid,text) from public,anon,authenticated,service_role;
create function public.session_inbox(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb;
begin
 select coalesce(jsonb_agg(item.value||case when n.id is null then '{}'::jsonb else jsonb_build_object('maintenance',
  jsonb_build_object('kind','quorum.update','notice_id',n.id,'target',jsonb_build_object('protocol_version',t.protocol_version,
   'skill_version',t.skill_version,'content_sha256',t.content_sha256))) end),'[]'::jsonb) into result
 from jsonb_array_elements(quorum_private.session_inbox_before_updates(p_agent_id,p_session_ref)) item
 left join quorum_private.update_notices n on n.message_id=(item.value->>'id')::uuid
 left join quorum_private.update_targets t on t.id=n.target_id;
 return result;
end $function$;
alter function public.session_ack(uuid,text,uuid) set schema quorum_private;
alter function quorum_private.session_ack(uuid,text,uuid) rename to session_ack_before_updates;
revoke all on function quorum_private.session_ack_before_updates(uuid,text,uuid) from public,anon,authenticated,service_role;
create function public.session_ack(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb; q uuid;
begin
 -- Ordinary receipts retain the exact reviewed implementation/semantics.
 if not exists(select 1 from quorum_private.update_notices where message_id=p_message_id and recipient_id=p_agent_id) then
  return quorum_private.session_ack_before_updates(p_agent_id,p_session_ref,p_message_id); end if;
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 select quorum_id into q from quorum_private.agents where id=p_agent_id;
 perform 1 from quorum_private.quorums where id=q for share;
 result:=quorum_private.session_ack_before_updates(p_agent_id,p_session_ref,p_message_id);
 -- The reviewed receipt may wait on a receipt/health write. Revalidate after
 -- it returns; a refusal rolls back both original receipt and new phase writes.
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 update quorum_private.update_notices set received_at=coalesce(received_at,now())
  where message_id=p_message_id and recipient_id=p_agent_id;
 return result;
end $function$;
revoke all on function public.session_update_request(uuid,text,uuid,integer,text,text,text),
 public.session_update_status(uuid,text,uuid,uuid,uuid,uuid),public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text),
 public.session_inbox(uuid,text),public.session_ack(uuid,text,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_update_request(uuid,text,uuid,integer,text,text,text),
 public.session_update_status(uuid,text,uuid,uuid,uuid,uuid),public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text),
 public.session_inbox(uuid,text),public.session_ack(uuid,text,uuid) to authenticated;
