-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261030142000_project_knowledge.sql and 20261103075741_llm_wiki.sql.
-- No required-release change, table grant, policy, quorum permission or model launch. (#128)
--
-- A knowledge grant is keyed by the authenticated user (auth.uid(): a GitHub
-- login or one anonymous open-quorum login), never by an agent, session or
-- membership. Agent rebinds keep the agent and its login, so they keep the
-- grant; a new anonymous login is a new user and has none. This file adds one
-- space-level default role for quorum members:
--
-- 1. knowledge_admin gains the owner operation `default_role` with payload
--    `space_id` and `permission` (`reader`, `writer` or `none`). `none` removes
--    the default. Ownership, binding, mutation lock and request idempotency are
--    the reviewed core's: the same helpers in the same order.
-- 2. knowledge_scope falls back to that default only when the caller has no
--    explicit grant, so an explicit reader or writer grant always wins. The
--    default reaches only callers the reviewed checks already admit: a live
--    agent, device and non-revoked membership of a quorum attached to the space
--    for that agent's project. Revoked members, revoked agents, unattached
--    quorums or projects, owner-only operations (verify, audit, administration)
--    and Claude worker scopes are unchanged.
--
-- The reviewed knowledge_admin moves to a private function and is called
-- unchanged for every other operation. The reviewed knowledge_scope moves to a
-- private function kept for rollback; the new body is that body plus the
-- default lookup. Lock order is unchanged: space advisory lock (mutations),
-- binding rows FOR SHARE, space row (FOR UPDATE by administration, FOR SHARE by
-- scope), then grant and default rows. The default row is read FOR SHARE in a
-- later READ COMMITTED statement than the space row, so a caller that waited on
-- a default change sees the committed default.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The scope check whose body is copied below (142000; unchanged by 075741).
 'quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean)','5879b35c9d58f0885dd8838b3956077469922bc4b1f30216c8c5dfb883473e2c',
 -- The administration entry point wrapped below (075741).
 'public.knowledge_admin(uuid,text,text,jsonb,text)','29d16c63750d499c998f60525e0c79f0a4d371d2e4ccaabfbe5dbca7b1c840f1',
 -- The live agent, device and non-revoked membership check the default relies on.
 'quorum_private.knowledge_binding(uuid,text)','e42a7dfb2b37f4f4261a26bcc51556cb8bee34bf00bdf7209d9e413cda0145a6',
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- Helpers the new operation calls exactly as the reviewed core does.
 'quorum_private.knowledge_fields(jsonb,text[],text[])','f5c677c4f2e0dbfa21864e6fea6ebf41e1c10a9ae695b3fe25d253fdfd59d80b',
 'quorum_private.knowledge_cached(text,jsonb,text)','ab66b04077746fcb9bed8e3d6eb755399fd12aa0bf3ce086321271e42a873d53',
 'quorum_private.knowledge_save(text,jsonb,text,jsonb)','8679767b3e13ae5fc2c2dc259f04d404a11d373db7277148181e636f914174d9',
 'quorum_private.knowledge_mutation_lock(uuid)','21079f9e957ea7825d4ca763aa999bfa4a56142aea6b1a7e4ef96b2e5a038047',
 -- Every caller of knowledge_scope: its writer and owner-only arguments decide what a default admits.
 'public.knowledge_write(uuid,text,uuid,text,jsonb,text)','d20b67540dfc37083591f2df7a21e326d08758fca4a19dd3c5783b4ef9590f5d',
 'public.knowledge_index(uuid,text,uuid,uuid,integer,text,text,integer,jsonb,text)','7a768927edae4f831439daf5dd05c34a044a1a625357e68806b0d1bccd4f6b1a',
 'public.knowledge_search(uuid,text,uuid,text,integer,integer,text,jsonb)','2db3b7d3f85719813be974fe82f60c08e4c1b71a557e268d2c14145a44428ea1',
 'public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean,boolean)','03b4b19732a394c8582b1e525b1bd4a16228385dfb1a9518bb33dd35c2071e69',
 'public.knowledge_source_write(uuid,text,uuid,jsonb,text)','4a242c00a0791039b59bd000105330ee84f0bcf1bde93b7169c445cd772f3819',
 'public.knowledge_source_read(uuid,text,uuid,uuid,integer,integer,boolean)','6e81e61cd81d5c866f5aba210d88d0635d9e9ceb5ebd03c225782e6350a2e924',
 'public.knowledge_catalog(uuid,text,uuid,uuid,integer,integer)','e4d040099c4875cb948e8f2a7ea3f226ac7c5d6cd8c7bb775ec4e099d7ec02f8');
