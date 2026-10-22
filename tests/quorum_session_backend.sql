-- Run inside BEGIN/ROLLBACK together with the session transport extension.
-- Uses two existing consenting pilot members; every fixture remains uncommitted.
do $test$
declare
 a quorum_private.agents%rowtype; b quorum_private.agents%rowtype;
 target uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 c uuid:=gen_random_uuid(); sent jsonb; reply jsonb; again jsonb; i integer;
begin
 select x.* into a from quorum_private.agents x where not x.revoked and exists
  (select 1 from quorum_private.agents y where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 if not found then raise exception 'two_pilot_members_required'; end if;
 select * into b from quorum_private.agents where quorum_id=a.quorum_id and owner_id<>a.owner_id and not revoked limit 1;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(target,a.quorum_id,a.device_id,a.owner_id,b.project_ref,'native-test','codex',sid);
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 sent:=public.message_send(a.quorum_id,b.id,c,target,null,'native transport test','native-test:'||c);
 begin
  perform public.session_inbox(target,sid); raise exception 'cross_owner_allowed';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 begin
  perform public.session_inbox(target,'wrong-session'); raise exception 'foreign_session_allowed';
 exception when insufficient_privilege then null; end;
 -- All native clients use the same owner/session authorization contract.
 update quorum_private.agents set engine='claude' where id=target;
 if jsonb_array_length(public.session_inbox(target,sid))<>1 then raise exception 'claude_inbox_missing'; end if;
 update quorum_private.agents set engine='grok' where id=target;
 if not quorum_private.receive_wakeup('quorum:agent:'||target) then raise exception 'grok_wake_denied'; end if;
 begin
  update quorum_private.agents set engine='unknown-engine' where id=target;
  raise exception 'unknown_engine_allowed';
 exception when check_violation then null; end;
 if jsonb_array_length(public.session_inbox(target,sid))<>1 then raise exception 'inbox_missing'; end if;
 perform public.session_ack(target,sid,(sent->>'message_id')::uuid);
 if jsonb_array_length(public.session_inbox(target,sid))<>0 then raise exception 'ack_not_persisted'; end if;
 reply:=public.session_reply(target,sid,(sent->>'message_id')::uuid,'pong from existing session');
 again:=public.session_reply(target,sid,(sent->>'message_id')::uuid,'pong from existing session');
 if reply->>'reply_message_id'<>again->>'reply_message_id' then raise exception 'duplicate_reply'; end if;
 if (select count(*) from quorum_private.messages where conversation_id=c)<>2 then raise exception 'duplicate_message'; end if;
 if exists(select 1 from quorum_private.deliveries where message_id=(reply->>'reply_message_id')::uuid and requires_handler) then raise exception 'reply_loop_enabled'; end if;
 if not exists(select 1 from quorum_private.deliveries where message_id=(sent->>'message_id')::uuid and state='answered') then raise exception 'not_answered'; end if;
 -- A native recipient also receives automatic replies, without requiring a handler claim.
 update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
 delete from quorum_private.session_receipts where agent_id=target;
 if jsonb_array_length(public.session_inbox(target,sid))<>1 then raise exception 'notification_missing'; end if;
 perform public.session_ack(target,sid,(sent->>'message_id')::uuid);
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 for i in 1..16 loop
  perform public.message_send(a.quorum_id,b.id,c,target,null,repeat('x',16384),'native-bound:'||c||':'||i);
 end loop;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 if octet_length(public.session_inbox(target,sid)::text)>262144 then raise exception 'response_too_large_for_client'; end if;
 update quorum_private.agents set revoked=true where id=target;
 begin
  perform public.session_inbox(target,sid); raise exception 'revocation_ignored';
 exception when insufficient_privilege then null; end;
 if has_function_privilege('anon','public.session_inbox(uuid,text)','EXECUTE') then raise exception 'anon_allowed'; end if;
 if has_table_privilege('authenticated','quorum_private.session_receipts','SELECT') then raise exception 'private_receipts_exposed'; end if;
end $test$;
