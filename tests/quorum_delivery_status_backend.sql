-- Disposable PostgreSQL only: one recipient's status of one message, for its sender (#162).
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 sender uuid:=gen_random_uuid(); sender_sid text:=gen_random_uuid()::text;
 recipient uuid:=gen_random_uuid(); recipient_sid text:=gen_random_uuid()::text;
 third uuid:=gen_random_uuid(); third_sid text:=gen_random_uuid()::text;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 first_id uuid; second_id uuid; third_id uuid; notice_id uuid; stale_id uuid; old_client_id uuid; late_id uuid;
 result jsonb; denied boolean; hints bigint; seen bigint;
begin
 insert into auth.users(id) values(owner_id),(stranger);
 insert into auth.identities(user_id,provider,provider_id) values
  (owner_id,'github','9000000000162'),(stranger,'github','9000000000163');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'delivery-status',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (sender,q,dev,owner_id,'status','asker','claude',sender_sid),
  (recipient,q,dev,owner_id,'status','doer','codex',recipient_sid),
  (third,q,dev,owner_id,'status','bystander','grok',third_sid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 first_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'first body','status-1','normal')->>'message_id')::uuid;

 -- Before anything is reported, both ends read `sent`; nobody else reads anything.
 result:=public.session_delivery_status(sender,sender_sid,first_id);
 if result->>'status'<>'sent' or result->>'recipient_role'<>'doer' or result->>'recipient_id'<>recipient::text
    or (result->>'receipted')::boolean then raise exception 'sent_view_wrong: %',result; end if;
 if result ? 'body' then raise exception 'body_in_status'; end if;
 if public.session_delivery_status(recipient,recipient_sid,first_id)->>'status'<>'sent' then raise exception 'recipient_read_denied'; end if;
 if public.session_delivery_status(third,third_sid,first_id) is not null then raise exception 'third_agent_read_it'; end if;
 if public.session_delivery_status(sender,sender_sid,gen_random_uuid()) is not null then raise exception 'unknown_read'; end if;
 denied:=false;
 begin perform public.session_delivery_status(sender,gen_random_uuid()::text,first_id);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_session_read_it'; end if;
 denied:=false;
 perform set_config('request.jwt.claim.sub',stranger::text,true);
 begin perform public.session_delivery_status(sender,sender_sid,first_id);
 exception when insufficient_privilege then denied:=true; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 if not denied then raise exception 'other_owner_read_it'; end if;

 -- Only the recipient writes, and only from its exact session.
 foreach result in array array[jsonb_build_object('a',sender,'s',sender_sid),jsonb_build_object('a',third,'s',third_sid)] loop
  denied:=false;
  begin perform public.session_delivery_mark((result->>'a')::uuid,result->>'s',first_id,'done');
  exception when insufficient_privilege then
   denied:=sqlerrm='message_denied'; end;
  if not denied then raise exception 'non_recipient_wrote: %',result; end if;
 end loop;
 denied:=false;
 begin perform public.session_delivery_mark(recipient,gen_random_uuid()::text,first_id,'done');
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_session_wrote'; end if;

 -- `read` needs a named proof, and the server alone writes `answered` and `sent`.
 foreach result in array array[
  '{"s":"read"}','{"s":"read","p":"exit_code"}','{"s":"queued","p":"received"}','{"s":"answered"}','{"s":"sent"}',
  '{"s":"cancelled"}','{"s":"expired"}','{"s":"deferred"}','{"s":"declined"}','{"s":"queued","r":"why"}',
  '{"s":"read","p":"received","r":"why"}']::jsonb[] loop
  denied:=false;
  begin perform public.session_delivery_mark(recipient,recipient_sid,first_id,result->>'s',result->>'r',result->>'p');
  exception when others then denied:=sqlerrm='invalid_delivery_status'; end;
  if not denied then raise exception 'invalid_mark_accepted: %',result; end if;
 end loop;
 denied:=false;
 begin perform public.session_delivery_mark(recipient,recipient_sid,first_id,'failed',repeat('x',513));
 exception when others then denied:=sqlerrm='invalid_delivery_status'; end;
 if not denied then raise exception 'long_reason_accepted'; end if;
 if exists(select 1 from quorum_private.delivery_statuses where message_id=first_id) then raise exception 'refused_mark_wrote'; end if;

 -- A receipt moves the queue and claims nothing: no row, no `read`, no hint.
 select count(*) into hints from realtime.messages where event='status_changed';
 perform public.session_ack(recipient,recipient_sid,first_id);
 if exists(select 1 from quorum_private.delivery_statuses where message_id=first_id) then raise exception 'receipt_wrote_a_status'; end if;
 result:=public.session_delivery_status(sender,sender_sid,first_id);
 if result->>'status'<>'sent' or not (result->>'receipted')::boolean then raise exception 'receipt_view_wrong: %',result; end if;
 if (select count(*) from realtime.messages where event='status_changed')<>hints then raise exception 'receipt_hinted'; end if;

 -- Facts move forward, keep their first time and send one empty hint per change.
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'queued');
 if not exists(select 1 from realtime.messages where topic='quorum:agent:'||sender and event='status_changed'
               and payload='{}'::jsonb and private) then raise exception 'hint_contract_broken'; end if;
 if (select count(*) from realtime.messages where event='status_changed')<>hints+1 then raise exception 'hint_count'; end if;
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'queued');
 if (select count(*) from realtime.messages where event='status_changed')<>hints+1 then raise exception 'repeat_hinted'; end if;
 result:=public.session_delivery_mark(recipient,recipient_sid,first_id,'read',null,'body_read');
 if result->>'status'<>'read' or result->>'proof'<>'body_read' or result->>'read_at' is null then raise exception 'read_wrong: %',result; end if;
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'queued');
 if public.session_delivery_status(sender,sender_sid,first_id)->>'status'<>'read' then raise exception 'queued_downgraded_read'; end if;

 -- Explicit statuses replace each other, with a reason, until an outcome ends them.
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'deferred','after the release');
 result:=public.session_delivery_status(sender,sender_sid,first_id);
 if result->>'status'<>'deferred' or result->>'reason'<>'after the release' then raise exception 'deferred_wrong: %',result; end if;
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'in_progress');
 result:=public.session_delivery_status(sender,sender_sid,first_id);
 if result->>'status'<>'in_progress' or result->>'reason' is not null then raise exception 'reason_kept: %',result; end if;
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'needs_owner');
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'done');
 perform public.session_delivery_mark(recipient,recipient_sid,first_id,'done');
 denied:=false;
 begin perform public.session_delivery_mark(recipient,recipient_sid,first_id,'failed','too late');
 exception when others then denied:=sqlerrm='delivery_status_final'; end;
 if not denied then raise exception 'outcome_replaced'; end if;

 -- A late fact keeps an outcome and still records its time.
 result:=public.session_reply(recipient,recipient_sid,first_id,'the answer','normal');
 result:=public.session_delivery_status(sender,sender_sid,first_id);
 if result->>'status'<>'done' or result->>'answered_at' is null then raise exception 'answer_over_outcome: %',result; end if;

 -- Without an outcome the server records `answered` from the reply, for any client.
 second_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'second body','status-2','normal')->>'message_id')::uuid;
 perform public.session_delivery_mark(recipient,recipient_sid,second_id,'in_progress');
 result:=public.session_reply(recipient,recipient_sid,second_id,'second answer','normal');
 result:=public.session_delivery_status(sender,sender_sid,second_id);
 if result->>'status'<>'answered' or result->>'read_at' is not null then raise exception 'reply_status_wrong: %',result; end if;
 -- An identical retry returns the first answer and changes nothing.
 select seq into seen from quorum_private.delivery_statuses where message_id=second_id;
 perform public.session_reply(recipient,recipient_sid,second_id,'second answer','normal');
 if (select seq from quorum_private.delivery_statuses where message_id=second_id)<>seen then raise exception 'reply_retry_changed'; end if;

 -- The feed is the sender's alone, in order, resumes after a cursor and holds no reason.
 result:=public.session_delivery_changes(sender,sender_sid,0);
 if jsonb_array_length(result->'rows')<>2 or (result->>'more')::boolean or (result->>'cursor')::bigint<=0
    or (result->'rows'->0->>'seq')::bigint>=(result->'rows'->1->>'seq')::bigint
    or result->'rows'->0->>'message_id'<>first_id::text or result->'rows'->1->>'status'<>'answered'
    or result->'rows'->0->>'recipient_role'<>'doer' or result::text like '%reason%' then
  raise exception 'feed_wrong: %',result; end if;
 if jsonb_array_length(public.session_delivery_changes(sender,sender_sid,(result->>'cursor')::bigint)->'rows')<>0 then
  raise exception 'feed_repeats'; end if;
 if jsonb_array_length(public.session_delivery_changes(recipient,recipient_sid,0)->'rows')<>0 then raise exception 'feed_not_the_senders'; end if;
 if jsonb_array_length(public.session_delivery_changes(third,third_sid,0)->'rows')<>0 then raise exception 'feed_leaked'; end if;
 denied:=false;
 begin perform public.session_delivery_changes(sender,sender_sid,-1);
 exception when others then denied:=sqlerrm='invalid_delivery_status'; end;
 if not denied then raise exception 'negative_cursor_accepted'; end if;

 -- An unavailable broadcaster never costs a status, and a reply survives a status failure.
 third_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'third body','status-3','normal')->>'message_id')::uuid;
 perform set_config('quorum_test.broadcast_failed','yes',true);
 perform public.session_delivery_mark(recipient,recipient_sid,third_id,'queued');
 perform set_config('quorum_test.broadcast_failed','no',true);
 if public.session_delivery_status(sender,sender_sid,third_id)->>'status'<>'queued' then raise exception 'hint_failure_lost_status'; end if;

 -- A transport notice gets no status: refused, unreadable and absent after a heartbeat with Hello.
 notice_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'notice body','status-notice','normal')->>'message_id')::uuid;
 insert into quorum_private.session_notices values(notice_id,sender);
 denied:=false;
 begin perform public.session_delivery_mark(recipient,recipient_sid,notice_id,'queued');
 exception when others then denied:=sqlerrm='delivery_status_not_applicable'; end;
 if not denied then raise exception 'notice_status_accepted'; end if;
 if public.session_delivery_status(sender,sender_sid,notice_id) is not null then raise exception 'notice_status_readable'; end if;

 -- The feed and its hint carry status changes only: a late fact on the same status is no new row.
 late_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'late body','status-late','normal')->>'message_id')::uuid;
 perform public.session_delivery_mark(recipient,recipient_sid,late_id,'needs_owner');
 select seq into seen from quorum_private.delivery_statuses where message_id=late_id;
 select count(*) into hints from realtime.messages where event='status_changed';
 perform public.session_delivery_mark(recipient,recipient_sid,late_id,'read',null,'received');
 if (select seq from quorum_private.delivery_statuses where message_id=late_id)<>seen then raise exception 'late_fact_moved_the_feed'; end if;
 if (select count(*) from realtime.messages where event='status_changed')<>hints then raise exception 'late_fact_hinted'; end if;
 if public.session_delivery_status(sender,sender_sid,late_id)->>'read_at' is null then raise exception 'late_fact_lost'; end if;
 perform public.session_delivery_mark(recipient,recipient_sid,late_id,'done');
 if (select seq from quorum_private.delivery_statuses where message_id=late_id)<=seen then raise exception 'status_change_stayed_behind'; end if;
 select count(*) into seen from quorum_private.delivery_statuses;
 select count(*) into hints from realtime.messages where event='status_changed';
 result:=public.session_heartbeat(sender,sender_sid,gen_random_uuid(),(required->>'protocol_version')::integer,required->>'skill_version');
 if result->'hello'->>'peers'<>'2' then raise exception 'heartbeat_sent_no_hello: %',result; end if;
 if (select count(*) from quorum_private.delivery_statuses)<>seen
    or (select count(*) from realtime.messages where event='status_changed')<>hints then raise exception 'heartbeat_touched_statuses'; end if;

 -- Nothing is stuck yet, and the check says when the next message comes due.
 result:=public.session_delivery_stuck(sender,sender_sid);
 if jsonb_array_length(result->'stuck')<>0 or (result->>'open')::integer<>1 or (result->>'limit_seconds')::integer<>900
    or (result->>'next_check_seconds')::integer not between 1 and 900 then raise exception 'stuck_early: %',result; end if;

 -- Past the limit: queued and sent are unread. A receipt with no status is an old client and is not claimed.
 update quorum_private.delivery_statuses set status_at=now()-interval '20 minutes' where message_id=third_id;
 stale_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'unsent body','status-4','normal')->>'message_id')::uuid;
 old_client_id:=(public.message_send(q,sender,gen_random_uuid(),recipient,null,'old client','status-5','normal')->>'message_id')::uuid;
 perform public.session_ack(recipient,recipient_sid,old_client_id);
 update quorum_private.deliveries set created_at=now()-interval '20 minutes' where message_id in (stale_id,old_client_id);
 result:=public.session_delivery_stuck(sender,sender_sid);
 if (result->>'stuck_total')::integer<>2 or (result->>'open')::integer<>2 or result->>'next_check_seconds' is not null
    or not (result->'stuck' @> jsonb_build_array(jsonb_build_object('message_id',stale_id,'status','sent','unread',true)))
    or not (result->'stuck' @> jsonb_build_array(jsonb_build_object('message_id',third_id,'status','queued','unread',true)))
    or result::text like '%'||old_client_id::text||'%' or result::text like '%body%' then
  raise exception 'stuck_wrong: %',result; end if;
 if (public.session_delivery_stuck(recipient,recipient_sid)->>'open')::integer<>0 then raise exception 'stuck_not_the_senders'; end if;

 -- Older than the window is dropped, and an outcome settles a message.
 update quorum_private.deliveries set created_at=now()-interval '8 days' where message_id=stale_id;
 perform public.session_delivery_mark(recipient,recipient_sid,third_id,'failed');
 result:=public.session_delivery_stuck(sender,sender_sid);
 if (result->>'open')::integer<>0 or jsonb_array_length(result->'stuck')<>0 then raise exception 'stuck_not_settled: %',result; end if;

 -- A status decides nothing: the task authority of a `failed` message is unchanged.
 if public.session_task_authority(recipient,recipient_sid,third_id)-'message_id' is distinct from
    public.session_task_authority(recipient,recipient_sid,stale_id)-'message_id' then raise exception 'status_changed_authority'; end if;

 -- No client role touches the tables; only the four RPCs are callable, by `authenticated` alone.
 if has_table_privilege('authenticated','quorum_private.delivery_statuses','select,insert,update,delete')
    or has_table_privilege('anon','quorum_private.delivery_statuses','select')
    or has_table_privilege('service_role','quorum_private.delivery_statuses','select')
    or has_table_privilege('authenticated','quorum_private.delivery_status_streams','select,insert,update,delete') then
  raise exception 'table_exposed'; end if;
 if has_function_privilege('authenticated','quorum_private.delivery_status_apply(quorum_private.messages,text,text,text)','execute')
    or has_function_privilege('anon','public.session_delivery_mark(uuid,text,uuid,text,text,text)','execute')
    or has_function_privilege('service_role','public.session_delivery_stuck(uuid,text)','execute')
    or not has_function_privilege('authenticated','public.session_delivery_changes(uuid,text,bigint)','execute') then
  raise exception 'function_grants_wrong'; end if;
end $test$;
