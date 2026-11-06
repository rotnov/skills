-- Disposable PostgreSQL only: Hello result reporting and the newcomer roster notice.
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 newcomer uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 solo uuid:=gen_random_uuid(); solo_sid text:=gen_random_uuid()::text;
 crowd uuid:=gen_random_uuid(); crowd_sid text:=gen_random_uuid()::text;
 legacy uuid:=gen_random_uuid(); legacy_sid text:=gen_random_uuid()::text;
 peer uuid; epoch uuid:=gen_random_uuid(); legacy_epoch uuid:=gen_random_uuid();
 result jsonb; again jsonb; hello jsonb; roster uuid; item jsonb; seen boolean:=false;
 notices bigint; messages bigint; amount integer;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000088');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'hello-suite',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (newcomer,q,dev,owner_id,'shared','newcomer','claude',sid),
  (solo,q,dev,owner_id,'alone','solo','codex',solo_sid),
  (crowd,q,dev,owner_id,'crowd','crowd-newcomer','codex',crowd_sid),
  (legacy,q,dev,owner_id,'shared','legacy','codex',legacy_sid);
 -- Three native same-project peers (two currently healthy), plus excluded rows.
 insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (q,dev,owner_id,'shared','zeta-codex','codex',gen_random_uuid()::text),
  (q,dev,owner_id,'shared','alpha-grok','grok',gen_random_uuid()::text),
  (q,dev,owner_id,'other','elsewhere','claude',gen_random_uuid()::text);
 insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref,revoked)
 values(q,dev,owner_id,'shared','gone','claude',gen_random_uuid()::text,true);
 insert into quorum_private.session_health(agent_id,last_seen)
  select id,now() from quorum_private.agents where quorum_id=q and role in ('zeta-codex','alpha-grok');
 -- Twenty peers in another project exercise the bounded role lists.
 for amount in 1..20 loop
  insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
  values(q,dev,owner_id,'crowd','peer-'||lpad(amount::text,2,'0'),'claude',gen_random_uuid()::text);
 end loop;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- A connection announced before this extension: same ID, no Hello, no invented result.
 insert into quorum_private.session_connections values(legacy,legacy_epoch);
 result:=public.session_heartbeat(legacy,legacy_sid,legacy_epoch,p,v);
 if result->>'new_connection'<>'false' or result->'hello'<>'null'::jsonb then
  raise exception 'legacy_connection_reported: %',result; end if;
 update quorum_private.session_health set last_seen=now()-interval '10 minutes' where agent_id=legacy;

 -- A release mismatch writes no Hello row and no roster.
 result:=public.session_heartbeat(newcomer,sid,epoch,1,'0.0.1');
 if result->>'code' is distinct from 'skill_update_required' or result ? 'hello'
    or exists(select 1 from quorum_private.session_hellos where agent_id=newcomer) then
  raise exception 'mismatch_reported_hello: %',result; end if;

 -- New connection: Hello to the three registered peers, roster of the two healthy ones.
 result:=public.session_heartbeat(newcomer,sid,epoch,p,v);
 hello:=result->'hello';
 if result->>'new_connection'<>'true' or hello->>'peers'<>'3' or hello->>'available'<>'2'
    or hello->>'project_ref'<>'shared'
    or hello->'roles'<>'["alpha-grok","legacy","zeta-codex"]'::jsonb
    or hello->'available_roles'<>'["alpha-grok","zeta-codex"]'::jsonb then
  raise exception 'hello_result_wrong: %',result; end if;
 if (select count(*) from quorum_private.session_notices n join quorum_private.messages m on m.id=n.message_id
     where n.agent_id=newcomer and m.recipient_id<>newcomer)<>3 then raise exception 'hello_count_mismatch'; end if;
 roster:=(hello->>'roster_message_id')::uuid;
 if not exists(select 1 from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
   join quorum_private.session_notices n on n.message_id=m.id
   where m.id=roster and m.sender_id=newcomer and m.recipient_id=newcomer and m.reply_to is null
    and not d.requires_handler and d.state='queued'
    and m.body='Quorum roster: 2 peer(s) available in project shared: alpha-grok, zeta-codex. Transport notice; no reply required.')
  then raise exception 'roster_not_quiet_notice'; end if;
 if to_regprocedure('public.session_task_authority(uuid,text,uuid)') is not null and
    public.session_task_authority(newcomer,sid,roster)->>'reason'<>'not_task' then raise exception 'roster_is_task'; end if;
 -- The newcomer's own inbox carries the roster as a notification.
 loop
  result:=public.session_inbox(newcomer,sid);
  exit when jsonb_array_length(result)=0;
  item:=result->0;
  if (item->>'id')::uuid=roster then
   if not (item->>'notification')::boolean then raise exception 'roster_not_notification'; end if;
   seen:=true;
  end if;
  perform public.session_ack(newcomer,sid,(item->>'id')::uuid);
 end loop;
 if not seen then raise exception 'roster_not_in_inbox'; end if;

 -- Retry/keepalive with the same connection: same reported result, nothing new sent.
 select count(*) into notices from quorum_private.session_notices;
 select count(*) into messages from quorum_private.messages;
 again:=public.session_heartbeat(newcomer,sid,epoch,p,v);
 if again->>'new_connection'<>'false' or again->'hello'<>hello then raise exception 'reused_result_changed: %',again; end if;
 if (select count(*) from quorum_private.session_notices)<>notices or (select count(*) from quorum_private.messages)<>messages then
  raise exception 'reused_connection_sent_again'; end if;

 -- A genuinely new connection announces again and gets its own roster.
 result:=public.session_heartbeat(newcomer,sid,gen_random_uuid(),p,v);
 if result->>'new_connection'<>'true' or result->'hello'->>'peers'<>'3'
    or (result->'hello'->>'roster_message_id')::uuid=roster then raise exception 'new_connection_not_announced'; end if;

 -- Zero peers share this project reference.
 result:=public.session_heartbeat(solo,solo_sid,gen_random_uuid(),p,v);
 if result->'hello'->>'peers'<>'0' or result->'hello'->>'available'<>'0' or result->'hello'->'roles'<>'[]'::jsonb then
  raise exception 'solo_result_wrong: %',result; end if;
 if not exists(select 1 from quorum_private.messages where id=(result->'hello'->>'roster_message_id')::uuid
   and body like 'Quorum roster: no other native session is available in project alone.%') then
  raise exception 'solo_roster_missing'; end if;

 -- Many peers: the counts are exact while both role lists stay bounded.
 insert into quorum_private.session_health(agent_id,last_seen)
  select id,now() from quorum_private.agents where project_ref='crowd' and id<>crowd
  on conflict(agent_id) do update set last_seen=excluded.last_seen;
 result:=public.session_heartbeat(crowd,crowd_sid,gen_random_uuid(),p,v);
 if result->'hello'->>'peers'<>'20' or result->'hello'->>'available'<>'20'
    or jsonb_array_length(result->'hello'->'roles')<>16 or jsonb_array_length(result->'hello'->'available_roles')<>16
    or result->'hello'->'roles'->>0<>'peer-01' then raise exception 'crowd_result_wrong: %',result; end if;
 if not exists(select 1 from quorum_private.messages where id=(result->'hello'->>'roster_message_id')::uuid
   and body like 'Quorum roster: 20 peer(s) available in project crowd: peer-01, %, peer-16, .... Transport notice; no reply required.')
  then raise exception 'crowd_roster_unbounded'; end if;

 -- Nothing new is exposed to clients.
 if has_table_privilege('authenticated','quorum_private.session_hellos','SELECT')
    or has_function_privilege('authenticated','quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','EXECUTE')
    or has_function_privilege('anon','public.session_heartbeat'::regproc::oid,'EXECUTE')
    or not has_function_privilege('authenticated','public.session_heartbeat'::regproc::oid,'EXECUTE') then
  raise exception 'hello_privileges_wrong'; end if;
 if public.session_requirements()<>required then raise exception 'hello_changed_release'; end if;
end $test$;
