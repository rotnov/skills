-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261103190000_open_invite_ttl.sql and 20261030140000_owner_update_maintenance.sql.
-- No required-release change, table grant, policy or model launch. (#105)
--
-- 1. Role on the invitation. GitHub and open invitations may carry an assigned
--    role and shared project reference. Acceptance records them on the
--    membership; agent_join from that membership then refuses any other role or
--    project with role_assignment_mismatch. Invitations without an assignment,
--    and memberships accepted from them, behave as before.
-- 2. One live agent per identity, role and project. agent_join refuses a role
--    and project held by another identity's live agent
--    (role_held_by_other_identity) or by the caller's own live agent on another
--    device or session (role_held_by_own_agent). p_supersede retires the caller's
--    own holders in the same transaction; p_keep_agent_id names one own holder
--    that may coexist (an adopting worker's parent, until its owner retires it).
--    A same device/session reconnect and an idempotent retry keep the reviewed
--    core's answer: the original agent or binding_conflict.
-- 3. Owner retire. agent_retire lets the quorum owner revoke any agent in the
--    quorum exactly like agent_leave, keeping the membership; quorum_agents is
--    the owner's agent roster without tokens or credentials.
--
-- The reviewed dispatcher moves to call_role_core unchanged and keeps owning
-- identity, membership, device, invitation, close serialization and idempotency
-- checks. Existing entry points gain only trailing defaulted arguments, so
-- current clients call them unchanged. Role exclusivity reads holders under an
-- advisory lock and therefore requires READ COMMITTED, like open access.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- Close-serialization wrapper (180000), as 140000 pinned it.
 'quorum_private.call(text,jsonb,text)','ca718afbcda8682ade19ea6b246ccc2f1bfd5523eb7d1076cf4e9583e24f3ab7',
 -- Entry points replaced below with trailing defaulted arguments.
 'public.invite_create_id(uuid,text,text)','3879f923b48fda8b9c67a76b5774d6cc132a8f4736ad451c6811b2de0c3d436b',
 'public.invite_create_open(uuid,text)','b3f06f990f5523723b27eb36a585ffca801087239e6784166e940ad96ac47349',
 'public.agent_join(uuid,uuid,text,text,text,text,text)','732ca06801a141867e97e14e678df538a55eb3b346ec83ae85aed75c0832551d',
 -- Unchanged entry point whose acceptance the wrapper records on the membership.
 'public.invite_accept_id(uuid,text)','d61c6972108e7feafa0a77ab32c436c8ec4971cacedf2c219fd4f7d6dc9e0fa4',
 -- Holder semantics match the reviewed role resolution.
 'public.resolve_role(uuid,text,text)','c75b3982fce17a7c32caa7d81f2e74d4bc3aa903b88a54f939ee6849ab68aea1',
 -- Ownership and verified-actor checks the new RPCs rely on.
 'quorum_private.member(uuid,uuid)','155cf2dcc2b2d61c5ff55a7533b13eae931b9e077420f3aaf2408b7a22ca7435',
 'quorum_private.verified_actor(uuid)','f20291c082af61a232754c29480b65877d7994c177c06ce079b655af6b0f832b',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.call_role_core(text,jsonb,text)') is not null then
  raise exception 'role_assignment_present'; end if;
 -- 140000 pins the dispatcher this file wraps, so it must already be applied.
 if to_regprocedure('quorum_private.update_session_locked(uuid,text,uuid)') is null then
  raise exception 'owner_update_maintenance_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
 alter function quorum_private.call(text,jsonb,text) rename to call_role_core;
end $migration$;
revoke all on function quorum_private.call_role_core(text,jsonb,text) from public,anon,authenticated,service_role;

-- The same role and project-reference rules as the client. The project charset
-- is inert inside single quotes in POSIX shells and PowerShell; no leading '-'.
-- PostgreSQL bounds regex repetition at 255, so the 500-character limit is a length check.
alter table quorum_private.id_invitations
 add column assigned_role text check (assigned_role ~ '^[a-z][a-z0-9-]{0,47}$'),
 add column assigned_project_ref text check (length(assigned_project_ref)<=500
  and assigned_project_ref ~ '^[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]*$'),
 add constraint id_invitations_assignment_check check (assigned_role is null or assigned_project_ref is not null);
alter table quorum_private.memberships
 add column assigned_role text check (assigned_role ~ '^[a-z][a-z0-9-]{0,47}$'),
 add column assigned_project_ref text check (length(assigned_project_ref)<=500
  and assigned_project_ref ~ '^[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]*$'),
 add constraint memberships_assignment_check check (assigned_role is null or assigned_project_ref is not null);

-- Which own agents a join retired, so an idempotent retry reports them again.
create table quorum_private.agent_supersessions (
 superseded_id uuid primary key references quorum_private.agents(id),
 agent_id uuid not null references quorum_private.agents(id),
 created_at timestamptz not null default now()
);
alter table quorum_private.agent_supersessions enable row level security;
revoke all on quorum_private.agent_supersessions from public,anon,authenticated,service_role;

create function quorum_private.call(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare
 who uuid:=auth.uid(); q uuid; result jsonb; previous record; replayed boolean; core jsonb;
 want_role text; want_project text; supersede boolean; keep uuid; own uuid[];
 invitation quorum_private.id_invitations%rowtype; membership quorum_private.memberships%rowtype;
 agent quorum_private.agents%rowtype; fingerprint text;
begin
 if op not in ('invite_create_id','invite_create_open','invite_accept_id','agent_join','agent_retire') then
  return quorum_private.call_role_core(op,p,req);
 end if;
 -- Requests the core rejects before reading a row keep its errors and precedence.
 if who is null or p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072
    or req is null or length(req) not between 1 and 128 then
  if op<>'agent_retire' then return quorum_private.call_role_core(op,p,req); end if;
  if who is null then raise insufficient_privilege using message='auth_required'; end if;
  if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072 then raise exception 'invalid_payload'; end if;
  raise exception 'request_id_required';
 end if;
 replayed:=exists(select 1 from quorum_private.requests r where r.actor=who and r.operation=op and r.request_id=req);

 if op in ('invite_create_id','invite_create_open') then
  -- The core authorizes and issues first; any refusal below rolls the issue back.
  result:=quorum_private.call_role_core(op,p-'role'-'project_ref',req);
  want_role:=p->>'role'; want_project:=p->>'project_ref';
  if want_role is not null and want_role !~ '^[a-z][a-z0-9-]{0,47}$' then raise exception 'invalid_role'; end if;
  if want_project is not null and (length(want_project)>500
     or want_project !~ '^[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]*$') then
   raise exception 'invalid_project_ref'; end if;
  if want_role is not null and want_project is null then
   select slug into want_project from quorum_private.quorums where id=(p->>'quorum_id')::uuid; end if;
  select * into invitation from quorum_private.id_invitations where id=(result->>'invite_id')::uuid;
  -- The assignment is outside the core's fingerprint, so a replay must agree
  -- with the stored invitation instead of rewriting it.
  if not replayed and invitation.assigned_role is null and invitation.assigned_project_ref is null then
   update quorum_private.id_invitations set assigned_role=want_role,assigned_project_ref=want_project
    where id=invitation.id;
  elsif invitation.assigned_role is distinct from want_role
     or invitation.assigned_project_ref is distinct from want_project then
   raise exception 'idempotency_conflict';
  end if;
  return result||jsonb_build_object('assigned',case when want_project is null then null
   else jsonb_build_object('role',want_role,'project_ref',want_project) end);

 elsif op='invite_accept_id' then
  -- Read before the core consumes it; the core relocks and rechecks the row.
  select * into invitation from quorum_private.id_invitations where id=(p->>'invite_id')::uuid;
  result:=quorum_private.call_role_core(op,p,req);
  -- Only a first acceptance sets the binding; a role-less invitation clears an older one.
  if not replayed and invitation.id is not null and invitation.accepted_by is null then
   update quorum_private.memberships set assigned_role=invitation.assigned_role,
    assigned_project_ref=invitation.assigned_project_ref
    where id=(result->>'membership_id')::uuid and user_id=who and role='member';
  end if;
  select * into membership from quorum_private.memberships where id=(result->>'membership_id')::uuid;
  return result||jsonb_build_object('assigned',case when membership.assigned_project_ref is null then null
   else jsonb_build_object('role',membership.assigned_role,'project_ref',membership.assigned_project_ref) end);

 elsif op='agent_join' then
  q:=(p->>'quorum_id')::uuid;
  supersede:=coalesce((p->>'supersede')::boolean,false);
  keep:=(p->>'keep_agent_id')::uuid;
  core:=p-'supersede'-'keep_agent_id';
  select * into membership from quorum_private.memberships m where m.quorum_id=q and m.user_id=who and not m.revoked;
  -- Non-members never learn who holds a role: the core refuses them.
  if not found or not quorum_private.verified_actor(who) then
   return quorum_private.call_role_core(op,core,req); end if;
  -- One key per quorum, project and role serializes concurrent claims; it is
  -- always taken before the core's request lock.
  perform pg_advisory_xact_lock(hashtextextended(q::text||'/'||coalesce(p->>'project_ref','')||'/'
   ||coalesce(p->>'role',''),3));
  if not (replayed or exists(select 1 from quorum_private.agents a where a.quorum_id=q
     and a.device_id=(p->>'device_id')::uuid and a.session_ref=p->>'session_ref')
     or exists(select 1 from quorum_private.requests r where r.actor=who and r.operation=op and r.request_id=req)) then
   if membership.assigned_project_ref is not null and (p->>'project_ref' is distinct from membership.assigned_project_ref
      or (membership.assigned_role is not null and p->>'role' is distinct from membership.assigned_role)) then
    raise exception 'role_assignment_mismatch' using detail=jsonb_build_object('role',membership.assigned_role,
     'project_ref',membership.assigned_project_ref)::text;
   end if;
   -- A fixed snapshot could miss a holder that committed while this waited.
   if current_setting('transaction_isolation')<>'read committed' then
    raise exception 'role_exclusivity_requires_read_committed'; end if;
   -- Live holders exactly as resolve_role counts them.
   if exists(select 1 from quorum_private.agents a join quorum_private.devices d on d.id=a.device_id
     join quorum_private.memberships m on m.user_id=a.owner_id and m.quorum_id=a.quorum_id
     where a.quorum_id=q and a.role=p->>'role' and a.project_ref=p->>'project_ref' and a.owner_id<>who
      and not a.revoked and not d.revoked and not m.revoked) then
    raise exception 'role_held_by_other_identity';
   end if;
   select coalesce(array_agg(a.id order by a.id),'{}') into own from quorum_private.agents a
    join quorum_private.devices d on d.id=a.device_id
    where a.quorum_id=q and a.role=p->>'role' and a.project_ref=p->>'project_ref' and a.owner_id=who
     and not a.revoked and not d.revoked and a.id is distinct from keep;
   if cardinality(own)>0 then
    if not supersede then raise exception 'role_held_by_own_agent' using detail=own[1]::text; end if;
    -- Exactly agent_leave's effect; the core's device check below rolls it back on refusal.
    update quorum_private.agents set revoked=true where id=any(own);
   end if;
  end if;
  result:=quorum_private.call_role_core(op,core,req);
  if cardinality(own)>0 then
   insert into quorum_private.agent_supersessions(superseded_id,agent_id)
    select s,(result->>'agent_id')::uuid from unnest(own) s;
  end if;
  return result||jsonb_build_object('superseded',coalesce((select jsonb_agg(s.superseded_id order by s.superseded_id)
   from quorum_private.agent_supersessions s where s.agent_id=(result->>'agent_id')::uuid),'[]'::jsonb));

 else
  -- agent_retire: the quorum owner revokes one agent of the quorum, keeping its membership.
  if not quorum_private.verified_actor(who) then raise insufficient_privilege using message='auth_required'; end if;
  q:=(p->>'quorum_id')::uuid;
  if quorum_private.member(q,who)<>'owner' then raise insufficient_privilege using message='owner_required'; end if;
  fingerprint:=encode(sha256(convert_to(p::text,'UTF8')),'hex');
  perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
  select * into previous from quorum_private.requests r where r.actor=who and r.operation=op and r.request_id=req;
  if found then
   if previous.fingerprint<>fingerprint then raise exception 'idempotency_conflict'; end if;
   return previous.result;
  end if;
  select * into agent from quorum_private.agents a where a.id=(p->>'agent_id')::uuid and a.quorum_id=q for update;
  if not found then raise insufficient_privilege using message='agent_denied'; end if;
  update quorum_private.agents set revoked=true where id=agent.id;
  result:=jsonb_build_object('retired',true,'agent_id',agent.id,'already_revoked',coalesce(agent.revoked,false));
  insert into quorum_private.requests values(who,op,req,fingerprint,result);
  insert into quorum_private.audit_events(actor,operation,quorum_id) values(who,op,q);
  return result;
 end if;
end $function$;
revoke all on function quorum_private.call(text,jsonb,text) from public,anon,authenticated,service_role;

-- Trailing defaulted arguments: current clients send the old argument set. The
-- old signatures are dropped so the unqualified calls stay unambiguous.
drop function public.invite_create_id(uuid,text,text);
create function public.invite_create_id(p_quorum_id uuid,p_target_github_id text,p_request_id text,
 p_role text default null,p_project_ref text default null)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_create_id',jsonb_build_object('quorum_id',p_quorum_id,
  'target_github_id',p_target_github_id,'role',p_role,'project_ref',p_project_ref),p_request_id)
$$;
drop function public.invite_create_open(uuid,text);
create function public.invite_create_open(p_quorum_id uuid,p_request_id text,
 p_role text default null,p_project_ref text default null)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_create_open',jsonb_build_object('quorum_id',p_quorum_id,
  'role',p_role,'project_ref',p_project_ref),p_request_id)
