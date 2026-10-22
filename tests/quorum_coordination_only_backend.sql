-- Disposable PostgreSQL only. Runs after the coordination migration and BEFORE
-- the mandatory urgent-priority migration: the historical 0.2.0/1 server.
-- A 0.3.0/2 client must be refused there and its priority RPCs cannot write.
do $test$
declare a quorum_private.agents%rowtype; b quorum_private.agents%rowtype;
 target uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 before_messages bigint; sent jsonb; refused boolean;
begin
 select x.* into a from quorum_private.agents x join quorum_private.devices d on d.id=x.device_id
  where not x.revoked and d.owner_id=x.owner_id and not d.revoked and exists
  (select 1 from quorum_private.agents y where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 if not found then raise exception 'two_pilot_members_required'; end if;
 select * into b from quorum_private.agents where quorum_id=a.quorum_id and owner_id<>a.owner_id and not revoked limit 1;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(target,a.quorum_id,a.device_id,a.owner_id,b.project_ref,'coordination-only','codex',sid);
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 -- The historical tuple is unchanged, so the 0.3.0 client's exact gate fails.
 if public.session_requirements()<>jsonb_build_object('protocol_version',1,'skill_version','0.2.0') then
  raise exception 'historical_release_changed'; end if;
 sent:=public.session_heartbeat(target,sid,gen_random_uuid(),2,'0.3.0');
 if sent->>'code' is distinct from 'skill_update_required'
   or sent->'required'<>jsonb_build_object('protocol_version',1,'skill_version','0.2.0') then
  raise exception 'new_client_not_refused'; end if;
 if exists(select 1 from quorum_private.session_health where agent_id=target) then
  raise exception 'refused_client_counted_online'; end if;
 -- Only the old normal signatures exist; the priority RPCs are absent.
 if to_regprocedure('public.message_send(uuid,uuid,uuid,uuid,uuid,text,text)') is null
   or to_regprocedure('public.session_reply(uuid,text,uuid,text)') is null then
  raise exception 'historical_rpcs_missing'; end if;
 if to_regprocedure('public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text)') is not null
   or to_regprocedure('public.session_reply(uuid,text,uuid,text,text)') is not null
   or exists(select 1 from information_schema.columns where table_schema='quorum_private'
             and table_name='messages' and column_name='priority') then
  raise exception 'priority_present_without_migration'; end if;
 -- Even a client that skipped its gate cannot write through this server.
 select count(*) into before_messages from quorum_private.messages;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 refused:=false;
 begin
  perform public.message_send(p_quorum_id=>a.quorum_id,p_sender_agent_id=>b.id,
   p_conversation_id=>gen_random_uuid(),p_recipient_agent_id=>target,p_reply_to=>null,
   p_body=>'normal from 0.3.0',p_request_id=>'coordination-only:normal',p_priority=>'normal');
 exception when undefined_function then refused:=true; end;
 if not refused then raise exception 'normal_priority_send_accepted'; end if;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 refused:=false;
 begin
  perform public.session_reply(p_agent_id=>target,p_session_ref=>sid,p_message_id=>gen_random_uuid(),
   p_body=>'reply from 0.3.0',p_priority=>'normal');
 exception when undefined_function then refused:=true; end;
 if not refused then raise exception 'normal_priority_reply_accepted'; end if;
 if (select count(*) from quorum_private.messages)<>before_messages then
  raise exception 'refused_pairing_wrote_messages'; end if;
end $test$;
