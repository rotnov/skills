-- Disposable regression: execute only inside BEGIN/ROLLBACK against the reviewed
-- schema plus invite_id_and_creator_gate migration. No models or external calls.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); target_id uuid:=gen_random_uuid(); outsider_id uuid:=gen_random_uuid();
 twin_id uuid:=gen_random_uuid(); email_id uuid:=gen_random_uuid();
 q uuid; q2 uuid; invitation uuid; legacy jsonb; result jsonb; first_result jsonb;
 owner_github text:='9000000000001'; target_github text:='9000000000002';
 request_prefix text:=gen_random_uuid()::text; device uuid;
begin
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(target_id,'{}'),
 (outsider_id,jsonb_build_object('provider_id',owner_github,'sub',owner_github,'user_name','rotnov')),
 (twin_id,'{}'),(email_id,jsonb_build_object('provider','github','provider_id',owner_github));
 insert into auth.identities(user_id,provider,provider_id,identity_data) values
 (owner_id,'github',owner_github,'{}'),(target_id,'github',target_github,'{}'),
 (outsider_id,'github','9000000000003','{}'),(twin_id,'github',target_github,'{}'),
 (email_id,'email',email_id::text,'{}');
 if exists(select 1 from quorum_private.instance_creators) then
  raise exception 'published_server_pregranted_creation'; end if;
 insert into quorum_private.instance_creators(github_id) values(owner_github);
 -- Authenticated strangers and edited user metadata never grant creation.
 perform set_config('request.jwt.claim.sub',outsider_id::text,true);
 begin perform public.quorum_create('outsider-'||request_prefix,request_prefix); raise exception 'outsider_created_quorum';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',email_id::text,true);
 begin perform public.quorum_create('email-'||request_prefix,request_prefix); raise exception 'email_created_quorum';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub','',true);
 begin perform public.quorum_create('anon-'||request_prefix,request_prefix); raise exception 'anonymous_created_quorum';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 first_result:=public.quorum_create('test-'||request_prefix,request_prefix);
 q:=(first_result->>'quorum_id')::uuid;
 if public.quorum_create('test-'||request_prefix,request_prefix)<>first_result then raise exception 'create_retry_changed'; end if;
 update quorum_private.instance_creators set revoked=true where github_id=owner_github;
 begin perform public.quorum_create('test-'||request_prefix,request_prefix); raise exception 'revoked_creator_replayed_cache';
 exception when insufficient_privilege then null; end;
 -- Losing instance creation leaves existing per-quorum owner rights intact.
 result:=public.invite_create_id(q,target_github,request_prefix);
 invitation:=(result->>'invite_id')::uuid;
 if result ? 'token' then raise exception 'id_invite_returned_secret'; end if;
 if public.invite_create_id(q,target_github,request_prefix)<>result then raise exception 'issue_retry_changed'; end if;
 begin perform public.quorum_create('invited-'||request_prefix,request_prefix); raise exception 'invitation_granted_creation';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_id(q,'123',request_prefix); raise exception 'issue_conflict_accepted';
 exception when raise_exception then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 -- Cross-quorum cache retries still require owner authority.
 insert into quorum_private.quorums(slug,owner_id) values('other-'||request_prefix,outsider_id) returning id into q2;
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q2,outsider_id,'owner');
 perform set_config('request.jwt.claim.sub',outsider_id::text,true);
 begin perform public.invite_create_id(q,target_github,request_prefix); raise exception 'foreign_owner_issued';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'public_id_outsider_accepted';
 exception when insufficient_privilege then null; end;
 begin perform public.quorum_create('foreign-owner-'||request_prefix,request_prefix); raise exception 'quorum_owner_gained_global_creation';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub','',true);
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'anonymous_accepted';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',target_id::text,true);
 first_result:=public.invite_accept_id(invitation,request_prefix);
 if (first_result->>'quorum_id')::uuid<>q then raise exception 'tenant_mismatch'; end if;
 if public.invite_accept_id(invitation,request_prefix)<>first_result then raise exception 'accept_retry_changed'; end if;
 if public.invite_accept_id(invitation,request_prefix||'-new')<>first_result then raise exception 'new_request_created_duplicate_membership'; end if;
 if (select count(*) from quorum_private.memberships where quorum_id=q and user_id=target_id)<>1 then raise exception 'duplicate_membership'; end if;
 if exists(select 1 from quorum_private.agents a where a.owner_id=target_id) or exists(select 1 from quorum_private.devices d where d.owner_id=target_id) then raise exception 'hidden_agent_registration'; end if;
 begin perform public.quorum_create('invited-'||request_prefix,request_prefix); raise exception 'invitation_granted_creation';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_create_id(q,'123',request_prefix); raise exception 'member_issued';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',twin_id::text,true);
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'consumed_by_different_user';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',target_id::text,true);
 update quorum_private.memberships set revoked=true where quorum_id=q and user_id=target_id;
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'revoked_member_replayed_cache';
 exception when insufficient_privilege then null; end;
 begin perform public.invite_accept_id(invitation,request_prefix||'-fresh'); raise exception 'consumed_invite_resurrected_member';
 exception when insufficient_privilege then null; end;
 update quorum_private.memberships set revoked=false where quorum_id=q and user_id=target_id;
 update quorum_private.id_invitations set expires_at=now()-interval '1 second' where id=invitation;
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'expired_cached_acceptance';
 exception when insufficient_privilege then null; end;
 update quorum_private.id_invitations set expires_at=now()+interval '1 hour' where id=invitation;
 perform set_config('request.jwt.claim.sub',outsider_id::text,true);
 begin perform public.invite_revoke_id(invitation,request_prefix); raise exception 'foreign_owner_revoked';
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.invite_revoke_id(invitation,request_prefix);
 perform set_config('request.jwt.claim.sub',target_id::text,true);
 begin perform public.invite_accept_id(invitation,request_prefix); raise exception 'revoked_cached_acceptance';
 exception when insufficient_privilege then null; end;
 -- Legacy invitation IDs never become public bearer-less invitations.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 legacy:=public.invite_create(q,target_github,request_prefix||'-legacy');
 perform set_config('request.jwt.claim.sub',target_id::text,true);
 begin perform public.invite_accept_id((legacy->>'invite_id')::uuid,request_prefix||'-legacy'); raise exception 'legacy_id_broadened';
 exception when insufficient_privilege then null; end;
 result:=public.invite_accept(legacy->>'token',request_prefix||'-legacy');
 if (result->>'quorum_id')::uuid<>q then raise exception 'legacy_token_broken'; end if;
 -- Provider-free native-session fixtures; registration is explicitly separate
 -- from acceptance, whose absence of devices/agents was asserted above.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 device:=(public.device_register('synthetic-owner',request_prefix)->>'device_id')::uuid;
 perform public.agent_join(q,device,'synthetic/project','owner-native','codex','synthetic-owner-session',request_prefix);
 perform set_config('request.jwt.claim.sub',target_id::text,true);
 device:=(public.device_register('synthetic-target',request_prefix)->>'device_id')::uuid;
 perform public.agent_join(q,device,'synthetic/project','target-native','claude','synthetic-target-session',request_prefix);
 if has_function_privilege('anon','public.invite_accept_id(uuid,text)','EXECUTE') then raise exception 'anon_rpc_granted'; end if;
 if has_function_privilege('authenticated','quorum_private.call_legacy_core(text,jsonb,text)','EXECUTE') then raise exception 'creator_gate_bypass_exposed'; end if;
 if has_table_privilege('authenticated','quorum_private.instance_creators','UPDATE') then raise exception 'creator_self_grant_possible'; end if;
 if has_table_privilege('authenticated','quorum_private.id_invitations','SELECT') then raise exception 'invitations_exposed'; end if;
end $test$;