$$;
drop function public.agent_join(uuid,uuid,text,text,text,text,text);
create function public.agent_join(p_quorum_id uuid,p_device_id uuid,p_project_ref text,p_role text,
 p_engine text,p_session_ref text,p_request_id text,p_supersede boolean default false,p_keep_agent_id uuid default null)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('agent_join',jsonb_build_object('quorum_id',p_quorum_id,'device_id',p_device_id,
  'project_ref',p_project_ref,'role',p_role,'engine',p_engine,'session_ref',p_session_ref,
  'supersede',coalesce(p_supersede,false),'keep_agent_id',p_keep_agent_id),p_request_id)
$$;
create function public.agent_retire(p_quorum_id uuid,p_agent_id uuid,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('agent_retire',jsonb_build_object('quorum_id',p_quorum_id,'agent_id',p_agent_id),p_request_id)
$$;

-- Owner roster: agent and binding metadata only, never a token, device label or session reference.
create function public.quorum_agents(p_quorum_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog as $function$
declare who uuid:=auth.uid(); result jsonb;
begin
 if not quorum_private.verified_actor(who) then raise insufficient_privilege using message='auth_required'; end if;
 if quorum_private.member(p_quorum_id,who)<>'owner' then raise insufficient_privilege using message='owner_required'; end if;
 select coalesce(jsonb_agg(to_jsonb(x) order by x.live desc,x.role,x.project_ref,x.last_seen desc nulls last,x.agent_id),
  '[]'::jsonb) into result from (
  select a.id agent_id,a.role,a.project_ref,a.engine,coalesce(a.revoked,false) revoked,
   not coalesce(a.revoked,false) and not coalesce(d.revoked,true) and not coalesce(m.revoked,true) live,
   h.last_seen,m.role member_role,a.owner_id=who own,
   case when gh.login is not null then gh.login when gh.provider_id is not null then 'github:'||gh.provider_id
    else 'anonymous:'||left(encode(sha256(convert_to(a.owner_id::text,'UTF8')),'hex'),8) end owner_label
  from quorum_private.agents a
  left join quorum_private.devices d on d.id=a.device_id
  left join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_health h on h.agent_id=a.id
  left join lateral (select i.provider_id,case when i.identity_data->>'user_name'
     ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$' then i.identity_data->>'user_name' end login
    from auth.identities i where i.user_id=a.owner_id and i.provider='github' order by i.provider_id limit 1) gh on true
  where a.quorum_id=p_quorum_id
  order by not coalesce(a.revoked,false) desc,h.last_seen desc nulls last,a.id
  limit 500) x;
 return result;
end $function$;

revoke all on function public.invite_create_id(uuid,text,text,text,text),public.invite_create_open(uuid,text,text,text),
 public.agent_join(uuid,uuid,text,text,text,text,text,boolean,uuid),public.agent_retire(uuid,uuid,text),
 public.quorum_agents(uuid) from public,anon,authenticated,service_role;
grant execute on function public.invite_create_id(uuid,text,text,text,text),public.invite_create_open(uuid,text,text,text),
 public.agent_join(uuid,uuid,text,text,text,text,text,boolean,uuid),public.agent_retire(uuid,uuid,text),
 public.quorum_agents(uuid) to authenticated;
