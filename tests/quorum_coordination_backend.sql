-- Disposable PostgreSQL only: session health, scoping and reconnect idempotency.
do $test$
declare a quorum_private.agents%rowtype; b quorum_private.agents%rowtype;
 sid text:=gen_random_uuid()::text; target uuid:=gen_random_uuid(); epoch uuid:=gen_random_uuid();
 result jsonb; sent jsonb; amount bigint; seen timestamptz; item jsonb; found_notice boolean:=false;
begin
 select x.* into a from quorum_private.agents x where not x.revoked and exists
  (select 1 from quorum_private.agents y where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 select * into b from quorum_private.agents where quorum_id=a.quorum_id and owner_id<>a.owner_id and not revoked limit 1;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(target,a.quorum_id,a.device_id,a.owner_id,b.project_ref,'health-test','claude',sid);
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 begin perform public.session_heartbeat(target,sid,epoch,2,'0.3.0'); raise exception 'foreign_heartbeat';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 if public.session_requirements()<>jsonb_build_object('protocol_version',2,'skill_version','0.3.0') then raise exception 'wrong_release_requirements'; end if;
 for result in select value from jsonb_array_elements('[{}, {"protocol_version":1,"skill_version":"0.2.0"}, {"protocol_version":2,"skill_version":"0.3.1"}, {"protocol_version":1,"skill_version":"0.3.0"}]'::jsonb) loop
  sent:=public.session_heartbeat(target,sid,epoch,(result->>'protocol_version')::integer,result->>'skill_version');
  if sent->>'code' is distinct from 'skill_update_required' or sent->'required' is distinct from jsonb_build_object('protocol_version',2,'skill_version','0.3.0') then raise exception 'missing_release_rejection'; end if;
  if exists(select 1 from quorum_private.session_health where agent_id=target) or exists(select 1 from quorum_private.session_connections where agent_id=target) or exists(select 1 from quorum_private.session_notices where agent_id=target) then raise exception 'mismatch_wrote_health_or_hello'; end if;
 end loop;
 perform public.session_heartbeat(target,sid,epoch,2,'0.3.0');
 select count(*) into amount from quorum_private.session_notices where agent_id=target;
 perform public.session_heartbeat(target,sid,epoch,2,'0.3.0');
 if (select count(*) from quorum_private.session_notices where agent_id=target)<>amount then raise exception 'duplicate_hello'; end if;
 update quorum_private.session_health set last_seen=now()-interval '5 seconds' where agent_id=target returning last_seen into seen;
 result:=public.session_heartbeat(target,sid,gen_random_uuid(),1,'0.2.0');
 if result->>'code' is distinct from 'skill_update_required' or
   (select last_seen from quorum_private.session_health where agent_id=target) is distinct from seen or
   (select count(*) from quorum_private.session_notices where agent_id=target)<>amount then raise exception 'later_mismatch_refreshed_health_or_hello'; end if;
 -- Read receiver health through another real native session owned by b.
 update quorum_private.agents set engine='codex' where id=b.id;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 result:=public.session_peers(b.id,b.session_ref);
 if not exists(select 1 from jsonb_array_elements(result) x where x->>'agent_id'=target::text and x->>'availability'='online') then
  raise exception 'healthy_receiver_missing'; end if;
 if not exists(select 1 from jsonb_array_elements(result) x where x->>'agent_id'=target::text and x->'client'=jsonb_build_object('protocol_version',2,'skill_version','0.3.0') and x->'required'=public.session_requirements() and x->>'update_required'='false') then raise exception 'versioned_peer_missing'; end if;
 if not exists(select 1 from jsonb_array_elements(result) x where x->>'agent_id'=b.id::text and x->>'update_required'='true') then raise exception 'legacy_peer_not_flagged'; end if;
 sent:=public.message_send(a.quorum_id,b.id,gen_random_uuid(),target,null,'health probe','health-probe');
 update quorum_private.deliveries set created_at=now()-interval '59 seconds' where message_id=(sent->>'message_id')::uuid;
 if exists(select 1 from jsonb_array_elements(public.session_peers(b.id,b.session_ref)) x where x->>'agent_id'=target::text and x->>'availability'='unavailable') then raise exception 'early_timeout'; end if;
 update quorum_private.deliveries set created_at=now()-interval '60 seconds' where message_id=(sent->>'message_id')::uuid;
 if not exists(select 1 from jsonb_array_elements(public.session_peers(b.id,b.session_ref)) x where x->>'agent_id'=target::text and x->>'availability'='unavailable') then raise exception 'missing_timeout'; end if;
 if not exists(select 1 from quorum_private.agents where id=target and not revoked) then raise exception 'timeout_revoked_agent'; end if;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 update quorum_private.session_health set last_seen=now()-interval '67 seconds' where agent_id=target;
 perform public.session_ack(target,sid,(sent->>'message_id')::uuid);
 if not exists(select 1 from jsonb_array_elements(public.session_peers(target,sid)) x where x->>'agent_id'=target::text and x->>'availability'='online' and x->>'update_required'='false') then raise exception 'fresh_receipt_ignored_as_health'; end if;
 -- Reconnect creates a notification, never a task or an automatic reply chain.
 perform public.session_heartbeat(target,sid,gen_random_uuid(),2,'0.3.0');
 if (select count(*) from quorum_private.session_notices where agent_id=target)<>amount*2 then raise exception 'reconnect_hello_missing'; end if;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 if not exists(select 1 from jsonb_array_elements(public.session_peers(b.id,b.session_ref)) x where x->>'agent_id'=target::text and x->>'availability'='online') then raise exception 'receipt_did_not_recover'; end if;
 for item in select value from jsonb_array_elements(public.session_inbox(b.id,b.session_ref)) loop
  if (item->>'notification')::boolean then found_notice:=true; end if;
 end loop;
 -- The first page may contain older fixture messages; verify the wire metadata
 -- when our Hello reaches the head instead of assuming an empty inbox.
 while not found_notice loop
  result:=public.session_inbox(b.id,b.session_ref);
  if jsonb_array_length(result)=0 then raise exception 'hello_not_notification'; end if;
  item:=result->0;
  found_notice:=coalesce((item->>'notification')::boolean,false);
  perform public.session_ack(b.id,b.session_ref,(item->>'id')::uuid);
 end loop;
 if has_table_privilege('authenticated','quorum_private.session_health','SELECT') or
    has_function_privilege('anon','public.session_heartbeat(uuid,text,uuid,integer,text)','EXECUTE') then raise exception 'health_exposed'; end if;
 if has_function_privilege('anon','public.session_requirements()','EXECUTE') then raise exception 'requirements_exposed'; end if;
 perform set_config('request.jwt.claim.sub','',true);
 begin perform public.session_requirements(); raise exception 'anonymous_requirements'; exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',gen_random_uuid()::text,true);
 begin perform public.session_requirements(); raise exception 'non_github_requirements'; exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 update quorum_private.memberships set revoked=true where user_id=a.owner_id and quorum_id=a.quorum_id;
 if exists(select 1 from jsonb_array_elements(public.session_peers(b.id,b.session_ref)) x where x->>'agent_id'=target::text) then raise exception 'revoked_peer_visible'; end if;
end $test$;
