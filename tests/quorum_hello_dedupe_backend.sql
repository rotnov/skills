-- Disposable PostgreSQL only: per-recipient Hello dedupe on listener reconnects (#126).
-- The runner sets quorum_test.release to the release the server must require. One
-- transaction keeps now() fixed, so elapsed time is simulated by moving stored timestamps.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 sender uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 first_peer uuid:=gen_random_uuid(); second_peer uuid:=gen_random_uuid(); late_peer uuid:=gen_random_uuid();
 connection uuid; result jsonb; again jsonb; messages bigint; notices bigint; rosters bigint;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000126');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'hello-dedupe',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (sender,q,dev,owner_id,'dedupe','flaky-codex','codex',sid),
  (first_peer,q,dev,owner_id,'dedupe','alpha','claude',gen_random_uuid()::text),
  (second_peer,q,dev,owner_id,'dedupe','beta','grok',gen_random_uuid()::text);
 insert into quorum_private.session_health(agent_id,last_seen) values(first_peer,now());
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- A release mismatch writes no health, streak or Hello row.
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),1,'0.0.1');
 if result->>'code' is distinct from 'skill_update_required'
    or exists(select 1 from quorum_private.session_streaks where agent_id=sender)
    or exists(select 1 from quorum_private.session_hello_sent where sender_id=sender) then
  raise exception 'dedupe_mismatch_wrote: %',result; end if;

 -- First connection: both peers are greeted and recorded; the streak starts now.
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if result->>'new_connection'<>'true' or result->'hello'->>'peers'<>'2' or result ? 'hello_skipped' then
  raise exception 'dedupe_first_connect_wrong: %',result; end if;
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>2
    or (select count(*) from quorum_private.session_hello_sent where sender_id=sender)<>2
    or (select online_since from quorum_private.session_streaks where agent_id=sender)<>now() then
  raise exception 'dedupe_first_connect_not_recorded'; end if;

 -- A reconnect inside the window with continuous health sends no Hello, yet the
 -- result and the newcomer roster still count both peers; the internal key never leaks.
 select count(*) into messages from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 select count(*) into rosters from quorum_private.messages where sender_id=sender and recipient_id=sender;
 connection:=gen_random_uuid();
 result:=public.session_heartbeat(sender,sid,connection,p,v);
 if result->>'new_connection'<>'true' or result->'hello'->>'peers'<>'2' or result->'hello'->>'available'<>'1'
    or result->'hello'->'roles'<>'["alpha","beta"]'::jsonb or result ? 'hello_skipped' then
  raise exception 'dedupe_reconnect_result_wrong: %',result; end if;
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages then
  raise exception 'dedupe_reconnect_sent_hello'; end if;
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id=sender)<>rosters+1
    or not exists(select 1 from quorum_private.messages where id=(result->'hello'->>'roster_message_id')::uuid
     and body='Quorum roster: 1 peer(s) available in project dedupe: alpha. Transport notice; no reply required.') then
  raise exception 'dedupe_reconnect_roster_wrong'; end if;
 -- A retry of that same connection reports the same result and sends nothing.
 select count(*) into notices from quorum_private.session_notices;
 again:=public.session_heartbeat(sender,sid,connection,p,v);
 if again->>'new_connection'<>'false' or again->'hello'<>result->'hello' or again ? 'hello_skipped'
    or (select count(*) from quorum_private.session_notices)<>notices then
  raise exception 'dedupe_retry_changed: %',again; end if;

 -- A peer registered since the last Hello is greeted; the others still are not.
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(late_peer,q,dev,owner_id,'dedupe','gamma','codex',gen_random_uuid()::text);
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if result->'hello'->>'peers'<>'3' then raise exception 'dedupe_late_peer_result: %',result; end if;
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages+1
    or not exists(select 1 from quorum_private.messages where sender_id=sender and recipient_id=late_peer) then
  raise exception 'dedupe_late_peer_not_greeted'; end if;

 -- The window ends 30 minutes after the last Hello, per recipient.
 update quorum_private.session_streaks set online_since=now()-interval '2 hours' where agent_id=sender;
 update quorum_private.session_hello_sent set sent_at=now()-interval '31 minutes'
  where sender_id=sender and recipient_id=first_peer;
 update quorum_private.session_hello_sent set sent_at=now()-interval '29 minutes'
  where sender_id=sender and recipient_id<>first_peer;
 select count(*) into messages from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages+1
    or (select sent_at from quorum_private.session_hello_sent where sender_id=sender and recipient_id=first_peer)<>now()
    or result->'hello'->>'peers'<>'3' then
  raise exception 'dedupe_window_not_bounded: %',result; end if;

 -- Continuous presence: health 30 seconds old is not an absence, so nothing is sent.
 update quorum_private.session_hello_sent set sent_at=now()-interval '5 minutes' where sender_id=sender;
 update quorum_private.session_streaks set online_since=now()-interval '10 minutes' where agent_id=sender;
 update quorum_private.session_health set last_seen=now()-interval '30 seconds' where agent_id=sender;
 select count(*) into messages from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 perform public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages
    or (select online_since from quorum_private.session_streaks where agent_id=sender)<>now()-interval '10 minutes' then
  raise exception 'dedupe_continuous_presence_announced'; end if;

 -- A real absence (health at least 60 seconds stale) starts a new streak: every peer is greeted.
 update quorum_private.session_hello_sent set sent_at=now()-interval '5 minutes' where sender_id=sender;
 update quorum_private.session_health set last_seen=now()-interval '60 seconds' where agent_id=sender;
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages+3
    or (select online_since from quorum_private.session_streaks where agent_id=sender)<>now()
    or result->'hello'->>'peers'<>'3' then
  raise exception 'dedupe_absence_not_announced: %',result; end if;

 -- A receipt is health activity too: an absence it ends also starts a new streak.
 update quorum_private.session_hello_sent set sent_at=now()-interval '5 minutes' where sender_id=sender;
 update quorum_private.session_streaks set online_since=now()-interval '10 minutes' where agent_id=sender;
 update quorum_private.session_health set last_seen=now()-interval '3 minutes' where agent_id=sender;
 perform public.session_ack(sender,sid,(select id from quorum_private.messages
  where sender_id=sender and recipient_id=sender order by sequence desc limit 1));
 if (select online_since from quorum_private.session_streaks where agent_id=sender)<>now() then
  raise exception 'dedupe_receipt_absence_ignored'; end if;
 select count(*) into messages from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 perform public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages+3 then
  raise exception 'dedupe_after_receipt_absence_not_announced'; end if;

 -- An agent known only from before this release has no streak: it announces once, then dedupes.
 update quorum_private.session_hello_sent set sent_at=now()-interval '5 minutes' where sender_id=sender;
 update quorum_private.session_health set last_seen=now()-interval '10 seconds' where agent_id=sender;
 delete from quorum_private.session_streaks where agent_id=sender;
 select count(*) into messages from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 perform public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 perform public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>messages+3 then
  raise exception 'dedupe_unknown_streak_not_fail_open'; end if;

 -- Every deduplicated Hello still went out as a quiet notice; peers' inboxes hold no extra copies.
 if exists(select 1 from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
   where m.sender_id=sender and (d.requires_handler or not exists(
    select 1 from quorum_private.session_notices n where n.message_id=m.id))) then
  raise exception 'dedupe_notice_not_quiet'; end if;

 -- Nothing new is exposed to clients, and the release is unchanged.
 if has_table_privilege('authenticated','quorum_private.session_streaks','SELECT')
    or has_table_privilege('authenticated','quorum_private.session_hello_sent','SELECT')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_core_before_dedupe(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_hello_before_dedupe(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.session_health_streak()','EXECUTE')
    or has_function_privilege('anon','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE') then
  raise exception 'dedupe_privileges_wrong'; end if;
 if public.session_requirements()<>required then raise exception 'dedupe_changed_release'; end if;
end $test$;
