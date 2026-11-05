-- Disposable PostgreSQL only: heartbeat peer status and its session_peers projection (#113, #104).
-- Execute inside BEGIN/ROLLBACK after 20261105160000_peer_status_visibility.sql. now() is fixed for
-- the transaction, so elapsed time is simulated by moving stored timestamps back.
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 viewer uuid:=gen_random_uuid(); vsid text:=gen_random_uuid()::text;
 worker uuid:=gen_random_uuid(); wsid text:=gen_random_uuid()::text;
 legacy uuid:=gen_random_uuid(); lsid text:=gen_random_uuid()::text;
 epoch uuid:=gen_random_uuid(); run_a uuid:=gen_random_uuid(); run_b uuid:=gen_random_uuid();
 result jsonb; again jsonb; before jsonb; after jsonb; row jsonb; notice uuid; seen timestamptz;
 status quorum_private.session_status%rowtype; overdue uuid;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
 sha text:=repeat('a',64);
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000113');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'status-suite',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (viewer,q,dev,owner_id,'shared','viewer','claude',vsid),
  (worker,q,dev,owner_id,'shared','worker','codex',wsid),
  (legacy,q,dev,owner_id,'shared','legacy','claude',lsid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- Privileges: one public heartbeat signature, private moved bodies and table, authenticated only.
 if to_regprocedure('public.session_heartbeat(uuid,text,uuid,integer,text)') is not null
    or to_regprocedure('quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)') is null
    or to_regprocedure('quorum_private.session_peers_before_status(uuid,text)') is null then
  raise exception 'status_signatures_wrong'; end if;
 if has_function_privilege('anon','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('service_role','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('anon','public.session_peers(uuid,text)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_peers(uuid,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_peers_before_status(uuid,text)','EXECUTE')
    or has_table_privilege('authenticated','quorum_private.session_status','SELECT')
    or has_table_privilege('service_role','quorum_private.session_status','SELECT') then
  raise exception 'status_privileges_wrong'; end if;

 -- An old client's call: the reviewed response (with its Hello object), and NULL status.
 result:=public.session_heartbeat(legacy,lsid,gen_random_uuid(),p,v);
 if result->>'online'<>'true' or result->>'new_connection'<>'true' or not result ? 'hello'
    or (select count(*) from jsonb_object_keys(result))<>5 then raise exception 'old_call_changed: %',result; end if;
 select * into status from quorum_private.session_status where agent_id=legacy;
 if not found or status.awaiting_owner is not null or status.client_run_id is not null
    or status.reception_hold_until is not null or status.runtime_started_at is not null then
  raise exception 'old_call_status_wrong'; end if;

 -- Rows without status data keep the reviewed projection exactly, apart from the three new keys.
 perform public.session_heartbeat(viewer,vsid,gen_random_uuid(),p,v);
 before:=quorum_private.session_peers_before_status(viewer,vsid);
 after:=public.session_peers(viewer,vsid);
 if jsonb_array_length(after)<>3 or (select jsonb_agg(e.value-'awaiting_owner'-'reception_hold_until'-'client_run_reported'
     order by e.value->>'agent_id') from jsonb_array_elements(after) e)
    <>(select jsonb_agg(e.value order by e.value->>'agent_id') from jsonb_array_elements(before) e) then
  raise exception 'reviewed_projection_changed: % vs %',after,before; end if;

 -- Validation happens before the core: nothing about health or connections changes.
 update quorum_private.session_health set last_seen=now()-interval '5 minutes' where agent_id=worker;
 begin perform public.session_heartbeat(worker,wsid,epoch,p,v,33); raise exception 'count_33_accepted';
 exception when raise_exception then if sqlerrm<>'invalid_peer_status' then raise; end if; end;
 begin perform public.session_heartbeat(worker,wsid,epoch,p,v,-1); raise exception 'count_negative_accepted';
 exception when raise_exception then if sqlerrm<>'invalid_peer_status' then raise; end if; end;
 if exists(select 1 from quorum_private.session_connections where agent_id=worker and connection_id=epoch)
    or exists(select 1 from quorum_private.session_status where agent_id=worker)
    or exists(select 1 from quorum_private.session_health where agent_id=worker and last_seen=now()) then
  raise exception 'invalid_status_wrote'; end if;
 -- A release mismatch stores no status either.
 result:=public.session_heartbeat(worker,wsid,epoch,1,'0.0.1',2,null,run_a,now());
 if result->>'code' is distinct from 'skill_update_required'
    or exists(select 1 from quorum_private.session_status where agent_id=worker) then
  raise exception 'mismatch_stored_status: %',result; end if;

 -- A current client: awaiting the owner while alive, with bounded timestamps.
 result:=public.session_heartbeat(worker,wsid,epoch,p,v,2,now()+interval '2 hours',run_a,now()+interval '1 day');
 if result->>'online'<>'true' or not result ? 'hello' then raise exception 'new_call_failed: %',result; end if;
 select * into status from quorum_private.session_status where agent_id=worker;
 if status.awaiting_owner<>2 or status.client_run_id<>run_a or status.reported_at<>now()
    or status.reception_hold_until<>now()+interval '30 minutes' or status.runtime_started_at<>now() then
  raise exception 'status_not_bounded'; end if;
 -- The same connection retried keeps the Hello result and overwrites the status.
 again:=public.session_heartbeat(worker,wsid,epoch,p,v,2,now()-interval '1 second',run_a,now()-interval '1 hour');
 if again->'hello'<>result->'hello' or again->>'new_connection'<>'false' then raise exception 'retry_changed: %',again; end if;
 select * into status from quorum_private.session_status where agent_id=worker;
 if status.reception_hold_until is not null or status.runtime_started_at<>now()-interval '1 hour' then
  raise exception 'past_hold_kept'; end if;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'awaiting_owner' or row->'reason'<>'null'::jsonb or row->>'awaiting_owner'<>'2'
    or row->'reception_hold_until'<>'null'::jsonb or row->>'client_run_reported'<>'true' then
  raise exception 'awaiting_not_projected: %',row; end if;
 -- Peers never see a run ID or the stored start time.
 if public.session_peers(viewer,vsid)::text like '%'||run_a::text||'%' or row ? 'client_run_id'
    or row ? 'runtime_started_at' then raise exception 'run_id_exposed'; end if;
 -- A zero count is plain online.
 perform public.session_heartbeat(worker,wsid,epoch,p,v,0,null,run_a,null);
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'online' or row->>'awaiting_owner'<>'0' then raise exception 'zero_not_online: %',row; end if;
 -- The latest heartbeat wins: an old-argument call clears the count.
 perform public.session_heartbeat(worker,wsid,epoch,p,v,5,null,run_a,null);
 perform public.session_heartbeat(worker,wsid,epoch,p,v);
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'online' or row->'awaiting_owner'<>'null'::jsonb or row->>'client_run_reported'<>'false' then
  raise exception 'old_call_kept_status: %',row; end if;
 -- A status older than 60 seconds is not shown, even while health is fresh from a receipt.
 perform public.session_heartbeat(worker,wsid,epoch,p,v,4,now()+interval '10 minutes',run_a,null);
 update quorum_private.session_status set reported_at=now()-interval '61 seconds' where agent_id=worker;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'online' or row->'awaiting_owner'<>'null'::jsonb or row->'reception_hold_until'<>'null'::jsonb then
  raise exception 'stale_status_shown: %',row; end if;

 -- An overdue queued message: receipt_timeout, or reception_hold while an explicit hold runs.
 perform public.session_heartbeat(worker,wsid,epoch,p,v,1,now()+interval '10 minutes',run_a,null);
 overdue:=(quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',viewer,
  'recipient_agent_id',worker,'conversation_id',gen_random_uuid(),'reply_to',null,'body','synthetic'),
  'status-overdue')->>'message_id')::uuid;
 update quorum_private.deliveries set created_at=now()-interval '5 minutes' where message_id=overdue;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'unavailable' or row->>'reason'<>'reception_hold' or row->>'awaiting_owner'<>'1'
    or (row->>'reception_hold_until')::timestamptz<>now()+interval '10 minutes' then
  raise exception 'hold_not_reported: %',row; end if;
 perform public.session_heartbeat(worker,wsid,epoch,p,v,1,null,run_a,null);
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'unavailable' or row->>'reason'<>'receipt_timeout' then
  raise exception 'overdue_without_hold_wrong: %',row; end if;
 perform public.session_ack(worker,wsid,overdue);

 -- Restart evidence: the owner-update runtime report of another run, after the last heartbeat.
 perform public.session_update_request(viewer,vsid,q,p,v,sha,'status-update');
 select id into notice from quorum_private.update_notices where recipient_id=worker;
 perform public.session_update_report(worker,wsid,q,notice,'active',jsonb_build_object('protocol_version',p,
  'skill_version',v,'content_sha256',sha,'run_id',run_b),'status-active');
 -- Fresh health: never restarted, whatever the report says.
 perform public.session_heartbeat(worker,wsid,epoch,p,v,0,null,run_a,null);
 update quorum_private.update_notices set runtime_evidence=jsonb_set(runtime_evidence,'{reported_at}',
  to_jsonb(now()+interval '1 second')) where id=notice;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'online' then raise exception 'fresh_marked_restarted: %',row; end if;
 -- Stale health, a different run reported after the last heartbeat: restarted, not recovered.
 seen:=now()-interval '2 minutes';
 update quorum_private.session_health set last_seen=seen where agent_id=worker;
 update quorum_private.session_status set reported_at=seen where agent_id=worker;
 update quorum_private.update_notices set runtime_evidence=jsonb_set(runtime_evidence,'{reported_at}',
  to_jsonb(seen+interval '30 seconds')) where id=notice;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'client_restarted_unrecovered' or row->>'reason'<>'run_changed_without_heartbeat'
    or row->'awaiting_owner'<>'null'::jsonb then raise exception 'restart_not_reported: %',row; end if;
 if public.session_peers(viewer,vsid)::text like '%'||run_b::text||'%' then raise exception 'report_run_exposed'; end if;
 -- The same run reported: plain heartbeat timeout.
 update quorum_private.update_notices set runtime_evidence=jsonb_set(runtime_evidence,'{run_id}',to_jsonb(run_a::text))
  where id=notice;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'unavailable' or row->>'reason'<>'heartbeat_timeout' then
  raise exception 'same_run_marked_restarted: %',row; end if;
 -- A different run reported before the last heartbeat: that heartbeat superseded it.
 update quorum_private.update_notices set runtime_evidence=jsonb_set(jsonb_set(runtime_evidence,'{run_id}',
  to_jsonb(run_b::text)),'{reported_at}',to_jsonb(seen-interval '1 second')) where id=notice;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'unavailable' then raise exception 'older_report_marked_restarted: %',row; end if;
 -- A heartbeat without a run generation (an old client) never yields the restart state.
 update quorum_private.update_notices set runtime_evidence=jsonb_set(runtime_evidence,'{reported_at}',
  to_jsonb(seen+interval '30 seconds')) where id=notice;
 update quorum_private.session_status set client_run_id=null where agent_id=worker;
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'unavailable' or row->>'client_run_reported'<>'false' then
  raise exception 'unreported_run_marked_restarted: %',row; end if;
 -- Recovery: the new run's heartbeat clears it at once.
 update quorum_private.session_status set client_run_id=run_a where agent_id=worker;
 perform public.session_heartbeat(worker,wsid,gen_random_uuid(),p,v,0,null,run_b,now());
 select e.value into row from jsonb_array_elements(public.session_peers(viewer,vsid)) e where e.value->>'agent_id'=worker::text;
 if row->>'availability'<>'online' then raise exception 'recovered_not_online: %',row; end if;

 -- A revoked binding cannot write status.
 update quorum_private.agents set revoked=true where id=legacy;
 begin perform public.session_heartbeat(legacy,lsid,gen_random_uuid(),p,v,3,null,run_a,null);
  raise exception 'revoked_heartbeat_accepted';
 exception when insufficient_privilege then null; end;
 if (select awaiting_owner from quorum_private.session_status where agent_id=legacy) is not null then
  raise exception 'revoked_status_written'; end if;
 if public.session_requirements()<>required then raise exception 'status_changed_release'; end if;
end $test$;
