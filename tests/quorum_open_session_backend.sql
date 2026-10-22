-- Disposable regression: execute only inside BEGIN/ROLLBACK after the open access,
-- native coordination and open session requirements migrations. No models or calls.
-- The runner sets quorum_test.release to the release the server must require.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); anon_id uuid:=gen_random_uuid(); outsider_id uuid:=gen_random_uuid();
 q uuid; closed_q uuid; invitation uuid; device uuid; agent uuid; owner_device uuid; owner_agent uuid;
 sid text:=gen_random_uuid()::text; owner_sid text:=gen_random_uuid()::text; req text:=gen_random_uuid()::text;
 required jsonb:=current_setting('quorum_test.release')::jsonb; result jsonb; head jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
begin
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(anon_id,'{}'),(outsider_id,'{}');
 insert into auth.identities(user_id,provider,provider_id,identity_data) values(owner_id,'github','9000000000021','{}');
 insert into quorum_private.instance_creators(github_id) values('9000000000021');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 q:=(public.quorum_create('os-'||left(md5(req),8),req)->>'quorum_id')::uuid;
 closed_q:=(public.quorum_create('cs-'||left(md5(req),8),req||'-closed')->>'quorum_id')::uuid;
 perform public.quorum_set_access(q,'open',req);
 invitation:=(public.invite_create_open(q,req)->>'invite_id')::uuid;
 owner_device:=(public.device_register('owner-session',req)->>'device_id')::uuid;
 owner_agent:=(public.agent_join(q,owner_device,'p','coordinator','codex',owner_sid,req)->>'agent_id')::uuid;
 -- Before admission an anonymous user cannot read release metadata or a closed quorum.
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.session_requirements(); raise exception 'unadmitted_anonymous_requirements';
 exception when insufficient_privilege then null; end;
 perform public.invite_accept_id(invitation,req);
 device:=(public.device_register('anon-session',req)->>'device_id')::uuid;
 agent:=(public.agent_join(q,device,'p','claude-cloud','claude',sid,req)->>'agent_id')::uuid;
 begin perform public.agent_join(closed_q,device,'p','claude-cloud','claude',sid||'-x',req||'-x');
  raise exception 'anonymous_joined_closed_quorum';
 exception when insufficient_privilege then null; end;
 -- Connect/start/run: requirements, heartbeat with Hello, peers, inbox, ack and reply.
 if public.session_requirements()<>required then raise exception 'open_member_requirements'; end if;
 result:=public.session_heartbeat(agent,sid,gen_random_uuid(),p,v);
 if result->>'online' is distinct from 'true' or result->>'new_connection' is distinct from 'true'
    or result->'required'<>required then raise exception 'open_member_heartbeat'; end if;
 if not exists(select 1 from quorum_private.session_notices n join quorum_private.messages m on m.id=n.message_id
   where n.agent_id=agent and m.recipient_id=owner_agent) then raise exception 'open_member_hello_missing'; end if;
 if public.session_heartbeat(agent,sid,gen_random_uuid(),1,'0.2.1')->>'code' is distinct from 'skill_update_required' then
  raise exception 'open_member_version_unchecked'; end if;
 if not exists(select 1 from jsonb_array_elements(public.session_peers(agent,sid)) x
   where x->>'agent_id'=agent::text and x->>'availability'='online' and x->>'update_required'='false') then
  raise exception 'open_member_peers'; end if;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.message_send(q,owner_agent,gen_random_uuid(),agent,null,'task for open member',req||'-task');
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 head:=public.session_inbox(agent,sid)->0;
 if head->>'body' is distinct from 'task for open member' then raise exception 'open_member_inbox'; end if;
 perform public.session_ack(agent,sid,(head->>'id')::uuid);
 result:=public.session_reply(agent,sid,(head->>'id')::uuid,'done');
 if (select recipient_id from quorum_private.messages where id=(result->>'reply_message_id')::uuid)<>owner_agent then
  raise exception 'open_member_reply'; end if;
 -- Release metadata never lends owner or creation authority.
 begin perform public.quorum_set_access(q,'github',req||'-anon'); raise exception 'open_member_set_access';
 exception when insufficient_privilege then null; end;
 begin perform public.quorum_create('anon-'||req,req||'-create'); raise exception 'open_member_created_quorum';
 exception when insufficient_privilege then null; end;
 -- A different non-GitHub user without membership stays denied.
 perform set_config('request.jwt.claim.sub',outsider_id::text,true);
 begin perform public.session_requirements(); raise exception 'outsider_requirements';
 exception when insufficient_privilege then null; end;
 -- Closing the quorum revokes the member, which removes every session path.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.quorum_set_access(q,'github',req);
 if public.session_requirements()<>required then raise exception 'github_owner_requirements'; end if;
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.session_requirements(); raise exception 'closed_member_requirements';
 exception when insufficient_privilege then null; end;
 begin perform public.session_heartbeat(agent,sid,gen_random_uuid(),p,v); raise exception 'closed_member_heartbeat';
 exception when insufficient_privilege then null; end;
 begin perform public.session_peers(agent,sid); raise exception 'closed_member_peers';
 exception when insufficient_privilege then null; end;
 if has_function_privilege('anon','public.session_requirements()','EXECUTE') then
  raise exception 'requirements_exposed'; end if;
end $test$;
