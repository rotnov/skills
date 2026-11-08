-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106180000_release_reports.sql.
-- No table grant, policy, task authority or model launch. (#172, phase two of #168)
--
-- A release is identified only by its content hash, and protocol_version is the only
-- compatibility gate on the wire. The required release drops the fixed skill_version, and
-- nothing the server stores, sends or compares carries it any more.
--
-- Copies already on protocol 2 still send the number. Every public signature keeps its
-- `p_skill_version` argument, now defaulted, so those calls are accepted and the value is
-- never read. A copy that no longer has a number simply leaves it out.
--
-- 1. session_heartbeat is a thin wrapper. The peer-status heartbeat moves to a private
--    function and is called unchanged with NULL in place of the number. The reviewed core
--    compares that NULL with the requirement's missing number and stores NULL in
--    session_health. Nothing reports a number for a client, and no core is copied.
-- 2. session_peers, session_inbox and session_update_status are thin wrappers: each calls
--    its moved reviewed body and removes the retired field. session_peers also derives
--    `update_required` from the protocol alone.
-- 3. session_update_request and session_update_report are replaced, because neither
--    shape fits a wrapper: the request writes the number into the target and the notice
--    text, and the report compares evidence and the required release with the target by
--    exact equality. Both are the reviewed 20261030140000 bodies with only the lines marked
--    "#172" changed. A report hears evidence without the number. Their reviewed bodies
--    stay private for reference.
-- 4. session_requirements returns only protocol_version, behind the same verified-actor gate.
-- 5. Stored data drops the number: update_targets.skill_version becomes nullable and is
--    cleared, runtime and report evidence lose the key, session_health forgets it, and the
--    server's own update notice text is rewritten to name the protocol and hash only.
--
-- A copy from before #168 compares the requirement's number and refuses every check
-- after this. Apply it only once every listener reports a running hash (#172).
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The peer-status heartbeat (160000), moved and called unchanged.
 'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','2957604dce5af878d0da3713b17545e11a745a95c4fc1aad95c222f6aa5394a2',
 -- The Hello wrapper (150000) and the core (120000) it calls. The core's release check is
 -- what reads a NULL number as matching a requirement that has none.
 'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','be24c31bc064a0f4dc70bb4d98086525a49738e27749bb1eb53aa5ce40dc6c76',
 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','b3791a5b3b5a93433a6a3dd854af5ffed93e9843bc905b811e201b20d55873a5',
 -- The peer view (160000), the inbox projection and the update status (140000), each moved and called unchanged.
 'public.session_peers(uuid,text)','7379f998ad449d00d1078eebad6e98876f5d950d1faa9d36d97b9e3c96a5438a',
 'public.session_inbox(uuid,text)','19b0ea41c4c595df85f71dd57e25daddc2fd3bb3343aad49aa3b82b29031bfe6',
 'public.session_update_status(uuid,text,uuid,uuid,uuid,uuid)','cf0cc30367424e8e731df31a88a3740db846809e37047136d3c911af22182fef',
 -- The two update writers (140000) whose bodies are replaced below.
 'public.session_update_request(uuid,text,uuid,integer,text,text,text)','07fbf7a1bb01ea3d1ee575750381a52cea638989cbf058bcdf6655de605dc5a1',
 'public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)','abce7c4012a3221f7ddafebb202c6468ed0bf90e5795f33babdfbb4a3af68060',
 -- What the replaced writers rely on: the binding check, the locked recheck and the dispatcher.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 'quorum_private.update_session_locked(uuid,text,uuid)','106b03f6b972dfa68e6a1bf088a3741f2721e2036fa1b655085fb5290ca19a12',
 'quorum_private.call(text,jsonb,text)','c7e71c9dcc515b6f0eb1f5b64b0e4cb431c94422b1bb763fc2687f5c2b86c99a',
 -- The mandatory 0.3.0/2 release (170000), replaced below.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)') is not null
    or to_regprocedure('quorum_private.session_update_request_before_hash_only(uuid,text,uuid,integer,text,text,text)') is not null then
  raise exception 'hash_only_release_present'; end if;
 if to_regclass('quorum_private.session_release') is null then
  raise exception 'release_reports_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- 5. Stored data. The column stays, nullable and empty, because the moved reviewed bodies still name it.
alter table quorum_private.update_targets alter column skill_version drop not null;
update quorum_private.update_targets set skill_version=null where skill_version is not null;
update quorum_private.update_notices set runtime_evidence=runtime_evidence-'skill_version'
 where runtime_evidence ? 'skill_version';
update quorum_private.update_reports set evidence=evidence-'skill_version' where evidence ? 'skill_version';
-- No streak trigger fires: it follows writes of last_seen only.
update quorum_private.session_health set skill_version=null where skill_version is not null;
-- Only the server's own update notices, and only their exact fixed sentence.
update quorum_private.messages m set body=regexp_replace(m.body,
 '^(Quorum maintenance proposal\. .* Target protocol [0-9]{1,10}), skill [0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}(, sha256 [0-9a-f]{64}\.)$','\1\2')
 from quorum_private.update_notices n
 where n.message_id=m.id
  and m.body~'^Quorum maintenance proposal\. .* Target protocol [0-9]{1,10}, skill [0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}, sha256 [0-9a-f]{64}\.$';

alter function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz) set schema quorum_private;
alter function quorum_private.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)
 rename to session_heartbeat_before_hash_only;
