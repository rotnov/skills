-- Disposable regression: execute only inside BEGIN/ROLLBACK after the
-- role_assignment_and_owner_retire migration. No models or external calls.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); alice uuid:=gen_random_uuid(); bob uuid:=gen_random_uuid();
 anon_id uuid:=gen_random_uuid(); q uuid; other_q uuid; slug text; inv uuid; bob_inv uuid; anon_inv uuid;
 result jsonb; first jsonb; a_device uuid; b_device uuid; c_device uuid; o_device uuid; first_agent uuid;
 second_agent uuid; third_agent uuid; b_agent uuid; c_agent uuid; owner_agent uuid; foreign_agent uuid;
 s1 text:=gen_random_uuid()::text; s2 text:=gen_random_uuid()::text; s3 text:=gen_random_uuid()::text;
 req text:=gen_random_uuid()::text; roster jsonb; entry jsonb; detail text; taker uuid;
begin
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(alice,'{}'),(bob,'{}'),(anon_id,'{}');
 insert into auth.identities(user_id,provider,provider_id,identity_data) values
 (owner_id,'github','9000000000101','{"user_name":"quorum-owner"}'),(alice,'github','9000000000102','{}'),
 (bob,'github','9000000000103','{"user_name":"bad name; drop"}');
 insert into quorum_private.instance_creators(github_id) values('9000000000101');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 slug:='roles-'||left(req,8);
 q:=(public.quorum_create(slug,req)->>'quorum_id')::uuid;
 other_q:=(public.quorum_create(slug||'-x',req||'-x')->>'quorum_id')::uuid;
 perform public.quorum_set_access(q,'open',req);
 o_device:=(public.device_register('owner',req)->>'device_id')::uuid;
 owner_agent:=(public.agent_join(q,o_device,slug,'coordinator','codex',gen_random_uuid()::text,req)->>'agent_id')::uuid;
 foreign_agent:=(public.agent_join(other_q,o_device,slug,'coordinator','codex',gen_random_uuid()::text,req||'-f')->>'agent_id')::uuid;

 -- Old entry points are replaced, never overloaded; grants stay authenticated-only.
 if to_regprocedure('public.invite_create_id(uuid,text,text)') is not null
    or to_regprocedure('public.invite_create_open(uuid,text)') is not null
    or to_regprocedure('public.agent_join(uuid,uuid,text,text,text,text,text)') is not null then
  raise exception 'old_signature_kept'; end if;
 if has_function_privilege('anon','public.agent_retire(uuid,uuid,text)','execute')
    or has_function_privilege('anon','public.quorum_agents(uuid)','execute')
    or has_function_privilege('anon','public.agent_join(uuid,uuid,text,text,text,text,text,boolean,uuid)','execute')
    or has_function_privilege('service_role','public.agent_retire(uuid,uuid,text)','execute')
    or not has_function_privilege('authenticated','public.agent_retire(uuid,uuid,text)','execute')
    or not has_function_privilege('authenticated','public.quorum_agents(uuid)','execute')
    or not has_function_privilege('authenticated','public.invite_create_open(uuid,text,text,text)','execute')
    or not has_function_privilege('authenticated','public.agent_join(uuid,uuid,text,text,text,text,text,boolean,uuid)','execute')
    or has_function_privilege('authenticated','quorum_private.call_role_core(text,jsonb,text)','execute')
    or has_table_privilege('authenticated','quorum_private.agent_supersessions','select') then
  raise exception 'role_privileges_wrong'; end if;

 -- Validation matches the client and rolls the issue back.
 begin perform public.invite_create_id(q,'9000000000102',req||'-bad','Bad Role'); raise exception 'invalid_role_issued';
 exception when raise_exception then if sqlerrm<>'invalid_role' then raise; end if; end;
 begin perform public.invite_create_id(q,'9000000000102',req||'-bad','reviewer','-rf'); raise exception 'invalid_project_issued';
 exception when raise_exception then if sqlerrm<>'invalid_project_ref' then raise; end if; end;
 begin perform public.invite_create_open(q,req||'-bad','reviewer','a b'); raise exception 'space_project_issued';
 exception when raise_exception then if sqlerrm<>'invalid_project_ref' then raise; end if; end;
 if exists(select 1 from quorum_private.requests where request_id=req||'-bad') then raise exception 'invalid_issue_kept'; end if;
 -- A role without a project defaults to the quorum slug, as on the client.
 first:=public.invite_create_id(q,'9000000000102',req,'reviewer');
 if first->'assigned'<>jsonb_build_object('role','reviewer','project_ref',slug) then raise exception 'default_project_wrong'; end if;
 inv:=(first->>'invite_id')::uuid;
 if (select assigned_role from quorum_private.id_invitations where id=inv)<>'reviewer' then raise exception 'role_not_stored'; end if;
 if public.invite_create_id(q,'9000000000102',req,'reviewer',slug)<>first then raise exception 'role_retry_changed'; end if;
 begin perform public.invite_create_id(q,'9000000000102',req,'writer'); raise exception 'role_replay_rewritten';
 exception when raise_exception then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 begin perform public.invite_create_id(q,'9000000000102',req); raise exception 'role_replay_dropped';
 exception when raise_exception then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 -- The old argument set still issues an unassigned invitation.
 result:=public.invite_create_id(q,'9000000000103',req||'-b');
 if result->'assigned'<>'null'::jsonb then raise exception 'unassigned_invite_assigned'; end if;
 bob_inv:=(result->>'invite_id')::uuid;
 result:=public.invite_create_open(q,req||'-open','tester','team/app:main');
 if result->'assigned'<>'{"role":"tester","project_ref":"team/app:main"}'::jsonb then raise exception 'open_assignment_missing'; end if;
 anon_inv:=(result->>'invite_id')::uuid;
 -- Only the owner issues.
 perform set_config('request.jwt.claim.sub',alice::text,true);
 begin perform public.invite_create_id(q,'9000000000102',req||'-a','reviewer'); raise exception 'outsider_issued';
 exception when insufficient_privilege then null; end;

 -- Acceptance records the assignment on the membership.
 result:=public.invite_accept_id(inv,req);
 if result->'assigned'<>first->'assigned' then raise exception 'accept_assignment_missing'; end if;
 if (select assigned_role||'|'||assigned_project_ref from quorum_private.memberships where quorum_id=q and user_id=alice)
    <>'reviewer|'||slug then raise exception 'membership_assignment_missing'; end if;
 if public.invite_accept_id(inv,req)<>result then raise exception 'accept_retry_changed'; end if;
 a_device:=(public.device_register('alice',req)->>'device_id')::uuid;
 begin perform public.agent_join(q,a_device,slug,'writer','claude',s1,req||'-w'); raise exception 'assigned_role_ignored';
 exception when raise_exception then
  get stacked diagnostics detail=pg_exception_detail;
  if sqlerrm<>'role_assignment_mismatch' or detail::jsonb<>first->'assigned' then raise; end if; end;
 begin perform public.agent_join(q,a_device,'elsewhere','reviewer','claude',s1,req||'-w'); raise exception 'assigned_project_ignored';
 exception when raise_exception then if sqlerrm<>'role_assignment_mismatch' then raise; end if; end;
 -- Non-members learn nothing about holders.
 begin perform public.agent_join(other_q,a_device,slug,'coordinator','claude',s1,req||'-o'); raise exception 'outsider_joined';
 exception when insufficient_privilege then null; end;
 result:=public.agent_join(q,a_device,slug,'reviewer','claude',s1,req);
 first_agent:=(result->>'agent_id')::uuid;
 if result->'superseded'<>'[]'::jsonb then raise exception 'fresh_join_superseded'; end if;
 -- Idempotent retry and the same device/session keep the original agent.
 if public.agent_join(q,a_device,slug,'reviewer','claude',s1,req)<>result then raise exception 'join_retry_changed'; end if;
 if (public.agent_join(q,a_device,slug,'reviewer','claude',s1,req||'-again')->>'agent_id')::uuid<>first_agent then
  raise exception 'same_session_duplicated'; end if;
 begin perform public.agent_join(q,a_device,slug,'reviewer','codex',s1,req||'-engine'); raise exception 'binding_changed';
 exception when raise_exception then if sqlerrm<>'binding_conflict' then raise; end if; end;

 -- Another own session under the same role is refused, naming the holder, unless superseded.
 begin perform public.agent_join(q,a_device,slug,'reviewer','claude',s2,req||'-2'); raise exception 'own_duplicate_joined';
 exception when raise_exception then
  get stacked diagnostics detail=pg_exception_detail;
  if sqlerrm<>'role_held_by_own_agent' or detail<>first_agent::text then raise; end if; end;
 if exists(select 1 from quorum_private.agents where session_ref=s2) then raise exception 'refused_join_kept'; end if;
 result:=public.agent_join(q,a_device,slug,'reviewer','claude',s2,req||'-2',true);
 second_agent:=(result->>'agent_id')::uuid;
 if result->'superseded'<>jsonb_build_array(first_agent) then raise exception 'supersede_not_reported'; end if;
 if not (select revoked from quorum_private.agents where id=first_agent) then raise exception 'superseded_still_live'; end if;
 if quorum_private.member(q,alice)<>'member' then raise exception 'supersede_removed_membership'; end if;
 if public.agent_join(q,a_device,slug,'reviewer','claude',s2,req||'-2',true)<>result then raise exception 'supersede_retry_changed'; end if;
 if public.agent_join(q,a_device,slug,'reviewer','claude',s2,req||'-2')<>result then raise exception 'supersede_flag_replay_changed'; end if;
 if (public.resolve_role(q,'reviewer',slug)->>'agent_id')::uuid<>second_agent then raise exception 'role_not_single'; end if;
 -- A superseded agent is revoked exactly as by agent_leave.
 begin perform public.agent_status(first_agent); raise exception 'superseded_agent_live';
 exception when insufficient_privilege then null; end;
 -- A named predecessor (an adopting worker's parent) may coexist; nothing else may.
 result:=public.agent_join(q,a_device,slug,'reviewer','claude',s3,req||'-3',false,second_agent);
 third_agent:=(result->>'agent_id')::uuid;
 if (select revoked from quorum_private.agents where id=second_agent) then raise exception 'kept_agent_revoked'; end if;
 begin perform public.agent_join(q,a_device,slug,'reviewer','claude',gen_random_uuid()::text,req||'-4',false,second_agent);
  raise exception 'keep_admitted_third';
 exception when raise_exception then if sqlerrm<>'role_held_by_own_agent' then raise; end if; end;
 perform public.agent_leave(second_agent,req||'-leave');

 -- Another identity cannot take a held role; unassigned members keep free roles as before.
 perform set_config('request.jwt.claim.sub',bob::text,true);
 if public.invite_accept_id(bob_inv,req)->'assigned'<>'null'::jsonb then raise exception 'unassigned_accept_assigned'; end if;
 b_device:=(public.device_register('bob',req)->>'device_id')::uuid;
 begin perform public.agent_join(q,b_device,slug,'reviewer','codex',s1,req||'-b'); raise exception 'other_identity_took_role';
 exception when raise_exception then
  get stacked diagnostics detail=pg_exception_detail;
  if sqlerrm<>'role_held_by_other_identity' or detail<>'' then raise; end if; end;
 begin perform public.agent_join(q,b_device,slug,'reviewer','codex',s1,req||'-b',true); raise exception 'supersede_crossed_identity';
 exception when raise_exception then if sqlerrm<>'role_held_by_other_identity' then raise; end if; end;
 b_agent:=(public.agent_join(q,b_device,slug,'builder','codex',s1,req)->>'agent_id')::uuid;
 if (select assigned_role from quorum_private.memberships where quorum_id=q and user_id=bob) is not null then
  raise exception 'unassigned_member_assigned'; end if;

 -- Open invitations carry the assignment for anonymous members too.
 perform set_config('request.jwt.claim.sub',anon_id::text,true);
 if public.invite_accept_id(anon_inv,req)->'assigned'<>'{"role":"tester","project_ref":"team/app:main"}'::jsonb then
  raise exception 'open_accept_assignment_missing'; end if;
 c_device:=(public.device_register('anon',req)->>'device_id')::uuid;
 begin perform public.agent_join(q,c_device,slug,'tester','grok',s1,req||'-c'); raise exception 'open_assignment_ignored';
 exception when raise_exception then if sqlerrm<>'role_assignment_mismatch' then raise; end if; end;
 c_agent:=(public.agent_join(q,c_device,'team/app:main','tester','grok',s1,req)->>'agent_id')::uuid;

 -- A later role-less invitation for the same member clears the binding.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 inv:=(public.invite_create_id(q,'9000000000102',req||'-free')->>'invite_id')::uuid;
 perform set_config('request.jwt.claim.sub',alice::text,true);
 if public.invite_accept_id(inv,req||'-free')->'assigned'<>'null'::jsonb then raise exception 'assignment_not_cleared'; end if;
 perform public.agent_join(q,a_device,slug,'free-role','claude',gen_random_uuid()::text,req||'-free');

 -- Owner retire: owner only, idempotent, membership kept.
 begin perform public.agent_retire(q,b_agent,req); raise exception 'member_retired_agent';
 exception when insufficient_privilege then if sqlerrm<>'owner_required' then raise; end if; end;
 begin perform public.quorum_agents(q); raise exception 'member_read_roster';
 exception when insufficient_privilege then if sqlerrm<>'owner_required' then raise; end if; end;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 begin perform public.agent_retire(q,b_agent,null); raise exception 'retire_without_request_id';
 exception when raise_exception then if sqlerrm<>'request_id_required' then raise; end if; end;
 begin perform public.agent_retire(q,foreign_agent,req||'-foreign'); raise exception 'foreign_agent_retired';
 exception when insufficient_privilege then if sqlerrm<>'agent_denied' then raise; end if; end;
 result:=public.agent_retire(q,b_agent,req);
 if result<>jsonb_build_object('retired',true,'agent_id',b_agent,'already_revoked',false) then raise exception 'retire_result_wrong'; end if;
 if not (select revoked from quorum_private.agents where id=b_agent) then raise exception 'retire_not_revoked'; end if;
 if public.agent_retire(q,b_agent,req)<>result then raise exception 'retire_retry_changed'; end if;
 begin perform public.agent_retire(q,c_agent,req); raise exception 'retire_replay_rewritten';
 exception when raise_exception then if sqlerrm<>'idempotency_conflict' then raise; end if; end;
 if public.agent_retire(q,b_agent,req||'-again')->>'already_revoked'<>'true' then raise exception 'retire_repeat_wrong'; end if;
 if quorum_private.member(q,bob)<>'member' then raise exception 'retire_removed_membership'; end if;
 perform set_config('request.jwt.claim.sub',bob::text,true);
 begin perform public.agent_status(b_agent); raise exception 'retired_agent_live';
 exception when insufficient_privilege then if sqlerrm<>'agent_denied' then raise; end if; end;
 -- The retired role is free again for its member.
 perform public.agent_join(q,b_device,slug,'builder','codex',s2,req||'-rejoin');

 -- Reactivating a revoked membership retires its stale agents instead of reviving
 -- them next to the identity that claimed their role meanwhile.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.member_revoke(q,(select id from quorum_private.memberships where quorum_id=q and user_id=alice),req||'-rv');
 if not exists(select 1 from quorum_private.agents where id=third_agent and not revoked) then
  raise exception 'revoke_fixture_changed'; end if;
 perform set_config('request.jwt.claim.sub',bob::text,true);
 taker:=(public.agent_join(q,b_device,slug,'reviewer','codex',s3,req||'-take')->>'agent_id')::uuid;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 inv:=(public.invite_create_id(q,'9000000000102',req||'-back')->>'invite_id')::uuid;
 perform set_config('request.jwt.claim.sub',alice::text,true);
 -- A refused acceptance changes nothing.
 update quorum_private.id_invitations set expires_at=now()-interval '1 second' where id=inv;
 begin perform public.invite_accept_id(inv,req||'-back'); raise exception 'expired_reactivation';
 exception when insufficient_privilege then null; end;
 if not exists(select 1 from quorum_private.agents where id=third_agent and not revoked) then
  raise exception 'refused_accept_retired_agents'; end if;
 update quorum_private.id_invitations set expires_at=now()+interval '1 hour' where id=inv;
 perform public.invite_accept_id(inv,req||'-back');
 if quorum_private.member(q,alice)<>'member' then raise exception 'membership_not_reactivated'; end if;
 if exists(select 1 from quorum_private.agents a where a.quorum_id=q and a.owner_id=alice and not a.revoked) then
  raise exception 'reactivation_revived_agents'; end if;
 if (public.resolve_role(q,'reviewer',slug)->>'agent_id')::uuid<>taker then raise exception 'reactivation_made_role_ambiguous'; end if;
 -- The legacy token acceptance reactivates the same way.
 first_agent:=(public.agent_join(q,a_device,slug,'token-role','claude',gen_random_uuid()::text,req||'-tok')->>'agent_id')::uuid;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 perform public.member_revoke(q,(select id from quorum_private.memberships where quorum_id=q and user_id=alice),req||'-rv2');
 detail:=public.invite_create(q,'9000000000102',req||'-tok')->>'token';
 perform set_config('request.jwt.claim.sub',alice::text,true);
 perform public.invite_accept(detail,req||'-tok');
 if quorum_private.member(q,alice)<>'member' or not (select revoked from quorum_private.agents where id=first_agent) then
  raise exception 'token_reactivation_revived_agent'; end if;
 -- An ordinary acceptance by a live member retires nothing.
 second_agent:=(public.agent_join(q,a_device,slug,'kept-role','claude',gen_random_uuid()::text,req||'-kept')->>'agent_id')::uuid;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 inv:=(public.invite_create_id(q,'9000000000102',req||'-live')->>'invite_id')::uuid;
 perform set_config('request.jwt.claim.sub',alice::text,true);
 perform public.invite_accept_id(inv,req||'-live');
 if (select revoked from quorum_private.agents where id=second_agent) then raise exception 'live_accept_retired_agent'; end if;

 -- Owner roster: binding metadata only.
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 roster:=public.quorum_agents(q);
 if jsonb_array_length(roster)<>(select count(*) from quorum_private.agents where quorum_id=q) then raise exception 'roster_incomplete'; end if;
 for entry in select value from jsonb_array_elements(roster) loop
  if (select array_agg(k order by k) from jsonb_object_keys(entry) k)
     <>array['agent_id','engine','last_seen','live','member_role','own','owner_label','project_ref','revoked','role'] then
   raise exception 'roster_fields_wrong: %',entry; end if;
 end loop;
 select value into entry from jsonb_array_elements(roster) where value->>'agent_id'=owner_agent::text;
 if entry->>'owner_label'<>'quorum-owner' or not (entry->>'own')::boolean or entry->>'member_role'<>'owner'
    or not (entry->>'live')::boolean then raise exception 'owner_entry_wrong: %',entry; end if;
 select value into entry from jsonb_array_elements(roster) where value->>'agent_id'=b_agent::text;
 if entry->>'owner_label'<>'github:9000000000103' or (entry->>'live')::boolean or not (entry->>'revoked')::boolean then
  raise exception 'retired_entry_wrong: %',entry; end if;
 select value into entry from jsonb_array_elements(roster) where value->>'agent_id'=c_agent::text;
 if entry->>'owner_label' !~ '^anonymous:[0-9a-f]{8}$' then raise exception 'anonymous_label_wrong: %',entry; end if;
 if jsonb_array_length(public.quorum_agents(other_q))<>1 then raise exception 'roster_crossed_quorum'; end if;
 perform set_config('request.jwt.claim.sub','',true);
 begin perform public.quorum_agents(q); raise exception 'anonymous_read_roster';
 exception when insufficient_privilege then null; end;
end $test$;
