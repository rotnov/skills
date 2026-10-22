-- Disposable regression: execute only inside BEGIN/ROLLBACK after the
-- open_access_quorums migration. No models or external calls.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); anon_id uuid:=gen_random_uuid(); other_id uuid:=gen_random_uuid();
 github_id uuid:=gen_random_uuid(); q uuid; invitation uuid; result jsonb; first_result jsonb;
 device uuid; agent uuid; sid text:=gen_random_uuid()::text; owner_agent uuid; owner_device uuid;
 req text:=gen_random_uuid()::text;
begin
 -- Anonymous Supabase users have no identity rows.
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(anon_id,'{}'),(other_id,'{}'),(github_id,'{}');
 insert into auth.identities(user_id,provider,provider_id,identity_data) values
 (owner_id,'github','9000000000011','{}'),(github_id,'github','9000000000012','{}');
 insert into quorum_private.instance_creators(github_id) values('9000000000011');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 q:=(public.quorum_create('open-'||req,req)->>'quorum_id')::uuid;
 if (select access from quorum_private.quorums where id=q)<>'github' then raise exception 'default_not_github'; end if;
 begin perform public.invite_create_open(q,req); raise exception 'github_quorum_issued_open_invite';
 exception when insufficient_privilege then null; end;
 begin perform public.quorum_set_access(q,'everyone',req); raise exception 'invalid_access_accepted';
 exception when raise_exception then if sqlerrm<>'invalid_access' then raise; end if; end;
 result:=public.quorum_set_access(q,'open',req);
 if result->>'access'<>'open' then raise exception 'access_not_set'; end if;
 result:=public.invite_create_open(q,req);
 invitation:=(result->>'invite_id')::uuid;
 if public.invite_create_open(q,req)<>result then raise exception 'open_issue_retry_changed'; end if;
 if (result->>'expires_at')::timestamptz not between now()+interval '14 minutes' and now()+interval '16 minutes' then
  raise exception 'open_invite_not_15_minutes'; end if;
 if (select target_github_id from quorum_private.id_invitations where id=invitation) is not null then
  raise exception 'open_invite_has_target'; end if;
 owner_device:=(public.device_register('owner',req)->>'device_id')::uuid;
 owner_agent:=(public.agent_join(q,owner_device,'p','coordinator','codex',gen_random_uuid()::text,req)->>'agent_id')::uuid;
 -- Without membership a non-GitHub user can do nothing but accept an open invitation.
 perform set_config('request.jwt.claim.sub','',true);
 begin perform public.invite_accept_id(invitation,req); raise exception 'unauthenticated_accepted';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.device_register('anon',req); raise exception 'unadmitted_anonymous_registered';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_open(q,req||'-anon'); raise exception 'anonymous_issued_open_invite';
 exception when insufficient_privilege then null; end;
 first_result:=public.invite_accept_id(invitation,req);
 if (first_result->>'quorum_id')::uuid<>q then raise exception 'open_tenant_mismatch'; end if;
 if public.invite_accept_id(invitation,req)<>first_result then raise exception 'open_accept_retry_changed'; end if;
 if public.invite_accept_id(invitation,req||'-new')<>first_result then raise exception 'open_accept_duplicated'; end if;
 if quorum_private.member(q,anon_id)<>'member' then raise exception 'anonymous_not_member'; end if;
 -- Admitted members use the ordinary member operations, never owner ones.
 device:=(public.device_register('anon',req)->>'device_id')::uuid;
 agent:=(public.agent_join(q,device,'p','claude-linux-test','claude',sid,req)->>'agent_id')::uuid;
 perform public.message_send(q,agent,gen_random_uuid(),owner_agent,null,'hello',req);
 if jsonb_array_length(public.session_inbox(agent,sid))<>0 then raise exception 'unexpected_inbox'; end if;
 if to_regprocedure('quorum_private.receive_wakeup(text)') is not null
    and not quorum_private.receive_wakeup('quorum:agent:'||agent) then raise exception 'anonymous_wake_denied'; end if;
 begin perform public.quorum_create('anon-'||req,req||'-create'); raise exception 'anonymous_created_quorum';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_id(q,'9000000000013',req||'-id'); raise exception 'anonymous_issued_id_invite';
 exception when insufficient_privilege then null; end;
 begin perform public.quorum_set_access(q,'open',req); raise exception 'anonymous_set_access';
 exception when insufficient_privilege then null; end;
 -- Single use: another user cannot reuse it, even before expiry.
 perform set_config('request.jwt.claim.sub',other_id::text,true);
 begin perform public.invite_accept_id(invitation,req); raise exception 'open_invite_reused';
 exception when insufficient_privilege then null; end;
 -- Expired and revoked open invitations are denied; GitHub users may use valid ones.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 invitation:=(public.invite_create_open(q,req||'-expired')->>'invite_id')::uuid;
 update quorum_private.id_invitations set expires_at=now()-interval '1 second' where id=invitation;
 perform set_config('request.jwt.claim.sub',other_id::text,true);
 begin perform public.invite_accept_id(invitation,req); raise exception 'expired_open_invite_accepted';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 invitation:=(public.invite_create_open(q,req||'-revoked')->>'invite_id')::uuid;
 perform public.invite_revoke_id(invitation,req||'-revoke');
 perform set_config('request.jwt.claim.sub',other_id::text,true);
 begin perform public.invite_accept_id(invitation,req); raise exception 'revoked_open_invite_accepted';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 invitation:=(public.invite_create_open(q,req||'-github')->>'invite_id')::uuid;
 perform set_config('request.jwt.claim.sub',github_id::text,true);
 perform public.invite_accept_id(invitation,req);
 -- Closing the quorum removes non-GitHub members and unused open invitations.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 invitation:=(public.invite_create_open(q,req||'-closed')->>'invite_id')::uuid;
 perform public.quorum_set_access(q,'github',req);
 if not (select revoked from quorum_private.memberships where quorum_id=q and user_id=anon_id) then
  raise exception 'anonymous_survived_close'; end if;
 if (select revoked from quorum_private.memberships where quorum_id=q and user_id=github_id) then
  raise exception 'github_member_revoked'; end if;
 if quorum_private.member(q,owner_id)<>'owner' then raise exception 'owner_lost'; end if;
 perform set_config('request.jwt.claim.sub',other_id::text,true);
 begin perform public.invite_accept_id(invitation,req); raise exception 'closed_quorum_open_invite_accepted';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 begin perform public.device_register('anon-after-close',req||'-after'); raise exception 'closed_anonymous_registered';
 exception when insufficient_privilege then null; end;
 begin perform public.session_inbox(agent,sid); raise exception 'closed_anonymous_inbox';
 exception when insufficient_privilege then null; end;
 if to_regprocedure('quorum_private.receive_wakeup(text)') is not null
    and quorum_private.receive_wakeup('quorum:agent:'||agent) then raise exception 'closed_anonymous_wake'; end if;
 begin perform public.invite_accept_id((first_result->>'membership_id')::uuid,req||'-x'); raise exception 'unknown_invite_accepted';
 exception when insufficient_privilege then null; end;
 if has_function_privilege('anon','public.invite_create_open(uuid,text)','EXECUTE')
    or has_function_privilege('anon','public.quorum_set_access(uuid,text,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.call_invite_core(text,jsonb,text)','EXECUTE')
    or has_function_privilege('authenticated','quorum_private.verified_actor(uuid)','EXECUTE') then
  raise exception 'open_access_grants_too_broad'; end if;
end $test$;
