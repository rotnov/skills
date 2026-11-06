-- Disposable PostgreSQL only: the Hello result reports sent and skipped apart (#146).
-- The runner sets quorum_test.release to the release the server must require. One
-- transaction keeps now() fixed, so a reconnect stays inside the dedupe window.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 sender uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 first_peer uuid:=gen_random_uuid(); second_peer uuid:=gen_random_uuid(); late_peer uuid:=gen_random_uuid();
 connection uuid:=gen_random_uuid(); result jsonb; sent bigint;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000146');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'hello-split',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (sender,q,dev,owner_id,'split','flaky-codex','codex',sid),
  (first_peer,q,dev,owner_id,'split','alpha','claude',gen_random_uuid()::text),
  (second_peer,q,dev,owner_id,'split','beta','grok',gen_random_uuid()::text);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- The first connection greets everyone and skips no one.
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if result->'hello'->>'peers'<>'2' or result->'hello'->'skipped'<>'0'::jsonb then
  raise exception 'split_first_connect_wrong: %',result; end if;

 -- A reconnect inside the window sends nothing, and now says so; `peers` keeps its meaning.
 select count(*) into sent from quorum_private.messages where sender_id=sender and recipient_id<>sender;
 result:=public.session_heartbeat(sender,sid,connection,p,v);
 if result->'hello'->>'peers'<>'2' or result->'hello'->'skipped'<>'2'::jsonb or result ? 'hello_skipped' then
  raise exception 'split_reconnect_wrong: %',result; end if;
 if (select count(*) from quorum_private.messages where sender_id=sender and recipient_id<>sender)<>sent then
  raise exception 'split_reconnect_sent'; end if;

 -- A retry of that connection repeats the stored split, not an empty one.
 result:=public.session_heartbeat(sender,sid,connection,p,v);
 if result->>'new_connection'<>'false' or result->'hello'->'skipped'<>'2'::jsonb then
  raise exception 'split_retry_wrong: %',result; end if;

 -- A peer that joined since is greeted, so the same result holds both kinds.
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(late_peer,q,dev,owner_id,'split','gamma','claude',gen_random_uuid()::text);
 result:=public.session_heartbeat(sender,sid,gen_random_uuid(),p,v);
 if result->'hello'->>'peers'<>'3' or result->'hello'->'skipped'<>'2'::jsonb then
  raise exception 'split_late_peer_wrong: %',result; end if;

 -- A connection recorded before this release reports the split as unknown.
 update quorum_private.session_hellos set skipped=null where agent_id=sender and connection_id=connection;
 result:=public.session_heartbeat(sender,sid,connection,p,v);
 if not (result->'hello' ? 'skipped') or result->'hello'->'skipped'<>'null'::jsonb then
  raise exception 'split_old_row_wrong: %',result; end if;

 -- Clients match the roster text exactly; the split leaves it alone.
 if exists(select 1 from quorum_private.messages where sender_id=sender and recipient_id=sender
   and body not like 'Quorum roster: %. Transport notice; no reply required.') then
  raise exception 'split_roster_text_changed'; end if;
end $test$;
