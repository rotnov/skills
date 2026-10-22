-- Run inside BEGIN/ROLLBACK together with the reviewed priority extension.
-- Uses two existing consenting pilot members; every fixture remains uncommitted.
do $test$
declare
 a quorum_private.agents%rowtype; b quorum_private.agents%rowtype;
 target uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 c uuid:=gen_random_uuid(); older jsonb; newer jsonb; urgent jsonb; replayed jsonb;
 head jsonb; reply jsonb; direct jsonb; refused boolean; replay_priority text; replay_body text;
 greeter uuid:=gen_random_uuid(); listener uuid:=gen_random_uuid();
 gsid text:=gen_random_uuid()::text; lsid text:=gen_random_uuid()::text;
begin
 select x.* into a from quorum_private.agents x where not x.revoked and exists
  (select 1 from quorum_private.agents y where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 if not found then raise exception 'two_pilot_members_required'; end if;
 select * into b from quorum_private.agents where quorum_id=a.quorum_id and owner_id<>a.owner_id and not revoked limit 1;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(target,a.quorum_id,a.device_id,a.owner_id,b.project_ref,'priority-test','codex',sid);
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);

 -- The mandatory migration advanced the exact release gate with its RPCs and
 -- left no old normal signature beside the priority entry points.
 if public.session_requirements()<>jsonb_build_object('protocol_version',2,'skill_version','0.3.0') then
  raise exception 'release_not_advanced'; end if;
 if to_regprocedure('public.message_send(uuid,uuid,uuid,uuid,uuid,text,text)') is not null
   or to_regprocedure('public.session_reply(uuid,text,uuid,text)') is not null then
  raise exception 'duplicate_normal_signature'; end if;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 refused:=public.session_heartbeat(target,sid,gen_random_uuid(),1,'0.2.0')->>'code'='skill_update_required';
 if not refused or exists(select 1 from quorum_private.session_health where agent_id=target) then
  raise exception 'old_client_not_refused'; end if;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);

 -- A message without an explicit priority stays exactly as it is today.
 older:=public.message_send(a.quorum_id,b.id,c,target,null,'first normal','prio:'||c||':1');
 newer:=public.message_send(a.quorum_id,b.id,c,target,null,'second normal','prio:'||c||':2');
 if (select count(*) from quorum_private.messages
     where id in ((older->>'message_id')::uuid,(newer->>'message_id')::uuid) and priority='normal')<>2 then
  raise exception 'default_priority_changed'; end if;

 refused:=false;
 begin
  perform public.message_send(a.quorum_id,b.id,c,target,null,'bad','prio:'||c||':bad',p_priority=>'critical');
 exception when raise_exception then
  if sqlerrm<>'invalid_priority' then raise; end if;
  refused:=true;
 end;
 if not refused then raise exception 'invalid_priority_accepted'; end if;

 urgent:=public.message_send(a.quorum_id,b.id,c,target,null,'urgent now','prio:'||c||':3',p_priority=>'urgent');
 if (select priority from quorum_private.messages where id=(urgent->>'message_id')::uuid)<>'urgent' then
  raise exception 'urgent_not_persisted'; end if;

 -- An agreeing replay returns the same message; a disagreeing one cannot
 -- rewrite the stored priority of a message that may already be delivered.
 replayed:=public.message_send(a.quorum_id,b.id,c,target,null,'urgent now','prio:'||c||':3',p_priority=>'urgent');
 if replayed->>'message_id'<>urgent->>'message_id' then raise exception 'replay_created_message'; end if;
 refused:=false;
 begin
  perform public.message_send(a.quorum_id,b.id,c,target,null,'urgent now','prio:'||c||':3');
 exception when raise_exception then
  if sqlerrm<>'idempotency_conflict' then raise; end if;
  refused:=true;
 end;
 if not refused then raise exception 'priority_replay_rewritten'; end if;
 if (select priority from quorum_private.messages where id=(urgent->>'message_id')::uuid)<>'urgent' then
  raise exception 'stored_priority_overwritten'; end if;

 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 -- The unreceipted normal head must not hide the later urgent message.
 head:=public.session_inbox(target,sid)->0;
 if head->>'id'<>urgent->>'message_id' then raise exception 'urgent_hidden_behind_normal_head'; end if;
 if head->>'priority'<>'urgent' then raise exception 'priority_not_reported'; end if;

 -- Urgency does not skip the receipt, and normal order is untouched by it.
 if jsonb_array_length(public.session_inbox(target,sid))<>1 then raise exception 'selection_not_bounded'; end if;
 perform public.session_ack(target,sid,(urgent->>'message_id')::uuid);
 head:=public.session_inbox(target,sid)->0;
 if head->>'id'<>older->>'message_id' then raise exception 'normal_order_changed'; end if;
 if head->>'priority'<>'normal' then raise exception 'normal_priority_changed'; end if;
 perform public.session_ack(target,sid,(older->>'message_id')::uuid);
 head:=public.session_inbox(target,sid)->0;
 if head->>'id'<>newer->>'message_id' then raise exception 'normal_sequence_changed'; end if;

 -- Replies may be urgent and still must not launch a reply loop.
 reply:=public.session_reply(target,sid,(newer->>'message_id')::uuid,'urgent answer',p_priority=>'urgent');
 if (select priority from quorum_private.messages where id=(reply->>'reply_message_id')::uuid)<>'urgent' then
  raise exception 'reply_priority_lost'; end if;
 if exists(select 1 from quorum_private.deliveries
           where message_id=(reply->>'reply_message_id')::uuid and requires_handler) then
  raise exception 'reply_loop_enabled'; end if;
 if not exists(select 1 from quorum_private.deliveries
               where message_id=(newer->>'message_id')::uuid and state='answered') then
  raise exception 'not_answered'; end if;

 -- A reply replay must agree on body and priority; NULL and the default are
 -- both normal. A disagreeing replay cannot report the stored answer as sent.
 if public.session_reply(target,sid,(newer->>'message_id')::uuid,'urgent answer',p_priority=>'urgent')
   ->>'reply_message_id'<>reply->>'reply_message_id' then raise exception 'urgent_reply_replay_changed'; end if;
 foreach replay_priority in array array['normal',null] loop
  refused:=false;
  begin
   perform public.session_reply(target,sid,(newer->>'message_id')::uuid,'urgent answer',p_priority=>replay_priority);
  exception when raise_exception then
   if sqlerrm<>'reply_already_sent' then raise; end if;
   refused:=true;
  end;
  if not refused then raise exception 'urgent_reply_replayed_as_normal: %', replay_priority; end if;
 end loop;
 refused:=false;
 begin
  perform public.session_reply(target,sid,(newer->>'message_id')::uuid,'urgent answer');
 exception when raise_exception then
  if sqlerrm<>'reply_already_sent' then raise; end if;
  refused:=true;
 end;
 if not refused then raise exception 'urgent_reply_replayed_as_default'; end if;
 if (select count(*) from quorum_private.messages where reply_to=(newer->>'message_id')::uuid)<>1
   or (select priority||':'||body from quorum_private.messages where id=(reply->>'reply_message_id')::uuid)
     <>'urgent:urgent answer' then raise exception 'urgent_reply_mutated'; end if;

 reply:=public.session_reply(target,sid,(older->>'message_id')::uuid,'normal answer');
 if public.session_reply(target,sid,(older->>'message_id')::uuid,'normal answer',p_priority=>null)
     ->>'reply_message_id'<>reply->>'reply_message_id'
   or public.session_reply(target,sid,(older->>'message_id')::uuid,'normal answer',p_priority=>'normal')
     ->>'reply_message_id'<>reply->>'reply_message_id' then raise exception 'normal_reply_replay_changed'; end if;
 foreach replay_body in array array['normal answer','other answer'] loop
  refused:=false;
  begin
   perform public.session_reply(target,sid,(older->>'message_id')::uuid,replay_body,
    p_priority=>case when replay_body='normal answer' then 'urgent' else 'normal' end);
  exception when raise_exception then
   if sqlerrm<>'reply_already_sent' then raise; end if;
   refused:=true;
  end;
  if not refused then raise exception 'normal_reply_replay_accepted: %', replay_body; end if;
 end loop;
 if (select count(*) from quorum_private.messages where reply_to=(older->>'message_id')::uuid)<>1
   or (select priority||':'||body from quorum_private.messages where id=(reply->>'reply_message_id')::uuid)
     <>'normal:normal answer' then raise exception 'normal_reply_mutated'; end if;
 refused:=false;
 begin
  perform public.session_reply(target,sid,(older->>'message_id')::uuid,'bad',p_priority=>'critical');
 exception when raise_exception then
  if sqlerrm<>'invalid_priority' then raise; end if;
  refused:=true;
 end;
 if not refused then raise exception 'invalid_reply_priority_accepted'; end if;

 -- Internal callers reach the wrapper directly with a payload that has no
 -- priority key at all (as other server functions call message_send). They
 -- must keep working unchanged and land as normal; an explicit key still binds.
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 direct:=quorum_private.call('message_send',jsonb_build_object('quorum_id',a.quorum_id,
  'sender_agent_id',b.id,'recipient_agent_id',target,'conversation_id',c,'reply_to',null,
  'body','internal caller without a priority key'),'prio:'||c||':internal');
 if direct->>'message_id' is null then raise exception 'keyless_internal_send_failed'; end if;
 if (select priority from quorum_private.messages where id=(direct->>'message_id')::uuid)<>'normal' then
  raise exception 'keyless_internal_send_not_normal'; end if;
 if quorum_private.call('message_send',jsonb_build_object('quorum_id',a.quorum_id,
   'sender_agent_id',b.id,'recipient_agent_id',target,'conversation_id',c,'reply_to',null,
   'body','internal caller without a priority key'),'prio:'||c||':internal')->>'message_id'
   <>direct->>'message_id' then raise exception 'keyless_internal_replay_created_message'; end if;
 direct:=quorum_private.call('message_send',jsonb_build_object('quorum_id',a.quorum_id,
  'sender_agent_id',b.id,'recipient_agent_id',target,'conversation_id',c,'reply_to',null,
  'body','internal urgent caller','priority','urgent'),'prio:'||c||':internal-urgent');
 if (select priority from quorum_private.messages where id=(direct->>'message_id')::uuid)<>'urgent' then
  raise exception 'internal_urgent_not_bound'; end if;
 -- Other dispatcher operations, including request-free reads, pass through
 -- the wrapper untouched.
 if jsonb_array_length(public.conversation_read(a.quorum_id,b.id,c,0))=0 then
  raise exception 'non_message_operation_changed'; end if;

 -- A server-generated Hello goes through this priority dispatcher without a
 -- priority key: it stays normal, keeps its notification marker, and an
 -- urgent message to the same recipient is still selected ahead of it.
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(greeter,a.quorum_id,a.device_id,a.owner_id,'priority-hello','hello-sender','claude',gsid),
       (listener,a.quorum_id,a.device_id,a.owner_id,'priority-hello','hello-listener','codex',lsid);
 perform public.session_heartbeat(greeter,gsid,gen_random_uuid(),2,'0.3.0');
 head:=public.session_inbox(listener,lsid)->0;
 if head is null or head->>'sender_id'<>greeter::text then raise exception 'hello_missing'; end if;
 if head->>'priority'<>'normal' or not coalesce((head->>'notification')::boolean,false) then
  raise exception 'hello_metadata_lost'; end if;
 if (select priority from quorum_private.messages where id=(head->>'id')::uuid)<>'normal' then
  raise exception 'hello_not_normal'; end if;
 if exists(select 1 from quorum_private.deliveries where message_id=(head->>'id')::uuid and requires_handler) then
  raise exception 'hello_reply_loop_enabled'; end if;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 urgent:=public.message_send(a.quorum_id,b.id,gen_random_uuid(),listener,null,'urgent after hello',
  'prio:'||c||':after-hello',p_priority=>'urgent');
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 direct:=public.session_inbox(listener,lsid)->0;
 if direct->>'id'<>urgent->>'message_id' or direct->>'priority'<>'urgent'
   or coalesce((direct->>'notification')::boolean,true) then raise exception 'urgent_hidden_behind_hello'; end if;
 perform public.session_ack(listener,lsid,(urgent->>'message_id')::uuid);
 if public.session_inbox(listener,lsid)->0->>'id'<>head->>'id' then raise exception 'hello_lost_after_urgent'; end if;

 -- Priority changes no authorization: the owner/session contract is unchanged.
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 begin
  perform public.session_inbox(target,sid); raise exception 'cross_owner_allowed';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 begin
  perform public.session_reply(target,'wrong-session',(older->>'message_id')::uuid,'x',p_priority=>'urgent');
  raise exception 'foreign_session_allowed';
 exception when insufficient_privilege then null; end;
 if exists(select 1 from quorum_private.messages where priority not in ('normal','urgent')) then
  raise exception 'unconstrained_priority'; end if;
 if has_function_privilege('anon','public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text)','EXECUTE')
   or has_function_privilege('anon','public.session_reply(uuid,text,uuid,text,text)','EXECUTE')
   or has_function_privilege('anon','public.session_inbox(uuid,text)','EXECUTE') then
  raise exception 'anon_allowed'; end if;
 if not has_function_privilege('authenticated','public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text)','EXECUTE')
   or not has_function_privilege('authenticated','public.session_reply(uuid,text,uuid,text,text)','EXECUTE') then
  raise exception 'authenticated_denied'; end if;
 if has_function_privilege('authenticated','quorum_private.call_priority_core(text,jsonb,text)','EXECUTE') then
  raise exception 'private_core_exposed'; end if;
end $test$;
