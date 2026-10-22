-- Actual migration, authorization predicates, policies and triggers run in
-- disposable PostgreSQL. Only Supabase's Realtime send boundary is a fixture.
do $test$
declare
 a quorum_private.agents%rowtype; b quorum_private.agents%rowtype;
 c uuid:=gen_random_uuid(); sent jsonb; n integer; message_count integer;
begin
 select x.* into a from quorum_private.agents x where not x.revoked and exists
  (select 1 from quorum_private.agents y where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 select * into b from quorum_private.agents where quorum_id=a.quorum_id and owner_id<>a.owner_id and not revoked limit 1;
 if a.id is null or b.id is null then raise exception 'two_members_required'; end if;
 perform set_config('quorum_test.agent',a.id::text,true);
 perform set_config('quorum_test.owner',a.owner_id::text,true);
 perform set_config('quorum_test.device',a.device_id::text,true);
 perform set_config('quorum_test.quorum',a.quorum_id::text,true);
 perform set_config('quorum_test.foreign_topic','quorum:agent:'||b.id,true);
 perform set_config('realtime.topic','quorum:agent:'||a.id,true);
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 if not quorum_private.receive_wakeup('quorum:agent:'||a.id) then raise exception 'own_channel_denied'; end if;
 if quorum_private.receive_wakeup('quorum:agent:'||b.id) then raise exception 'foreign_channel_allowed'; end if;
 if quorum_private.receive_wakeup('quorum:agent:invalid') then raise exception 'invalid_channel_allowed'; end if;
 select count(*) into n from realtime.messages;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 sent:=public.message_send(a.quorum_id,b.id,c,a.id,null,'private message body','realtime:'||c);
 if (select count(*) from realtime.messages)<>n+1 then raise exception 'new_message_did_not_wake'; end if;
 if not exists(select 1 from realtime.messages where topic='quorum:agent:'||a.id
  and event='inbox_changed' and payload='{}'::jsonb and private) then raise exception 'signal_contract_broken'; end if;
 if exists(select 1 from realtime.messages where payload::text like '%private message body%') then raise exception 'body_leaked_to_broadcast'; end if;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 perform public.session_ack(a.id,a.session_ref,(sent->>'message_id')::uuid);
 if (select count(*) from realtime.messages)<>n+2 then raise exception 'receipt_did_not_wake_next_message'; end if;
 -- A second idempotent receipt must not create a second wake hint.
 perform public.session_ack(a.id,a.session_ref,(sent->>'message_id')::uuid);
 if (select count(*) from realtime.messages)<>n+2 then raise exception 'duplicate_receipt_signal'; end if;
 -- Broadcast unavailable: the authoritative message transaction still commits.
 perform set_config('quorum_test.broadcast_failed','yes',true);
 select count(*) into message_count from quorum_private.messages;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 perform public.message_send(a.quorum_id,b.id,c,a.id,null,'survives broadcast outage','realtime-outage:'||c);
 if (select count(*) from quorum_private.messages)<>message_count+1 then raise exception 'broadcast_failure_lost_durable_message'; end if;
 perform set_config('quorum_test.broadcast_failed','no',true);
 -- Delivery insertion and wake hint belong to the same transaction.
 select count(*) into n from realtime.messages;
 select count(*) into message_count from quorum_private.messages;
 begin
  perform public.message_send(a.quorum_id,b.id,c,a.id,null,'rolled back','realtime-rollback:'||c);
  raise exception 'abort_fixture_transaction';
 exception when raise_exception then
  if sqlerrm<>'abort_fixture_transaction' then raise; end if;
 end;
 if (select count(*) from realtime.messages)<>n or (select count(*) from quorum_private.messages)<>message_count then
  raise exception 'notification_escaped_rollback'; end if;
 -- A private policy must not accidentally grant schema/table access.
 if has_schema_privilege('authenticated','quorum_private','USAGE')
  or has_table_privilege('authenticated','quorum_private.messages','SELECT') then raise exception 'private_schema_exposed'; end if;
 if has_function_privilege('anon','quorum_private.receive_wakeup(text)','EXECUTE')
  or has_function_privilege('authenticated','quorum_private.notify_inbox()','EXECUTE') then raise exception 'privileged_function_exposed'; end if;
 insert into realtime.messages(topic,event,payload,private) values('foreign-topic','inbox_changed','{}',true);
 insert into realtime.messages(topic,event,payload,private,extension) values('quorum:agent:'||a.id,'inbox_changed','{}',true,'presence');
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
end $test$;

set local role authenticated;
do $test$
begin
 if not exists(select 1 from realtime.messages) then raise exception 'own_broadcast_policy_denied'; end if;
 if exists(select 1 from realtime.messages where topic<>current_setting('realtime.topic')) then raise exception 'foreign_broadcast_policy_allowed'; end if;
 if exists(select 1 from realtime.messages where extension<>'broadcast') then raise exception 'presence_policy_allowed'; end if;
 begin
  insert into realtime.messages(topic,event,payload,private) values(current_setting('realtime.topic'),'inbox_changed','{}',true);
  raise exception 'client_broadcast_allowed';
 exception when insufficient_privilege then null; end;
end $test$;
select set_config('realtime.topic',current_setting('quorum_test.foreign_topic'),true);
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'foreign_agent_subscription_allowed'; end if; end $$;
select set_config('realtime.topic','quorum:agent:'||current_setting('quorum_test.agent'),true);
reset role;

-- Every current revocation condition must deny the very next authorization/RPC.
update quorum_private.agents set revoked=true where id=current_setting('quorum_test.agent')::uuid;
set local role authenticated;
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'revoked_agent_policy_allowed'; end if; end $$;
reset role;
update quorum_private.agents set revoked=false where id=current_setting('quorum_test.agent')::uuid;
update quorum_private.devices set revoked=true where id=current_setting('quorum_test.device')::uuid;
set local role authenticated;
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'revoked_device_policy_allowed'; end if; end $$;
reset role;
update quorum_private.devices set revoked=false where id=current_setting('quorum_test.device')::uuid;
update quorum_private.memberships set revoked=true where quorum_id=current_setting('quorum_test.quorum')::uuid and user_id=current_setting('quorum_test.owner')::uuid;
set local role authenticated;
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'revoked_member_policy_allowed'; end if; end $$;
reset role;
update quorum_private.memberships set revoked=false where quorum_id=current_setting('quorum_test.quorum')::uuid and user_id=current_setting('quorum_test.owner')::uuid;
update auth.identities set provider='not-github' where user_id=current_setting('quorum_test.owner')::uuid and provider='github';
set local role authenticated;
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'non_github_policy_allowed'; end if; end $$;
reset role;
update auth.identities set provider='github' where user_id=current_setting('quorum_test.owner')::uuid and provider='not-github';
select set_config('request.jwt.claim.sub','',true);
set local role anon;
do $$ begin if exists(select 1 from realtime.messages) then raise exception 'anonymous_channel_allowed'; end if; end $$;
reset role;
