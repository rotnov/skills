-- SOURCE ONLY. Administrator-reviewed extension of the 2026-10-22 pilot.
-- Installation/login never applies this migration. Run in one transaction.
-- Check the reviewed dispatcher before wrapping it; fail closed on schema drift.
do $migration$
begin
 if encode(sha256(convert_to(pg_get_functiondef('quorum_private.call(text,jsonb,text)'::regprocedure),'UTF8')),'hex')
   <> '4943ace7810ac4084313d4045fcb8def4b6c29c15162a770935dd440ed211213' then
  raise exception 'reviewed_dispatcher_changed'; end if;
 alter function quorum_private.call(text,jsonb,text) rename to call_legacy_core;
end $migration$;
revoke all on function quorum_private.call_legacy_core(text,jsonb,text) from public,anon,authenticated,service_role;

-- Only a database administrator manages instance creation capability. Quorum
-- ownership, client flags, JWT/user metadata and membership never grant it.
create table quorum_private.instance_creators (
 github_id text primary key check(github_id ~ '^[0-9]{1,20}$'),
 revoked boolean not null default false
);
alter table quorum_private.instance_creators enable row level security;
revoke all on quorum_private.instance_creators from public,anon,authenticated,service_role;
-- Starts empty: administrator explicitly grants the instance owner's verified ID.

-- Separate storage prevents existing token invitation IDs acquiring new meaning.
create table quorum_private.id_invitations (
 id uuid primary key default gen_random_uuid(),
 quorum_id uuid not null references quorum_private.quorums(id),
 target_github_id text not null check(target_github_id ~ '^[0-9]{1,20}$'),
 created_by uuid not null,
 expires_at timestamptz not null default now()+interval '24 hours',
 accepted_by uuid,
 revoked boolean not null default false
);
alter table quorum_private.id_invitations enable row level security;
revoke all on quorum_private.id_invitations from public,anon,authenticated,service_role;

create function quorum_private.call(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare
 who uuid:=auth.uid(); q uuid; membership uuid; result jsonb; previous record;
 invitation quorum_private.id_invitations%rowtype;
 fingerprint text:=encode(sha256(convert_to(p::text,'UTF8')),'hex');
begin
 if who is null then raise insufficient_privilege using message='auth_required'; end if;
 if not exists(select 1 from auth.identities where user_id=who and provider='github') then
  raise insufficient_privilege using message='github_identity_required'; end if;
 if op='quorum_create' then
  -- Before *all* legacy execution, including cached successful retries.
  -- Row lock serializes administrative revocation with authorized creation.
  perform 1 from quorum_private.instance_creators c where not c.revoked and exists
   (select 1 from auth.identities i where i.user_id=who and i.provider='github' and i.provider_id=c.github_id)
   for share of c;
  if not found then raise insufficient_privilege using message='instance_creator_required'; end if;
 end if;
 if op not in ('invite_create_id','invite_accept_id','invite_revoke_id') then
  return quorum_private.call_legacy_core(op,p,req);
 end if;
 if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072 then
  raise exception 'invalid_payload'; end if;
 if req is null or length(req) not between 1 and 128 then raise exception 'request_id_required'; end if;
 if op='invite_create_id' then
  q:=(p->>'quorum_id')::uuid;
  if quorum_private.member(q,who)<>'owner' then raise insufficient_privilege using message='owner_required'; end if;
  if p->>'target_github_id' is null or p->>'target_github_id' !~ '^[0-9]{1,20}$' then
   raise exception 'invalid_target_github_id'; end if;
 else
  select * into invitation from quorum_private.id_invitations where id=(p->>'invite_id')::uuid for update;
  if not found then raise insufficient_privilege using message='invite_denied'; end if;
  q:=invitation.quorum_id;
  if op='invite_revoke_id' then
   if quorum_private.member(q,who)<>'owner' then raise insufficient_privilege using message='owner_required'; end if;
  else
   if invitation.revoked or invitation.expires_at<=now() then
    raise insufficient_privilege using message='invite_denied'; end if;
   if not exists(select 1 from auth.identities where user_id=who and provider='github'
     and provider_id=invitation.target_github_id) then
    raise insufficient_privilege using message='invite_target_mismatch'; end if;
   if invitation.accepted_by is not null then
    if invitation.accepted_by<>who then raise insufficient_privilege using message='invite_consumed'; end if;
    -- A consumed invitation cannot resurrect a revoked member, even under a
    -- fresh request ID or a successful cached acceptance response.
    perform quorum_private.member(q,who);
   end if;
  end if;
 end if;
 perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
 select * into previous from quorum_private.requests where actor=who and operation=op and request_id=req;
 if found then
  if previous.fingerprint<>fingerprint then raise exception 'idempotency_conflict'; end if;
  return previous.result;
 end if;
 case op
 when 'invite_create_id' then
  insert into quorum_private.id_invitations(quorum_id,target_github_id,created_by)
   values(q,p->>'target_github_id',who) returning * into invitation;
  result:=jsonb_build_object('invite_id',invitation.id,'expires_at',invitation.expires_at);
 when 'invite_accept_id' then
  if invitation.accepted_by is null then
   insert into quorum_private.memberships(quorum_id,user_id,role) values(q,who,'member')
    on conflict(quorum_id,user_id) do update set revoked=false returning id into membership;
   update quorum_private.id_invitations set accepted_by=who where id=invitation.id;
  else
   select id into membership from quorum_private.memberships where quorum_id=q and user_id=who and not revoked;
  end if;
  result:=jsonb_build_object('quorum_id',q,'membership_id',membership);
 when 'invite_revoke_id' then
  update quorum_private.id_invitations set revoked=true where id=invitation.id;
  result:=jsonb_build_object('revoked',true,'invite_id',invitation.id);
 end case;
 insert into quorum_private.requests values(who,op,req,fingerprint,result);
 insert into quorum_private.audit_events(actor,operation,quorum_id) values(who,op,q);
 return result;
end $function$;
revoke all on function quorum_private.call(text,jsonb,text) from public,anon,authenticated,service_role;

create function public.invite_create_id(p_quorum_id uuid,p_target_github_id text,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_create_id',jsonb_build_object('quorum_id',p_quorum_id,'target_github_id',p_target_github_id),p_request_id)
$$;
create function public.invite_accept_id(p_invite_id uuid,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_accept_id',jsonb_build_object('invite_id',p_invite_id),p_request_id)
$$;
create function public.invite_revoke_id(p_invite_id uuid,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_revoke_id',jsonb_build_object('invite_id',p_invite_id),p_request_id)
$$;
revoke all on function public.invite_create_id(uuid,text,text) from public,anon;
revoke all on function public.invite_accept_id(uuid,text) from public,anon;
revoke all on function public.invite_revoke_id(uuid,text) from public,anon;
grant execute on function public.invite_create_id(uuid,text,text) to authenticated;
grant execute on function public.invite_accept_id(uuid,text) to authenticated;
grant execute on function public.invite_revoke_id(uuid,text) to authenticated;