begin
 if to_regclass('quorum_private.knowledge_member_defaults') is not null
    or to_regprocedure('quorum_private.knowledge_scope_before_default_role(uuid,text,uuid,boolean,boolean)') is not null
    or to_regprocedure('quorum_private.knowledge_admin_before_default_role(uuid,text,text,jsonb,text)') is not null then
  raise exception 'knowledge_default_role_present'; end if;
 if to_regclass('quorum_private.knowledge_grants') is null
    or to_regprocedure('public.knowledge_catalog(uuid,text,uuid,uuid,integer,integer)') is null then
  raise exception 'knowledge_wiki_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- One optional default per space. No row means no default (`none`).
create table quorum_private.knowledge_member_defaults (
 space_id uuid primary key references quorum_private.knowledge_spaces(id) on delete cascade,
 permission text not null check(permission in ('reader','writer')),
 updated_at timestamptz not null default now()
);
alter table quorum_private.knowledge_member_defaults enable row level security;
revoke all on quorum_private.knowledge_member_defaults from public,anon,authenticated,service_role;

alter function quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean) rename to knowledge_scope_before_default_role;
revoke all on function quorum_private.knowledge_scope_before_default_role(uuid,text,uuid,boolean,boolean)
 from public,anon,authenticated,service_role;
alter function public.knowledge_admin(uuid,text,text,jsonb,text) set schema quorum_private;
alter function quorum_private.knowledge_admin(uuid,text,text,jsonb,text) rename to knowledge_admin_before_default_role;
revoke all on function quorum_private.knowledge_admin_before_default_role(uuid,text,text,jsonb,text)
 from public,anon,authenticated,service_role;

-- The reviewed 142000 body; the only added lines read the space default when no explicit grant exists.
create function quorum_private.knowledge_scope(a uuid,sid text,s uuid,writer boolean default false,owner_only boolean default false)
returns uuid language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; own uuid; permitted text;
begin
 q:=quorum_private.knowledge_binding(a,sid);
 select owner_id into own from quorum_private.knowledge_spaces where id=s for share;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 perform 1 from quorum_private.knowledge_attachments ka join quorum_private.agents ag
  on ag.id=a and ag.quorum_id=ka.quorum_id and ag.project_ref=ka.project_ref
 where ka.space_id=s and ka.quorum_id=q for share of ka;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 if own=auth.uid() then return q; end if;
 if owner_only then raise insufficient_privilege using message='knowledge_denied'; end if;
 select permission into permitted from quorum_private.knowledge_grants
  where space_id=s and user_id=auth.uid() for share;
 if not found then
  -- knowledge_binding proved a live agent, device and non-revoked membership of q,
  -- and q is attached for this agent's project: the default applies to that member.
  select permission into permitted from quorum_private.knowledge_member_defaults
   where space_id=s for share;
  if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 end if;
 if writer and permitted<>'writer' then
  raise insufficient_privilege using message='knowledge_denied'; end if;
 return q;
end $function$;

create function public.knowledge_admin(p_agent_id uuid,p_session_ref text,p_operation text,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare s uuid; own uuid; cached jsonb; result jsonb;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'payload',p_payload);
begin
 if p_operation is distinct from 'default_role' then
  return quorum_private.knowledge_admin_before_default_role(p_agent_id,p_session_ref,p_operation,p_payload,p_request_id);
 end if;
 -- The reviewed core's order: space mutation lock, binding, payload, owned space row, request journal.
 perform quorum_private.knowledge_mutation_lock((p_payload->>'space_id')::uuid);
 perform quorum_private.knowledge_binding(p_agent_id,p_session_ref);
 perform quorum_private.knowledge_fields(p_payload,array['space_id','permission'],array['space_id','permission']);
 if jsonb_typeof(p_payload->'permission')<>'string' or p_payload->>'permission' not in ('reader','writer','none') then
  raise exception 'knowledge_invalid_payload'; end if;
 s:=(p_payload->>'space_id')::uuid;
 select owner_id into own from quorum_private.knowledge_spaces where id=s for update;
 if not found or own<>auth.uid() then raise insufficient_privilege using message='knowledge_denied'; end if;
 cached:=quorum_private.knowledge_cached('admin:default_role',p,p_request_id);
 if cached is not null then return cached; end if;
 if p_payload->>'permission'='none' then
  delete from quorum_private.knowledge_member_defaults where space_id=s;
 else
  insert into quorum_private.knowledge_member_defaults(space_id,permission) values(s,p_payload->>'permission')
   on conflict(space_id) do update set permission=excluded.permission,updated_at=now();
 end if;
 result:=jsonb_build_object('space_id',s,'operation','default_role','permission',p_payload->>'permission');
 return quorum_private.knowledge_save('admin:default_role',p,p_request_id,result);
end $function$;

revoke all on function quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean)
 from public,anon,authenticated,service_role;
revoke all on function public.knowledge_admin(uuid,text,text,jsonb,text) from public,anon,authenticated,service_role;
grant execute on function public.knowledge_admin(uuid,text,text,jsonb,text) to authenticated;
