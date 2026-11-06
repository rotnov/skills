-- Disposable PostgreSQL only: the task authority project-ref bound matches join (#143).
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 sender uuid:=gen_random_uuid(); receiver uuid:=gen_random_uuid(); sid text:=gen_random_uuid()::text;
 conversation uuid:=gen_random_uuid(); message uuid:=gen_random_uuid(); result jsonb;
 joined_limit text:=repeat('p',500); denied boolean:=false;
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000143');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'authority-bound',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (sender,q,dev,owner_id,joined_limit,'lead','codex',gen_random_uuid()::text),
  (receiver,q,dev,owner_id,joined_limit,'member','claude',sid);
 insert into quorum_private.conversations(id,quorum_id,sender_id,recipient_id) values(conversation,q,sender,receiver);
 insert into quorum_private.messages(id,quorum_id,conversation_id,sender_id,recipient_id,sequence,body)
  values(message,q,conversation,sender,receiver,1,'task');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 -- The longest project ref join admits now reaches the authority decision.
 result:=public.session_task_authority(receiver,sid,message);
 if result->>'authorized'<>'true' or result->>'reason'<>'current_member' or result->>'project_ref'<>joined_limit then
  raise exception 'authority_bound_join_limit_denied: %',result->>'reason'; end if;

 -- A longer ref than join admits is still refused as a binding denial.
 update quorum_private.agents set project_ref=repeat('p',501) where id in (sender,receiver);
 begin
  perform public.session_task_authority(receiver,sid,message);
 exception when insufficient_privilege then
  denied:=sqlerrm='session_binding_denied';
 end;
 if not denied then raise exception 'authority_bound_overlong_ref_admitted'; end if;

 -- The reviewed body is private, keeps its old bound for rollback and is not callable by clients.
 if has_function_privilege('authenticated','quorum_private.session_task_authority_before_project_bound(uuid,text,uuid)','execute') then
  raise exception 'authority_bound_old_body_exposed'; end if;
 if not has_function_privilege('authenticated','public.session_task_authority(uuid,text,uuid)','execute')
    or has_function_privilege('anon','public.session_task_authority(uuid,text,uuid)','execute') then
  raise exception 'authority_bound_grants_wrong'; end if;
end $test$;
