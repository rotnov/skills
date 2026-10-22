-- Disposable regression: execute only inside BEGIN/ROLLBACK after
-- 150000 -> 152603 -> 160000 -> 170000. No models or service calls.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); anon_id uuid:=gen_random_uuid(); outsider_id uuid:=gen_random_uuid();
 q uuid; closed_q uuid; invitation uuid; device uuid; agent uuid; owner_device uuid; owner_agent uuid;
 sid text:=gen_random_uuid()::text; owner_sid text:=gen_random_uuid()::text; req text:=gen_random_uuid()::text;
 required jsonb:=jsonb_build_object('protocol_version',2,'skill_version','0.3.0');
 result jsonb; head jsonb; normal_id uuid; urgent_id uuid; conv uuid:=gen_random_uuid(); urgent_conv uuid:=gen_random_uuid(); f text;
begin
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(anon_id,'{}'),(outsider_id,'{}');
 insert into auth.identities(user_id,provider,provider_id,identity_data) values(owner_id,'github','9000000000031','{}');
 insert into quorum_private.instance_creators(github_id) values('9000000000031');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 q:=(public.quorum_create('po-'||left(md5(req),8),req)->>'quorum_id')::uuid;
 closed_q:=(public.quorum_create('pc-'||left(md5(req),8),req||'-closed')->>'quorum_id')::uuid;
 if public.session_requirements()<>required then raise exception 'github_owner_requirements'; end if;
 perform public.quorum_set_access(q,'open',req);
 invitation:=(public.invite_create_open(q,req)->>'invite_id')::uuid;
 owner_device:=(public.device_register('owner-session',req)->>'device_id')::uuid;
 owner_agent:=(public.agent_join(q,owner_device,'p','coordinator','codex',owner_sid,req)->>'agent_id')::uuid;

 -- Before the open invitation is accepted the anonymous user has no path at all.
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.session_requirements(); raise exception 'unadmitted_requirements';
 exception when insufficient_privilege then null; end;
 begin perform public.device_register('anon-early',req||'-early'); raise exception 'unadmitted_device';
 exception when insufficient_privilege then null; end;
 begin perform public.message_send(q,owner_agent,conv,owner_agent,null,'x',req||'-early','urgent');
  raise exception 'unadmitted_message_send';
 exception when insufficient_privilege then null; end;

 perform public.invite_accept_id(invitation,req);
 device:=(public.device_register('anon-session',req)->>'device_id')::uuid;
 agent:=(public.agent_join(q,device,'p','claude-cloud','claude',sid,req)->>'agent_id')::uuid;
 begin perform public.agent_join(closed_q,device,'p','claude-cloud','claude',sid||'-x',req||'-x');
  raise exception 'anonymous_joined_closed_quorum';
 exception when insufficient_privilege then null; end;

 -- Release gate: open member reads 2/0.3.0; a 0.2.0 listener must update first.
 if public.session_requirements()<>required then raise exception 'open_member_requirements'; end if;
 if public.session_heartbeat(agent,sid,gen_random_uuid(),1,'0.2.0')->>'code' is distinct from 'skill_update_required' then
  raise exception 'old_release_counted_online'; end if;
 result:=public.session_heartbeat(agent,sid,gen_random_uuid(),2,'0.3.0');
 if result->>'online' is distinct from 'true' or result->>'new_connection' is distinct from 'true'
    or result->'required'<>required then raise exception 'open_member_heartbeat'; end if;
 if not exists(select 1 from jsonb_array_elements(public.session_peers(agent,sid)) x
   where x->>'agent_id'=agent::text and x->>'availability'='online' and x->>'update_required'='false') then
  raise exception 'open_member_peers'; end if;

 -- Hello reaches the GitHub peer as a notification with normal priority, and a receipt clears it.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 head:=public.session_inbox(owner_agent,owner_sid)->0;
 if head->>'sender_id' is distinct from agent::text or head->>'body' not like 'Hello!%'
    or head->>'notification' is distinct from 'true' or head->>'priority' is distinct from 'normal' then
  raise exception 'hello_not_notification: %', head; end if;
 if (select requires_handler from quorum_private.deliveries where message_id=(head->>'id')::uuid) then
  raise exception 'hello_requires_handler'; end if;
 perform public.session_ack(owner_agent,owner_sid,(head->>'id')::uuid);
 if jsonb_array_length(public.session_inbox(owner_agent,owner_sid))<>0 then raise exception 'hello_receipt_ignored'; end if;

 -- Owner queues normal then urgent; the urgent one is delivered first.
 normal_id:=(public.message_send(q,owner_agent,conv,agent,null,'normal task',req||'-n')->>'message_id')::uuid;
 urgent_id:=(public.message_send(q,owner_agent,urgent_conv,agent,null,'urgent task',req||'-u','urgent')->>'message_id')::uuid;
 -- Replays must agree with stored priority; same priority replays the same row.
 if (public.message_send(q,owner_agent,urgent_conv,agent,null,'urgent task',req||'-u','urgent')->>'message_id')::uuid<>urgent_id then
  raise exception 'urgent_replay_changed'; end if;
 begin perform public.message_send(q,owner_agent,urgent_conv,agent,null,'urgent task',req||'-u');
  raise exception 'urgent_replay_downgraded';
 exception when others then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 begin perform public.message_send(q,owner_agent,conv,agent,null,'normal task',req||'-n','urgent');
  raise exception 'priority_replay_rewrote';
 exception when others then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 begin perform public.message_send(q,owner_agent,conv,agent,null,'x',req||'-bad','critical');
  raise exception 'invalid_priority_accepted';
 exception when others then if sqlerrm<>'invalid_priority' then raise; end if; end;

 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 head:=public.session_inbox(agent,sid)->0;
 if (head->>'id')::uuid<>urgent_id or head->>'priority'<>'urgent' or head->>'notification'<>'false' then
  raise exception 'urgent_not_first: %', head; end if;
 perform public.session_ack(agent,sid,urgent_id);
 head:=public.session_inbox(agent,sid)->0;
 if (head->>'id')::uuid<>normal_id or head->>'priority'<>'normal' then raise exception 'receipt_not_honoured: %', head; end if;
 -- Urgent reply through the open member's own session routes to the owner.
 result:=public.session_reply(agent,sid,urgent_id,'urgent done','urgent');
 if (select recipient_id from quorum_private.messages where id=(result->>'reply_message_id')::uuid)<>owner_agent
    or (select priority from quorum_private.messages where id=(result->>'reply_message_id')::uuid)<>'urgent' then
  raise exception 'open_member_urgent_reply'; end if;
 -- 4-argument reply and 7-argument send keep their defaults for the open member.
 result:=public.session_reply(agent,sid,normal_id,'normal done');
 if (select priority from quorum_private.messages where id=(result->>'reply_message_id')::uuid)<>'normal' then
  raise exception 'open_member_default_reply'; end if;
 -- The open member's reply replays bind priority in both directions.
 if public.session_reply(agent,sid,normal_id,'normal done',null)->>'reply_message_id'<>result->>'reply_message_id' then
  raise exception 'open_member_null_replay'; end if;
 begin perform public.session_reply(agent,sid,normal_id,'normal done','urgent');
  raise exception 'open_member_replay_raised_priority';
 exception when others then if sqlerrm<>'reply_already_sent' then raise; end if; end;
 begin perform public.session_reply(agent,sid,urgent_id,'urgent done');
  raise exception 'open_member_replay_lowered_priority';
 exception when others then if sqlerrm<>'reply_already_sent' then raise; end if; end;
 if (select string_agg(priority||':'||body,',' order by body) from quorum_private.messages
     where reply_to in (urgent_id,normal_id))<>'normal:normal done,urgent:urgent done' then
  raise exception 'open_member_reply_mutated'; end if;
 result:=public.message_send(q,agent,gen_random_uuid(),owner_agent,null,'status',req||'-status');
 if (select priority from quorum_private.messages where id=(result->>'message_id')::uuid)<>'normal' then
  raise exception 'open_member_default_send'; end if;

 -- Priority lends no owner or creator authority.
 begin perform public.quorum_create('anon-'||left(md5(req),8),req||'-create'); raise exception 'open_member_created_quorum';
 exception when insufficient_privilege then null; end;
 begin perform public.quorum_set_access(q,'github',req||'-anon'); raise exception 'open_member_set_access';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_open(q,req||'-anon'); raise exception 'open_member_invited';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_id(q,'9000000000099',req||'-anon'); raise exception 'open_member_invited_id';
 exception when insufficient_privilege then null; end;

 -- An unrelated non-GitHub user stays denied.
 perform set_config('request.jwt.claim.sub',outsider_id::text,true);
 begin perform public.session_requirements(); raise exception 'outsider_requirements';
 exception when insufficient_privilege then null; end;
 begin perform public.message_send(q,agent,gen_random_uuid(),owner_agent,null,'x',req||'-out','urgent');
  raise exception 'outsider_message_send';
 exception when insufficient_privilege then null; end;

 -- Closing the quorum revokes the member: every session and send path is denied.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.message_send(q,owner_agent,gen_random_uuid(),agent,null,'late task',req||'-late','urgent');
 perform public.quorum_set_access(q,'github',req);
 if public.session_requirements()<>required then raise exception 'github_owner_after_close'; end if;
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.session_requirements(); raise exception 'revoked_requirements';
 exception when insufficient_privilege then null; end;
 begin perform public.session_heartbeat(agent,sid,gen_random_uuid(),2,'0.3.0'); raise exception 'revoked_heartbeat';
 exception when insufficient_privilege then null; end;
 begin perform public.session_peers(agent,sid); raise exception 'revoked_peers';
 exception when insufficient_privilege then null; end;
 begin perform public.session_inbox(agent,sid); raise exception 'revoked_inbox';
 exception when insufficient_privilege then null; end;
 begin perform public.session_reply(agent,sid,normal_id,'normal done','urgent'); raise exception 'revoked_reply';
 exception when insufficient_privilege then null; end;
 begin perform public.message_send(q,agent,gen_random_uuid(),owner_agent,null,'x',req||'-revoked','urgent');
  raise exception 'revoked_message_send';
 exception when insufficient_privilege then null; end;

 -- No new exposure.
 foreach f in array array['public.session_requirements()','public.session_inbox(uuid,text)',
   'public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text)','public.session_reply(uuid,text,uuid,text,text)'] loop
  if has_function_privilege('anon',f,'EXECUTE') then raise exception 'anon_exposed: %', f; end if;
 end loop;
 foreach f in array array['quorum_private.call(text,jsonb,text)','quorum_private.call_priority_core(text,jsonb,text)'] loop
  if has_function_privilege('authenticated',f,'EXECUTE') or has_function_privilege('anon',f,'EXECUTE') then
   raise exception 'private_exposed: %', f; end if;
 end loop;
end $test$;