alter function public.session_peers(uuid,text) set schema quorum_private;
alter function quorum_private.session_peers(uuid,text) rename to session_peers_before_hash_only;
alter function public.session_inbox(uuid,text) set schema quorum_private;
alter function quorum_private.session_inbox(uuid,text) rename to session_inbox_before_hash_only;
alter function public.session_update_status(uuid,text,uuid,uuid,uuid,uuid) set schema quorum_private;
alter function quorum_private.session_update_status(uuid,text,uuid,uuid,uuid,uuid) rename to session_update_status_before_hash_only;
alter function public.session_update_request(uuid,text,uuid,integer,text,text,text) set schema quorum_private;
alter function quorum_private.session_update_request(uuid,text,uuid,integer,text,text,text)
 rename to session_update_request_before_hash_only;
alter function public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text) set schema quorum_private;
alter function quorum_private.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)
 rename to session_update_report_before_hash_only;
revoke all on function quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz),
 quorum_private.session_peers_before_hash_only(uuid,text),quorum_private.session_inbox_before_hash_only(uuid,text),
 quorum_private.session_update_status_before_hash_only(uuid,text,uuid,uuid,uuid,uuid),
 quorum_private.session_update_request_before_hash_only(uuid,text,uuid,integer,text,text,text),
 quorum_private.session_update_report_before_hash_only(uuid,text,uuid,uuid,text,jsonb,text)
 from public,anon,authenticated,service_role;

-- 4. The same audience as before; the release is the protocol alone.
create or replace function public.session_requirements()
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 if not quorum_private.verified_actor(auth.uid()) then
  raise insufficient_privilege using message='github_identity_required'; end if;
 return jsonb_build_object('protocol_version',2);
end $function$;
revoke all on function public.session_requirements() from public,anon,authenticated;
grant execute on function public.session_requirements() to authenticated;

-- 1. One public signature, as before; the number is now optional and never passed on.
create function public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text default null, p_awaiting_owner integer default null,
 p_reception_hold_until timestamptz default null, p_client_run_id uuid default null,
 p_runtime_started_at timestamptz default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 -- The reviewed heartbeat validates the binding, the protocol and the status, and writes everything.
 return quorum_private.session_heartbeat_before_hash_only(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,null,p_awaiting_owner,p_reception_hold_until,p_client_run_id,p_runtime_started_at);
end $function$;

