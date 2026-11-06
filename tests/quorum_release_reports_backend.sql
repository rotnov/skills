-- Disposable PostgreSQL only: running content hashes against the fleet target, beside unchanged clients (#168).
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); other_q uuid:=gen_random_uuid();
 dev uuid:=gen_random_uuid();
 lead uuid:=gen_random_uuid(); lead_sid text:=gen_random_uuid()::text;
 current_peer uuid:=gen_random_uuid(); current_sid text:=gen_random_uuid()::text;
 old_peer uuid:=gen_random_uuid(); old_sid text:=gen_random_uuid()::text;
 elsewhere uuid:=gen_random_uuid(); elsewhere_sid text:=gen_random_uuid()::text;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 old_release text:=required->>'skill_version';
 proto integer:=(required->>'protocol_version')::integer;
 running_a text:=repeat('a',64); target_b text:=repeat('b',64); target_c text:=repeat('c',64);
 run_one uuid:=gen_random_uuid(); run_two uuid:=gen_random_uuid(); run_old uuid:=gen_random_uuid();
 request jsonb; op uuid; notice uuid; result jsonb; row_view jsonb; denied boolean; before_hash text; after_hash text;
begin
 insert into auth.users(id) values(owner_id),(stranger);
 insert into auth.identities(user_id,provider,provider_id) values
  (owner_id,'github','9000000000168'),(stranger,'github','9000000000169');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'release-reports',owner_id),(other_q,'release-other',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner'),(other_q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (lead,q,dev,owner_id,'fleet','lead','claude',lead_sid),
  (current_peer,q,dev,owner_id,'fleet','current','codex',current_sid),
  (old_peer,q,dev,owner_id,'fleet','old','grok',old_sid),
  (elsewhere,q,dev,owner_id,'other-project','elsewhere','codex',elsewhere_sid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- Nothing reported and no target: rows exist and say so.
 result:=public.session_release_status(lead,lead_sid);
 if result->'target'<>'null'::jsonb or jsonb_array_length(result->'agents')<>3 then raise exception 'empty_view_wrong: %',result; end if;
 if exists(select 1 from jsonb_array_elements(result->'agents') a where a.value->>'content_sha256' is not null) then
  raise exception 'unreported_agent_has_a_hash'; end if;
 if exists(select 1 from jsonb_array_elements(result->'agents') a where a.value->>'agent_id'=elsewhere::text) then
  raise exception 'peer_scope_widened'; end if;

 -- A heartbeat that names a run, then a report by that run. The old version argument is untouched.
 if public.session_heartbeat(current_peer,current_sid,gen_random_uuid(),proto,old_release,0,null,run_one,now()) ? 'code' then
  raise exception 'current_heartbeat_refused'; end if;
 if public.session_release_report(current_peer,current_sid,running_a,run_one)<>'{"recorded":true}'::jsonb then
  raise exception 'report_not_recorded'; end if;
 select a.value into row_view from jsonb_array_elements(public.session_release_status(lead,lead_sid)->'agents') a
  where a.value->>'agent_id'=current_peer::text;
 if row_view->>'content_sha256'<>running_a or row_view->>'reported_via'<>'heartbeat' or row_view->>'reported_at' is null then
  raise exception 'report_view_wrong: %',row_view; end if;

 -- A later report replaces the row: there is one per agent.
 perform public.session_release_report(current_peer,current_sid,target_b,run_one);
 if (select count(*) from quorum_private.session_release where agent_id=current_peer)<>1
    or (select content_sha256 from quorum_private.session_release where agent_id=current_peer)<>target_b then
  raise exception 'report_not_replaced'; end if;

 -- A heartbeat from another run, which reports nothing: the older run's hash is no longer believed.
 perform public.session_heartbeat(current_peer,current_sid,gen_random_uuid(),proto,old_release,0,null,run_two,now());
 select a.value into row_view from jsonb_array_elements(public.session_release_status(lead,lead_sid)->'agents') a
  where a.value->>'agent_id'=current_peer::text;
 if row_view->>'content_sha256' is not null or row_view->>'reported_via' is not null then
  raise exception 'replaced_run_kept_its_hash: %',row_view; end if;
 perform public.session_release_report(current_peer,current_sid,target_b,run_two);

 -- Invalid hashes and other people's sessions are refused and write nothing.
 denied:=false;
 begin perform public.session_release_report(current_peer,current_sid,'not-a-hash',run_two);
 exception when others then denied:=sqlerrm='release_report_invalid'; end;
 if not denied then raise exception 'invalid_hash_recorded'; end if;
 denied:=false;
 begin perform public.session_release_report(current_peer,current_sid,null,run_two);
 exception when others then denied:=sqlerrm='release_report_invalid'; end;
 if not denied then raise exception 'null_hash_recorded'; end if;
 denied:=false;
 begin perform public.session_release_report(current_peer,gen_random_uuid()::text,running_a,run_two);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_session_reported'; end if;
 perform set_config('request.jwt.claim.sub',stranger::text,true);
 denied:=false;
 begin perform public.session_release_report(current_peer,current_sid,running_a,run_two);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'stranger_reported'; end if;
 denied:=false;
 begin perform public.session_release_status(lead,lead_sid);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'stranger_read_reports'; end if;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 if (select content_sha256 from quorum_private.session_release where agent_id=current_peer)<>target_b then
  raise exception 'refused_report_wrote'; end if;

 -- The newest requested target is the fleet target, and an older requested one stops being it.
 request:=public.session_update_request(lead,lead_sid,q,proto,old_release,target_c,'release-first');
 request:=public.session_update_request(lead,lead_sid,q,proto,old_release,target_b,'release-second');
 op:=(request->>'operation_id')::uuid;
 update quorum_private.update_operations set created_at=created_at+interval '1 second' where id=op;
 result:=public.session_release_status(lead,lead_sid);
 if result->'target'->>'content_sha256'<>target_b or (result->'target'->>'protocol_version')::integer<>proto then
  raise exception 'target_wrong: %',result; end if;
 if result->'target' ? 'skill_version' then raise exception 'target_carries_the_number'; end if;

 -- An older listener sends no report. Its runtime report for an owner update counts while its run does.
 perform public.session_heartbeat(old_peer,old_sid,gen_random_uuid(),proto,old_release,0,null,run_old,now());
 select r.notice_id into notice from quorum_private.update_recipients r where r.operation_id=op and r.recipient_id=old_peer;
 perform public.session_update_report(old_peer,old_sid,q,notice,'active',jsonb_build_object('protocol_version',proto,
  'skill_version',old_release,'content_sha256',target_b,'run_id',run_old),'release-old-active');
 select a.value into row_view from jsonb_array_elements(public.session_release_status(lead,lead_sid)->'agents') a
  where a.value->>'agent_id'=old_peer::text;
 if row_view->>'content_sha256'<>target_b or row_view->>'reported_via'<>'update_report' then
  raise exception 'runtime_report_not_read: %',row_view; end if;
 perform public.session_heartbeat(old_peer,old_sid,gen_random_uuid(),proto,old_release,0,null,gen_random_uuid(),now());
 select a.value into row_view from jsonb_array_elements(public.session_release_status(lead,lead_sid)->'agents') a
  where a.value->>'agent_id'=old_peer::text;
 if row_view->>'content_sha256' is not null then raise exception 'stale_runtime_report_believed: %',row_view; end if;

 -- With an operation: its recipients for its initiator, across projects, one cursor page at a time.
 result:=public.session_release_status(lead,lead_sid,op);
 if jsonb_array_length(result->'agents')<>3 or result->'target'->>'content_sha256'<>target_b then
  raise exception 'operation_view_wrong: %',result; end if;
 if exists(select 1 from jsonb_array_elements(result->'agents') a where a.value->>'agent_id'=lead::text) then
  raise exception 'initiator_in_recipients'; end if;
 result:=public.session_release_status(lead,lead_sid,op,(select max(id::text)::uuid from
  (select unnest(array[current_peer,old_peer,elsewhere]) as id) ids));
 if jsonb_array_length(result->'agents')<>0 then raise exception 'cursor_ignored: %',result; end if;
 denied:=false;
 begin perform public.session_release_status(current_peer,current_sid,op);
 exception when insufficient_privilege then denied:=sqlerrm='update_status_denied'; end;
 if not denied then raise exception 'recipient_read_the_operation'; end if;

 -- A report never reaches session_peers or the required release: the number stays as old clients read it.
 if public.session_requirements()<>required then raise exception 'requirements_changed'; end if;
 select a.value into row_view from jsonb_array_elements(public.session_peers(lead,lead_sid)) a
  where a.value->>'agent_id'=current_peer::text;
 if row_view->'client'->>'skill_version'<>old_release or (row_view->>'update_required')::boolean then
  raise exception 'peer_view_changed: %',row_view; end if;
 if public.session_heartbeat(current_peer,current_sid,gen_random_uuid(),proto,'9.9.9')->>'code'<>'skill_update_required' then
  raise exception 'old_gate_changed'; end if;

 if has_table_privilege('authenticated','quorum_private.session_release','select') then
  raise exception 'table_exposed'; end if;
end $test$;
