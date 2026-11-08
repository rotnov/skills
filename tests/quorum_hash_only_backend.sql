-- Disposable PostgreSQL only: a release identified by its content hash alone (#172).
-- Execute inside BEGIN/ROLLBACK after 20261108120000_hash_only_release.sql, following
-- quorum_hash_only_before_backend.sql in the same transaction.
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 fixture jsonb:=(select jsonb_object_agg(key,value) from hash_only_fixture);
 owner_id uuid:=(fixture->>'owner')::uuid; q uuid:=(fixture->>'quorum')::uuid;
 lead uuid:=(fixture->>'lead')::uuid; lead_sid text:=fixture->>'lead_sid';
 peer uuid:=(fixture->>'peer')::uuid; peer_sid text:=fixture->>'peer_sid';
 old_run uuid:=(fixture->>'run_id')::uuid; old_digest text:=fixture->>'digest'; old_notice uuid:=(fixture->>'notice')::uuid;
 old_release text:=fixture->>'old_release';
 dev uuid:=gen_random_uuid(); fresh uuid:=gen_random_uuid(); fresh_sid text:=gen_random_uuid()::text;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer;
 digest text:=repeat('e',64); run_id uuid:=gen_random_uuid();
 result jsonb; row_view jsonb; op uuid; notice uuid; body text; denied boolean; seen integer;
begin
 if required<>jsonb_build_object('protocol_version',p) then raise exception 'runner_release_wrong: %',required; end if;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- Signatures and privileges: one public signature each, moved bodies private, authenticated only.
 if to_regprocedure('quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)') is null
    or to_regprocedure('quorum_private.session_update_request_before_hash_only(uuid,text,uuid,integer,text,text,text)') is null
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_hash_only(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_peers_before_hash_only(uuid,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_inbox_before_hash_only(uuid,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_update_status_before_hash_only(uuid,text,uuid,uuid,uuid,uuid)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_update_request_before_hash_only(uuid,text,uuid,integer,text,text,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_update_report_before_hash_only(uuid,text,uuid,uuid,text,jsonb,text)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('anon','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('service_role','public.session_update_request(uuid,text,uuid,integer,text,text,text)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_update_report(uuid,text,uuid,uuid,text,jsonb,text)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_update_status(uuid,text,uuid,uuid,uuid,uuid)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_inbox(uuid,text)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_peers(uuid,text)','EXECUTE')
    or has_function_privilege('anon','public.session_requirements()','EXECUTE') then
  raise exception 'hash_only_privileges_wrong'; end if;

 -- The release is the protocol alone.
 if public.session_requirements()<>jsonb_build_object('protocol_version',2) then
  raise exception 'requirements_still_carry_the_number: %',public.session_requirements(); end if;

 -- Rows the phase-one server stored lost the number; the notice text names the protocol and hash only.
 if exists(select 1 from quorum_private.update_targets where skill_version is not null)
    or exists(select 1 from quorum_private.update_notices where runtime_evidence ? 'skill_version')
    or exists(select 1 from quorum_private.update_reports where evidence ? 'skill_version')
    or exists(select 1 from quorum_private.session_health where skill_version is not null) then
  raise exception 'stored_number_left'; end if;
 select m.body into body from quorum_private.messages m where m.id=(fixture->>'message')::uuid;
 if body<>'Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
    ||'no installation, lifecycle or task permission is granted. Target protocol '||p||', sha256 '||old_digest||'.' then
  raise exception 'old_notice_not_rewritten: %',body; end if;
 if (select runtime_evidence-'reported_at' from quorum_private.update_notices where id=old_notice)
    <>jsonb_build_object('protocol_version',p,'content_sha256',old_digest,'run_id',old_run) then
  raise exception 'old_runtime_evidence_wrong'; end if;

 -- A phase-one report retried after the migration is the same report, with or without the number.
 if public.session_update_report(peer,peer_sid,q,old_notice,'refreshed',jsonb_build_object('protocol_version',p,
     'skill_version',old_release,'content_sha256',old_digest),'before-refresh')<>'{"recorded":true}'::jsonb
    or public.session_update_report(peer,peer_sid,q,old_notice,'refreshed',jsonb_build_object('protocol_version',p,
     'content_sha256',old_digest),'before-refresh')<>'{"recorded":true}'::jsonb then
  raise exception 'old_report_replay_refused'; end if;

 -- Heartbeats: a copy that still sends a number, any number, and one that sends none are all heard.
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic-hash');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (fresh,q,dev,owner_id,'hash','fresh','grok',fresh_sid);
 result:=public.session_heartbeat(peer,peer_sid,gen_random_uuid(),p,old_release,0,null,old_run,now());
 if result ? 'code' or result->'required'<>required then raise exception 'old_heartbeat_refused: %',result; end if;
 result:=public.session_heartbeat(peer,peer_sid,gen_random_uuid(),p,'9.9.9');
 if result ? 'code' then raise exception 'any_number_refused: %',result; end if;
 result:=public.session_heartbeat(p_agent_id=>fresh,p_session_ref=>fresh_sid,p_connection_id=>gen_random_uuid(),
  p_protocol_version=>p,p_awaiting_owner=>0,p_client_run_id=>run_id,p_runtime_started_at=>now());
 if result ? 'code' or result->>'online'<>'true' or not result ? 'hello' then raise exception 'numberless_heartbeat_refused: %',result; end if;
 if (select client_run_id from quorum_private.session_status where agent_id=fresh)<>run_id then
  raise exception 'numberless_status_lost'; end if;
 result:=public.session_heartbeat(p_agent_id=>fresh,p_session_ref=>fresh_sid,p_connection_id=>gen_random_uuid(),
  p_protocol_version=>p);
 if result ? 'code' then raise exception 'reviewed_argument_set_refused: %',result; end if;
 if exists(select 1 from quorum_private.session_health where skill_version is not null) then
  raise exception 'heartbeat_stored_a_number'; end if;
 -- The protocol is still the gate, and a refusal names only it.
 result:=public.session_heartbeat(p_agent_id=>fresh,p_session_ref=>fresh_sid,p_connection_id=>gen_random_uuid(),
  p_protocol_version=>p+1);
 if result->>'code' is distinct from 'skill_update_required' or result->'required'<>required then
  raise exception 'protocol_gate_lost: %',result; end if;

 -- Peers: no number in client or required, and the protocol alone decides update_required.
 result:=public.session_peers(lead,lead_sid);
 if jsonb_array_length(result)<>3 then raise exception 'peer_rows_changed: %',result; end if;
 if exists(select 1 from jsonb_array_elements(result) e where e.value->'client' ? 'skill_version'
     or e.value->'required'<>required) then raise exception 'peer_view_carries_the_number: %',result; end if;
 select e.value into row_view from jsonb_array_elements(result) e where e.value->>'agent_id'=peer::text;
 if (row_view->>'update_required')::boolean or (row_view->'client'->>'protocol_version')::integer<>p
    or row_view->>'availability'<>'online' then raise exception 'current_peer_wrong: %',row_view; end if;
 select e.value into row_view from jsonb_array_elements(result) e where e.value->>'agent_id'=lead::text;
 if not (row_view->>'update_required')::boolean then raise exception 'unseen_peer_not_flagged: %',row_view; end if;
 if (select jsonb_agg(e.value-'client'-'required'-'update_required' order by e.ordinality) from jsonb_array_elements(result) with ordinality e)
    <>(select jsonb_agg(e.value-'client'-'required'-'update_required' order by e.ordinality)
       from jsonb_array_elements(quorum_private.session_peers_before_hash_only(lead,lead_sid)) with ordinality e) then
  raise exception 'peer_view_changed_more_than_the_number'; end if;

 -- A request without a number: the target and the notice carry the protocol and hash only.
 result:=public.session_update_request(p_agent_id=>lead,p_session_ref=>lead_sid,p_quorum_id=>q,p_protocol_version=>p,
  p_content_sha256=>digest,p_request_id=>'hash-only-new');
 op:=(result->>'operation_id')::uuid;
 if (result->>'queued')::integer<>2 then raise exception 'request_fanout_wrong: %',result; end if;
 if (select skill_version from quorum_private.update_targets t join quorum_private.update_operations o on o.target_id=t.id
     where o.id=op) is not null then raise exception 'target_stored_a_number'; end if;
 -- A retry that sends a number is the same request, and a copy that sends one makes no new target.
 if public.session_update_request(lead,lead_sid,q,p,old_release,digest,'hash-only-new')->>'operation_id'<>op::text
    or public.session_update_request(lead,lead_sid,q,p,'9.9.9',digest,'hash-only-new')->>'operation_id'<>op::text then
  raise exception 'request_retry_conflict'; end if;
 result:=public.session_update_request(lead,lead_sid,q,p,old_release,digest,'hash-only-old-copy');
 if (result->>'existing')::integer<>2 or (select count(*) from quorum_private.update_targets where quorum_id=q)<>2 then
  raise exception 'old_copy_request_wrong: %',result; end if;
 denied:=false;
 begin perform public.session_update_request(lead,lead_sid,q,p,null,'not-a-hash','hash-only-invalid');
 exception when others then denied:=sqlerrm='update_target_invalid'; end;
 if not denied then raise exception 'invalid_target_accepted'; end if;
 denied:=false;
 begin perform public.session_update_request(lead,lead_sid,q,p+1,null,digest,'hash-only-conflict');
 exception when others then denied:=sqlerrm='update_target_conflict'; end;
 if not denied then raise exception 'protocol_conflict_accepted'; end if;
 select notice_id into notice from quorum_private.update_recipients where operation_id=op and recipient_id=peer;
 select m.body into body from quorum_private.messages m join quorum_private.update_notices n on n.message_id=m.id where n.id=notice;
 if body<>'Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
    ||'no installation, lifecycle or task permission is granted. Target protocol '||p||', sha256 '||digest||'.' then
  raise exception 'new_notice_text_wrong: %',body; end if;

 -- The inbox and the status name the target by protocol and hash. The inbox shows one head at a
 -- time, so earlier heads (Hellos, the phase-one notice) are receipted until the new notice leads.
 seen:=0;
 loop
  result:=public.session_inbox(peer,peer_sid);
  if jsonb_array_length(result)<>1 then raise exception 'inbox_lost_the_notice: %',result; end if;
  if result<>(select coalesce(jsonb_agg(e.value#-'{maintenance,target,skill_version}' order by e.ordinality),'[]'::jsonb)
     from jsonb_array_elements(quorum_private.session_inbox_before_hash_only(peer,peer_sid)) with ordinality e) then
   raise exception 'inbox_changed_more_than_the_number'; end if;
  row_view:=result->0;
  if row_view ? 'maintenance' then
   if row_view->'maintenance'->'target'<>jsonb_build_object('protocol_version',p,'content_sha256',
      case when row_view->'maintenance'->>'notice_id'=notice::text then digest else old_digest end) then
    raise exception 'inbox_target_wrong: %',row_view; end if;
   exit when row_view->'maintenance'->>'notice_id'=notice::text;
  end if;
  perform public.session_ack(peer,peer_sid,(row_view->>'id')::uuid);
  seen:=seen+1;
  if seen>8 then raise exception 'inbox_never_reached_the_notice'; end if;
 end loop;
 if row_view->>'body'<>body then raise exception 'inbox_body_wrong: %',row_view; end if;
 perform public.session_ack(peer,peer_sid,(row_view->>'id')::uuid);
 result:=public.session_update_status(peer,peer_sid,q,null,notice);
 if result->'target'<>jsonb_build_object('protocol_version',p,'content_sha256',digest) or result->>'state'<>'owner_action_required'
  then raise exception 'notice_status_wrong: %',result; end if;
 result:=public.session_update_status(lead,lead_sid,q,op);
 if result->'target'<>jsonb_build_object('protocol_version',p,'content_sha256',digest)
    or jsonb_array_length(result->'participants')<>2 then raise exception 'operation_status_wrong: %',result; end if;

 -- Reports: evidence without the number, or with it from an older copy, and the protocol gate on `active`.
 if public.session_update_report(peer,peer_sid,q,notice,'refreshed',jsonb_build_object('protocol_version',p,
     'content_sha256',digest),'hash-refresh')<>'{"recorded":true}'::jsonb then raise exception 'refresh_refused'; end if;
 if public.session_update_report(peer,peer_sid,q,notice,'refreshed',jsonb_build_object('protocol_version',p,
     'skill_version',old_release,'content_sha256',digest),'hash-refresh')<>'{"recorded":true}'::jsonb then
  raise exception 'old_copy_refresh_replay_refused'; end if;
 denied:=false;
 begin perform public.session_update_report(peer,peer_sid,q,notice,'refreshed',jsonb_build_object('protocol_version',p,
  'content_sha256',old_digest),'hash-wrong-target');
 exception when others then denied:=sqlerrm='update_evidence_invalid'; end;
 if not denied then raise exception 'other_target_accepted'; end if;
 denied:=false;
 begin perform public.session_update_report(peer,peer_sid,q,notice,'refreshed','"not-an-object"'::jsonb,'hash-scalar');
 exception when others then denied:=sqlerrm='update_evidence_invalid'; end;
 if not denied then raise exception 'scalar_evidence_accepted'; end if;
 denied:=false;
 begin perform public.session_update_report(peer,peer_sid,q,notice,'active',jsonb_build_object('protocol_version',p,
  'content_sha256',digest,'run_id',run_id),'hash-refresh');
 exception when others then denied:=sqlerrm='idempotency_conflict'; end;
 if not denied then raise exception 'conflicting_replay_accepted'; end if;
 if public.session_update_report(peer,peer_sid,q,notice,'active',jsonb_build_object('protocol_version',p,
     'skill_version',old_release,'content_sha256',digest,'run_id',run_id),'hash-active')<>'{"recorded":true}'::jsonb then
  raise exception 'active_refused'; end if;
 if (select runtime_evidence-'reported_at' from quorum_private.update_notices where id=notice)
    <>jsonb_build_object('protocol_version',p,'content_sha256',digest,'run_id',run_id)
    or exists(select 1 from quorum_private.update_reports where evidence ? 'skill_version') then
  raise exception 'report_stored_a_number'; end if;
 if public.session_update_report(peer,peer_sid,q,notice,'blocked',jsonb_build_object('code','update_fetch_failed',
     'skill_version',old_release),'hash-blocked')<>'{"recorded":true}'::jsonb
    or (select blocked_code from quorum_private.update_notices where id=notice)<>'update_fetch_failed' then
  raise exception 'blocked_refused'; end if;
 select e.value into row_view from jsonb_array_elements(public.session_update_status(lead,lead_sid,q,op)->'participants') e
  where e.value->>'agent_id'=peer::text;
 if row_view->'runtime_report' ? 'skill_version' or row_view->>'state'<>'blocked' then
  raise exception 'participant_wrong: %',row_view; end if;

 -- The fleet target is the newest requested target, still by protocol and hash only.
 update quorum_private.update_operations set created_at=created_at+interval '1 second' where id=op;
 result:=public.session_release_status(lead,lead_sid);
 if result->'target'->>'content_sha256'<>digest or result->'target' ? 'skill_version' then
  raise exception 'release_target_wrong: %',result; end if;
end $test$;
