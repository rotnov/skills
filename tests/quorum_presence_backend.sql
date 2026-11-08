-- Disposable PostgreSQL only: a connection is announced by presence, never by per-peer Hello (#196).
-- Execute inside BEGIN/ROLLBACK after 20261108140000_presence_without_hello.sql.
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 newcomer uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 alpha uuid:=gen_random_uuid(); alpha_sid text:=gen_random_uuid()::text;
 beta uuid:=gen_random_uuid(); beta_sid text:=gen_random_uuid()::text;
 elsewhere uuid:=gen_random_uuid(); stale uuid:=gen_random_uuid(); stale_sid text:=gen_random_uuid()::text;
 old_sender uuid:=gen_random_uuid(); old_sid text:=gen_random_uuid()::text;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer;
 result jsonb; peer jsonb; legacy uuid; connection uuid:=gen_random_uuid();
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000196');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'presence',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (newcomer,q,dev,owner_id,'presence','newcomer','codex',sid),
  (alpha,q,dev,owner_id,'presence','alpha','claude',alpha_sid),
  (beta,q,dev,owner_id,'presence','beta','grok',beta_sid),
  (elsewhere,q,dev,owner_id,'other-project','elsewhere','codex',gen_random_uuid()::text),
  (stale,q,dev,owner_id,'presence','stale','codex',stale_sid),
  (old_sender,q,dev,owner_id,'presence','old-sender','claude',old_sid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- Privileges: one public signature, the moved heartbeat private.
 if to_regprocedure('quorum_private.session_heartbeat_before_presence(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)') is null
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_presence(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('anon','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE')
    or has_function_privilege('service_role','public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','EXECUTE') then
  raise exception 'presence_privileges_wrong'; end if;

 -- A peer that is already online, before the newcomer connects.
 result:=public.session_heartbeat(alpha,alpha_sid,gen_random_uuid(),p);
 if result ? 'code' then raise exception 'alpha_heartbeat_refused: %',result; end if;

 -- The first connection sends no Hello and keeps the response a deployed client reads.
 result:=public.session_heartbeat(newcomer,sid,connection,p);
 if result ? 'code' or result->>'online'<>'true' or result->>'new_connection'<>'true' then
  raise exception 'presence_first_connect_refused: %',result; end if;
 if exists(select 1 from quorum_private.messages where sender_id in (newcomer,alpha) and recipient_id<>sender_id)
    or exists(select 1 from quorum_private.requests where operation='message_send' and request_id like 'hello:%') then
  raise exception 'presence_sent_hello'; end if;
 if result->'hello'->>'peers'<>'4' or result->'hello'->>'skipped'<>'4' or result->'hello'->>'available'<>'1'
    or result->'hello'->'available_roles'<>'["alpha"]'::jsonb
    or result->'hello'->'roles'<>'["alpha", "beta", "old-sender", "stale"]'::jsonb then
  raise exception 'presence_roster_wrong: %',result; end if;
 -- The newcomer's own quiet roster notice is still the one message of this connection.
 if (select count(*) from quorum_private.messages where sender_id=newcomer and recipient_id=newcomer
     and body='Quorum roster: 1 peer(s) available in project presence: alpha. Transport notice; no reply required.')<>1
    or (select count(*) from quorum_private.messages where sender_id=newcomer)<>1 then
  raise exception 'presence_roster_notice_wrong'; end if;

 -- Peers see the newcomer through session peers: available, with a last_seen.
 select value into peer from jsonb_array_elements(public.session_peers(alpha,alpha_sid))
  where value->>'agent_id'=newcomer::text;
 if peer is null or peer->>'availability'<>'online' or peer->>'last_seen' is null then
  raise exception 'presence_not_visible: %',peer; end if;

 -- A retry of the same connection and a reconnect announce nothing either.
 result:=public.session_heartbeat(newcomer,sid,connection,p);
 if result->>'new_connection'<>'false' or result->'hello'->>'skipped'<>'4' then
  raise exception 'presence_retry_wrong: %',result; end if;
 result:=public.session_heartbeat(newcomer,sid,gen_random_uuid(),p,null,0,null,gen_random_uuid(),now());
 if result ? 'code' or result->>'new_connection'<>'true' then raise exception 'presence_reconnect_refused: %',result; end if;
 if exists(select 1 from quorum_private.messages where sender_id=newcomer and recipient_id<>newcomer) then
  raise exception 'presence_reconnect_sent_hello'; end if;

 -- A deployed client that still names a number sends no Hello either.
 result:=public.session_heartbeat(beta,beta_sid,gen_random_uuid(),p,'0.3.0');
 if result ? 'code' or exists(select 1 from quorum_private.messages where sender_id=beta and recipient_id<>beta) then
  raise exception 'presence_numbered_client_wrong: %',result; end if;

 -- A refused heartbeat records nothing for its peers.
 result:=public.session_heartbeat(stale,stale_sid,gen_random_uuid(),p+1);
 if result->>'code'<>'skill_update_required'
    or exists(select 1 from quorum_private.session_hello_sent where sender_id=stale) then
  raise exception 'presence_refused_heartbeat_wrote: %',result; end if;

 -- A Hello an unmigrated server queued earlier is still an ordinary quiet notice its recipient receipts.
 perform quorum_private.session_heartbeat_before_presence(old_sender,old_sid,gen_random_uuid(),p,null,null,null,null,null);
 select m.id into legacy from quorum_private.messages m
  where m.sender_id=old_sender and m.recipient_id=alpha and m.body like 'Hello! Listener connected: old-sender %';
 if legacy is null or not exists(select 1 from quorum_private.session_notices where message_id=legacy) then
  raise exception 'legacy_hello_fixture_missing'; end if;
 perform public.session_ack(alpha,alpha_sid,legacy);
 if not exists(select 1 from quorum_private.session_receipts where agent_id=alpha and message_id=legacy) then
  raise exception 'legacy_hello_not_receipted'; end if;
end $test$;
