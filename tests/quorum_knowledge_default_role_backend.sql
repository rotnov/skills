-- Space default member role (#128). Synthetic users and spaces only; actual pgvector PostgreSQL.
create function pg_temp.dr_denied(statement text) returns void language plpgsql as $$
begin
 begin execute statement; exception when insufficient_privilege then return; end;
 raise exception 'expected_denial: %',statement;
end $$;
create function pg_temp.dr_failed(statement text,code text) returns void language plpgsql as $$
begin
 begin execute statement; exception when others then if sqlerrm=code then return; else raise; end if; end;
 raise exception 'expected_failure: %',code;
end $$;
do $default_role$
declare
 owner_user uuid:='11111111-1111-4111-8111-111111111128';
 member_user uuid:='22222222-2222-4222-8222-222222222128';
 anon_user uuid:='33333333-3333-4333-8333-333333333128';
 revoked_user uuid:='44444444-4444-4444-8444-444444444128';
 granted_user uuid:='55555555-5555-4555-8555-555555555128';
 later_user uuid:='66666666-6666-4666-8666-666666666128';
 q uuid:='77777777-7777-4777-8777-777777777128';
 unattached uuid:='77777777-7777-4777-8777-777777777129';
 s uuid:='88888888-8888-4888-8888-888888888128';
 owner_agent uuid:='a0000000-0000-4000-8000-000000000128';
 member_agent uuid:='b0000000-0000-4000-8000-000000000128';
 member_other_project uuid:='b1000000-0000-4000-8000-000000000128';
 member_unattached uuid:='b2000000-0000-4000-8000-000000000128';
 anon_agent uuid:='c0000000-0000-4000-8000-000000000128';
 revoked_agent uuid:='d0000000-0000-4000-8000-000000000128';
 granted_agent uuid:='e0000000-0000-4000-8000-000000000128';
 granted_second uuid:='e1000000-0000-4000-8000-000000000128';
 later_agent uuid:='f0000000-0000-4000-8000-000000000128';
 result jsonb; role_name text; fn text;
 owner_call text:=format('select public.knowledge_admin(%L,%L,%L,',owner_agent,'dr-owner','default_role');
begin
 insert into auth.users(id) values(owner_user),(member_user),(anon_user),(revoked_user),(granted_user),(later_user);
 insert into auth.identities(user_id,provider,provider_id) values(owner_user,'github','9128001'),(member_user,'github','9128002'),
  (revoked_user,'github','9128004'),(granted_user,'github','9128005'),(later_user,'github','9128006');
 insert into quorum_private.quorums(id,slug,owner_id,access) values(q,'default-role',owner_user,'open'),(unattached,'default-role-other',owner_user,'open');
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_user,'owner'),(q,member_user,'member'),
  (q,anon_user,'member'),(q,revoked_user,'member'),(q,granted_user,'member'),(unattached,owner_user,'owner'),(unattached,member_user,'member');
 insert into quorum_private.devices(id,owner_id,label) values(owner_agent,owner_user,'dr-owner'),(member_agent,member_user,'dr-member'),
  (anon_agent,anon_user,'dr-anon'),(revoked_agent,revoked_user,'dr-revoked'),(granted_agent,granted_user,'dr-granted');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (owner_agent,q,owner_agent,owner_user,'wiki','owner','codex','dr-owner'),
  (member_agent,q,member_agent,member_user,'wiki','member','claude','dr-member'),
  (member_other_project,q,member_agent,member_user,'elsewhere','member-elsewhere','claude','dr-member-elsewhere'),
  (member_unattached,unattached,member_agent,member_user,'wiki','member-unattached','claude','dr-member-unattached'),
  (anon_agent,q,anon_agent,anon_user,'wiki','anonymous','grok','dr-anon'),
  (revoked_agent,q,revoked_agent,revoked_user,'wiki','revoked','codex','dr-revoked'),
  (granted_agent,q,granted_agent,granted_user,'wiki','granted','codex','dr-granted');
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform public.knowledge_admin(owner_agent,'dr-owner','create',jsonb_build_object('space_id',s,'name','default role'),'dr-create');
 perform public.knowledge_admin(owner_agent,'dr-owner','attach',jsonb_build_object('space_id',s,'quorum_id',q,'project_ref','wiki'),'dr-attach');
 perform public.knowledge_admin(owner_agent,'dr-owner','grant',jsonb_build_object('space_id',s,'user_id',granted_user,'permission','writer'),'dr-grant');
 perform public.knowledge_write(owner_agent,'dr-owner',s,'propose',jsonb_build_object('entry_id','90000000-0000-4000-8000-000000000128',
  'type','fact','title','DEFAULT_ROLE_128','body','Seed page'),'dr-seed');

 -- Without a default, membership alone grants nothing: the reviewed behaviour.
 perform set_config('request.jwt.claim.sub',member_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',member_agent,'dr-member',s,'DEFAULT_ROLE_128'));
 -- Only the space owner sets a default; a member's attempt changes nothing.
 perform pg_temp.dr_denied(format('select public.knowledge_admin(%L,%L,%L,%L,%L)',member_agent,'dr-member','default_role',
  jsonb_build_object('space_id',s,'permission','writer'),'dr-member-default'));
 if exists(select 1 from quorum_private.knowledge_member_defaults where space_id=s) then raise exception 'member_set_default'; end if;

 -- Payload validation before any change.
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s,'permission','owner'),'dr-bad-1'),'knowledge_invalid_payload');
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s,'permission',true),'dr-bad-2'),'knowledge_invalid_payload');
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s),'dr-bad-3'),'knowledge_invalid_payload');
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s,'permission','writer','quorum_id',q),'dr-bad-4'),'knowledge_invalid_payload');
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s,'permission','writer'),''),'knowledge_request_id_required');
 if exists(select 1 from quorum_private.knowledge_member_defaults where space_id=s) then raise exception 'invalid_default_stored'; end if;

 -- Writer default; an identical retry replays, a changed retry conflicts.
 result:=public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','writer'),'dr-writer');
 if result<>jsonb_build_object('space_id',s,'operation','default_role','permission','writer') then raise exception 'default_result: %',result; end if;
 if result<>public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','writer'),'dr-writer') then
  raise exception 'default_retry_changed'; end if;
 perform pg_temp.dr_failed(owner_call||format('%L,%L)',jsonb_build_object('space_id',s,'permission','reader'),'dr-writer'),'knowledge_idempotency_conflict');
 -- Other administration still runs through the reviewed core.
 perform pg_temp.dr_failed(format('select public.knowledge_admin(%L,%L,%L,%L,%L)',owner_agent,'dr-owner','rename','{}','dr-rename'),'knowledge_invalid_operation');

 -- Current members, GitHub and anonymous, propose and read without an explicit grant.
 perform set_config('request.jwt.claim.sub',member_user::text,true);
 perform public.knowledge_write(member_agent,'dr-member',s,'propose',jsonb_build_object('entry_id','91000000-0000-4000-8000-000000000128',
  'type','fact','title','Member page','body','From the default'),'dr-member-propose');
 if jsonb_array_length(public.knowledge_search(member_agent,'dr-member',s,'DEFAULT_ROLE_128')->'results')<>1 then raise exception 'default_reader_search'; end if;
 -- Owner-only operations stay with the owner.
 perform pg_temp.dr_denied(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',member_agent,'dr-member',s,'verify',
  jsonb_build_object('entry_id','91000000-0000-4000-8000-000000000128','expected_revision',1,'owner_assertion',true,'evidence','x'),'dr-member-verify'));
 perform pg_temp.dr_denied(format('select public.knowledge_read(%L,%L,%L,%L,null,0,4096,true)',member_agent,'dr-member',s,'91000000-0000-4000-8000-000000000128'));
 perform pg_temp.dr_denied(format('select public.knowledge_admin(%L,%L,%L,%L,%L)',member_agent,'dr-member','grant',
  jsonb_build_object('space_id',s,'user_id',anon_user,'permission','writer'),'dr-member-grant'));
 -- The same member outside the attached quorum or project gets nothing.
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',member_other_project,'dr-member-elsewhere',s,'DEFAULT_ROLE_128'));
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',member_unattached,'dr-member-unattached',s,'DEFAULT_ROLE_128'));
 perform set_config('request.jwt.claim.sub',anon_user::text,true);
 perform public.knowledge_write(anon_agent,'dr-anon',s,'propose',jsonb_build_object('entry_id','92000000-0000-4000-8000-000000000128',
  'type','hypothesis','title','Anonymous page','body','Open member'),'dr-anon-propose');

 -- Revoked members and revoked agents never get the default.
 update quorum_private.memberships set revoked=true where quorum_id=q and user_id=revoked_user;
 perform set_config('request.jwt.claim.sub',revoked_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',revoked_agent,'dr-revoked',s,'DEFAULT_ROLE_128'));
 update quorum_private.memberships set revoked=false where quorum_id=q and user_id=revoked_user;
 perform public.knowledge_search(revoked_agent,'dr-revoked',s,'DEFAULT_ROLE_128');
 update quorum_private.agents set revoked=true where id=revoked_agent;
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',revoked_agent,'dr-revoked',s,'DEFAULT_ROLE_128'));

 -- A future joiner (accepted after the default was set) is covered without a grant.
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,later_user,'member');
 insert into quorum_private.devices(id,owner_id,label) values(later_agent,later_user,'dr-later');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
  values(later_agent,q,later_agent,later_user,'wiki','later','claude','dr-later');
 perform set_config('request.jwt.claim.sub',later_user::text,true);
 perform public.knowledge_write(later_agent,'dr-later',s,'propose',jsonb_build_object('entry_id','93000000-0000-4000-8000-000000000128',
  'type','fact','title','Later page','body','Future member'),'dr-later-propose');

 -- An explicit grant overrides the default: an explicit reader cannot write under a writer default.
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform public.knowledge_admin(owner_agent,'dr-owner','grant',jsonb_build_object('space_id',s,'user_id',member_user,'permission','reader'),'dr-member-reader');
 perform set_config('request.jwt.claim.sub',member_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',member_agent,'dr-member',s,'propose',
  jsonb_build_object('entry_id','94000000-0000-4000-8000-000000000128','type','fact','title','x','body','x'),'dr-member-reader-propose'));
 perform public.knowledge_search(member_agent,'dr-member',s,'DEFAULT_ROLE_128');
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform public.knowledge_admin(owner_agent,'dr-owner','revoke',jsonb_build_object('space_id',s,'user_id',member_user),'dr-member-unreader');

 -- Reader default: members read but cannot propose, index or retain sources; explicit writers still write.
 perform public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','reader'),'dr-reader');
 perform set_config('request.jwt.claim.sub',anon_user::text,true);
 perform public.knowledge_search(anon_agent,'dr-anon',s,'DEFAULT_ROLE_128');
 perform public.knowledge_catalog(anon_agent,'dr-anon',s);
 perform pg_temp.dr_denied(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',anon_agent,'dr-anon',s,'propose',
  jsonb_build_object('entry_id','95000000-0000-4000-8000-000000000128','type','fact','title','x','body','x'),'dr-anon-reader-propose'));
 perform pg_temp.dr_denied(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',anon_agent,'dr-anon',s,
  jsonb_build_object('source_id','96000000-0000-4000-8000-000000000128','reference','r','author','a','source_date','2026-01-01T00:00:00Z',
   'body','b','is_excerpt',false,'excerpt_scope',''),'dr-anon-reader-source'));
 perform set_config('request.jwt.claim.sub',granted_user::text,true);
 perform public.knowledge_write(granted_agent,'dr-granted',s,'propose',jsonb_build_object('entry_id','97000000-0000-4000-8000-000000000128',
  'type','fact','title','Granted page','body','Explicit writer'),'dr-granted-propose');

 -- An explicit grant follows the login, not the agent: a second agent of the same user (a new
 -- session, or a rebind that keeps the agent) keeps it. A new anonymous login is a new user.
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
  values(granted_second,q,granted_agent,granted_user,'wiki','granted-second','claude','dr-granted-second');
 perform public.knowledge_write(granted_second,'dr-granted-second',s,'propose',jsonb_build_object('entry_id','98000000-0000-4000-8000-000000000128',
  'type','fact','title','Second agent page','body','Same login'),'dr-granted-second-propose');

 -- `none` removes the default; explicit grants stay; repeating `none` is harmless.
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 result:=public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','none'),'dr-none');
 if result->>'permission'<>'none' or exists(select 1 from quorum_private.knowledge_member_defaults where space_id=s) then raise exception 'none_kept_default'; end if;
 perform public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','none'),'dr-none-again');
 perform set_config('request.jwt.claim.sub',anon_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',anon_agent,'dr-anon',s,'DEFAULT_ROLE_128'));
 perform set_config('request.jwt.claim.sub',later_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',later_agent,'dr-later',s,'DEFAULT_ROLE_128'));
 perform set_config('request.jwt.claim.sub',granted_user::text,true);
 perform public.knowledge_search(granted_agent,'dr-granted',s,'DEFAULT_ROLE_128');

 -- Detaching the quorum removes the default's reach too.
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform public.knowledge_admin(owner_agent,'dr-owner','default_role',jsonb_build_object('space_id',s,'permission','writer'),'dr-writer-again');
 perform public.knowledge_admin(owner_agent,'dr-owner','detach',jsonb_build_object('space_id',s,'quorum_id',q,'project_ref','wiki'),'dr-detach');
 perform set_config('request.jwt.claim.sub',later_user::text,true);
 perform pg_temp.dr_denied(format('select public.knowledge_search(%L,%L,%L,%L)',later_agent,'dr-later',s,'DEFAULT_ROLE_128'));
 perform set_config('request.jwt.claim.sub',owner_user::text,true);
 perform public.knowledge_admin(owner_agent,'dr-owner','attach',jsonb_build_object('space_id',s,'quorum_id',q,'project_ref','wiki'),'dr-reattach');

 -- Private table: RLS, no client privileges. Moved bodies are private; only the entry point is granted.
 if not (select relrowsecurity from pg_class where oid='quorum_private.knowledge_member_defaults'::regclass) then raise exception 'default_rls'; end if;
 foreach role_name in array array['anon','authenticated','service_role'] loop
  if has_table_privilege(role_name,'quorum_private.knowledge_member_defaults','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then
   raise exception 'default_table_grant: %',role_name; end if;
  foreach fn in array array['quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean)',
    'quorum_private.knowledge_scope_before_default_role(uuid,text,uuid,boolean,boolean)',
    'quorum_private.knowledge_admin_before_default_role(uuid,text,text,jsonb,text)'] loop
   if has_function_privilege(role_name,fn,'execute') then raise exception 'private_function_grant: % %',role_name,fn; end if;
  end loop;
  if has_function_privilege(role_name,'public.knowledge_admin(uuid,text,text,jsonb,text)','execute')<>(role_name='authenticated') then
   raise exception 'admin_entry_grant: %',role_name; end if;
 end loop;
 if has_function_privilege('public','public.knowledge_admin(uuid,text,text,jsonb,text)','execute') then raise exception 'admin_public_grant'; end if;
 if (select count(*) from pg_proc where proname='knowledge_admin' and pronamespace='public'::regnamespace)<>1 then raise exception 'admin_overloaded'; end if;
end $default_role$;
select 'default_role_contract_green';