-- 2. The reviewed peer view, each row without the number and with the protocol as the only gate.
create function public.session_peers(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare required jsonb; result jsonb;
begin
 result:=quorum_private.session_peers_before_hash_only(p_agent_id,p_session_ref);
 required:=public.session_requirements();
 select coalesce(jsonb_agg(peer.value||jsonb_build_object('client',(peer.value->'client')-'skill_version',
   'required',required,'update_required',(peer.value#>>'{client,protocol_version}')::integer
    is distinct from (required->>'protocol_version')::integer) order by peer.ordinality),'[]'::jsonb) into result
 from jsonb_array_elements(result) with ordinality peer;
 return result;
end $function$;

create function public.session_inbox(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb;
begin
 select coalesce(jsonb_agg(item.value#-'{maintenance,target,skill_version}' order by item.ordinality),'[]'::jsonb)
  into result
 from jsonb_array_elements(quorum_private.session_inbox_before_hash_only(p_agent_id,p_session_ref)) with ordinality item;
 return result;
end $function$;

create function public.session_update_status(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_operation_id uuid default null,p_notice_id uuid default null,p_after_agent uuid default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 return quorum_private.session_update_status_before_hash_only(p_agent_id,p_session_ref,p_quorum_id,
  p_operation_id,p_notice_id,p_after_agent)#-'{target,skill_version}';
end $function$;

-- 3. The reviewed 140000 request; lines marked #172 stop validating, storing and sending the number.
create function public.session_update_request(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_protocol_version integer,p_skill_version text default null,p_content_sha256 text default null,p_request_id text default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; target quorum_private.update_targets%rowtype; op quorum_private.update_operations%rowtype;
 peer record; nid uuid; sent jsonb; failure text; queued_count integer:=0; reused integer:=0; failed_count integer:=0;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Match close's lock order before ownership, membership, target and message locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege using message='session_binding_denied'; end if;
 if p_protocol_version is null or p_protocol_version<1 -- #172: p_skill_version is never read
  or p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'update_target_invalid'; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 perform pg_advisory_xact_lock(hashtextextended('update-request:'||p_agent_id||':'||p_request_id,0));
 select * into op from quorum_private.update_operations where initiator_id=p_agent_id and request_id=p_request_id;
 if found then
  perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
  select * into target from quorum_private.update_targets where id=op.target_id;
  if target.quorum_id<>q or target.protocol_version<>p_protocol_version -- #172
   or target.content_sha256<>p_content_sha256 then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('operation_id',op.id,'queued',op.queued,'existing',op.existing,'failed',op.failed);
 end if;
 perform pg_advisory_xact_lock(hashtextextended('update-target:'||q||':'||p_content_sha256,0));
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 insert into quorum_private.update_targets(quorum_id,protocol_version,content_sha256) -- #172
 values(q,p_protocol_version,p_content_sha256) on conflict do nothing; -- #172
 select * into target from quorum_private.update_targets where quorum_id=q and content_sha256=p_content_sha256;
 if target.protocol_version<>p_protocol_version then -- #172
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
      ||', sha256 '||p_content_sha256||'.','priority','normal'), -- #172
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

-- 3. The reviewed 140000 report; lines marked #172 compare evidence without the number. The
-- required release is now the protocol alone, so the unchanged `active` check compares only that.
create function public.session_update_report(p_agent_id uuid,p_session_ref text,p_quorum_id uuid,
 p_notice_id uuid,p_event text,p_evidence jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; n quorum_private.update_notices%rowtype; target quorum_private.update_targets%rowtype;
 expected jsonb; previous quorum_private.update_reports%rowtype;
 -- #172: a copy that still sends the number is heard without it.
 evidence jsonb:=case when jsonb_typeof(p_evidence)='object' then p_evidence-'skill_version' else p_evidence end; -- #172
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
 expected:=jsonb_build_object('protocol_version',target.protocol_version, -- #172
  'content_sha256',target.content_sha256);
 if p_event='blocked' then
  if evidence->>'code' is null or evidence->>'code' not in ('update_tool_missing','update_fetch_failed', -- #172
   'update_invalid_copy','update_validation_failed','update_cache_conflict','update_target_changed',
   'update_in_progress','update_unsafe_cache') then raise exception 'update_evidence_invalid'; end if;
  expected:=jsonb_build_object('code',evidence->>'code'); -- #172
 end if;
 if p_event='active' then
  if evidence->>'run_id' is null or evidence->>'run_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then -- #172
   raise exception 'update_evidence_invalid'; end if;
  expected:=expected||jsonb_build_object('run_id',evidence->>'run_id'); -- #172
  if public.session_requirements()<>expected-'content_sha256'::text-'run_id'::text then raise exception 'skill_update_required'; end if;
 end if;
 if evidence is distinct from expected then raise exception 'update_evidence_invalid'; end if; -- #172
 select * into previous from quorum_private.update_reports where notice_id=n.id and request_id=p_request_id;
 if found then
  if previous.event<>p_event or previous.evidence<>evidence then raise exception 'idempotency_conflict'; end if; -- #172
  return jsonb_build_object('recorded',true);
 end if;
 insert into quorum_private.update_reports values(n.id,p_request_id,p_event,evidence); -- #172
 update quorum_private.update_notices set received_at=coalesce(received_at,now()),
  refreshed_at=case when p_event='refreshed' then coalesce(refreshed_at,now()) else refreshed_at end,
  active_at=case when p_event='active' then now() else active_at end,
  runtime_evidence=case when p_event='active' then evidence||jsonb_build_object('reported_at',now()) else runtime_evidence end, -- #172
  blocked_code=case when p_event='blocked' then evidence->>'code' else null end -- #172
 where id=n.id;
 return jsonb_build_object('recorded',true);
end $function$;

revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz),
 public.session_peers(uuid,text),public.session_inbox(uuid,text),public.session_update_status(uuid,text,uuid,uuid,uuid,uuid),
 public.session_update_request(uuid,text,uuid,integer,text,text,text),
 public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text) from public,anon,authenticated,service_role;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz),
 public.session_peers(uuid,text),public.session_inbox(uuid,text),public.session_update_status(uuid,text,uuid,uuid,uuid,uuid),
 public.session_update_request(uuid,text,uuid,integer,text,text,text),
 public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text) to authenticated;
