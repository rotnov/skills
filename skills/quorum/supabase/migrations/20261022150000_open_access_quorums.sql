-- SOURCE ONLY. Administrator-reviewed extension; requires the invite-ID migration.
-- Installation/login never applies this migration. Run in one transaction.
-- Per-quorum access mode. 'github' (every existing quorum) keeps identity-bound
-- 24-hour invitations. 'open' adds single-use 15-minute invitations that any
-- authenticated Supabase user, including an anonymous sign-in, may accept.
-- Instance creation, ownership, access changes and invitation issuance still
-- require GitHub. Reviewed bodies are pinned; fail closed on schema drift.
alter table quorum_private.quorums add column access text not null default 'github'
 check (access in ('github','open'));
alter table quorum_private.id_invitations alter column target_github_id drop not null;
alter table quorum_private.id_invitations add column open boolean not null default false;
alter table quorum_private.id_invitations add constraint id_invitations_open_target_check
 check (open = (target_github_id is null));

-- GitHub users, or users holding a live membership in a currently open quorum.
-- Closing a quorum revokes its non-GitHub members, which removes this access.
create function quorum_private.verified_actor(who uuid)
returns boolean language sql stable security definer set search_path=pg_catalog as $$
 select who is not null and (
  exists(select 1 from auth.identities where user_id=who and provider='github')
  or exists(select 1 from quorum_private.memberships m join quorum_private.quorums q on q.id=m.quorum_id
            where m.user_id=who and not m.revoked and q.access='open'))
$$;
revoke all on function quorum_private.verified_actor(uuid) from public,anon,authenticated,service_role;

-- Replace exactly one reviewed GitHub gate in a pinned function body.
create function pg_temp.quorum_gate(f regprocedure, gate text, replacement text, reviewed text[])
returns void language plpgsql as $$
declare def text; patched text;
begin
 if not encode(sha256(convert_to((select prosrc from pg_proc where oid=f),'UTF8')),'hex')=any(reviewed) then
  raise exception 'reviewed_function_changed: %', f; end if;
 def:=pg_get_functiondef(f);
 patched:=replace(def,gate,replacement);
 if length(def)-length(patched)<>length(gate)-length(replacement) then
  raise exception 'reviewed_gate_not_unique: %', f; end if;
 execute patched;
end $$;

do $migration$
begin
 alter function quorum_private.call(text,jsonb,text) rename to call_invite_core;
 perform pg_temp.quorum_gate('quorum_private.call_invite_core(text,jsonb,text)',
  $g$not exists(select 1 from auth.identities where user_id=who and provider='github')$g$,
  'not quorum_private.verified_actor(who)',
  array['788189d4f25ab96bc8d412c32cb5300491abed24c33c1e4ab1cd69185111ad81']);
 perform pg_temp.quorum_gate('quorum_private.call_legacy_core(text,jsonb,text)',
  $g$not exists(select 1 from auth.identities where user_id=who and provider='github')$g$,
  'not quorum_private.verified_actor(who)',
  array['fb97e84ea0487aea1ebfe39aea71dec34eee944d164295a7b479180207db846d']);
 -- Bootstrap and native Grok session variants.
 perform pg_temp.quorum_gate('quorum_private.native_session(uuid,text)',
  $g$not exists(select 1 from auth.identities where user_id=auth.uid() and provider='github')$g$,
  'not quorum_private.verified_actor(auth.uid())',
  array['0fdd5a5c6bdd7d0e6d17eff99d5735f4596e9436145d408d136ccf6ac7ef7ab4',
        '8c2a1214c146838730713adad08bf7439b4fc6c3301637fef0ffc4d8f4596d00']);
 -- The Realtime extension is optional; without it clients keep polling.
 if to_regprocedure('quorum_private.receive_wakeup(text)') is not null then
  perform pg_temp.quorum_gate('quorum_private.receive_wakeup(text)',
   $g$exists(select 1 from auth.identities where user_id=auth.uid() and provider='github')$g$,
   'quorum_private.verified_actor(auth.uid())',
   array['4192466371136bcf122b7d628ee2293985ab05a261d1f9e2d192f1930a270f68',
         '6b7c6289a6d367bc7dd29df0ab2568aabdd370bcbedf0f2c6a3a56a448a69538']);
 end if;
end $migration$;
drop function pg_temp.quorum_gate(regprocedure,text,text,text[]);
revoke all on function quorum_private.call_invite_core(text,jsonb,text) from public,anon,authenticated,service_role;

create function quorum_private.call(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare
 who uuid:=auth.uid(); q uuid; membership uuid; result jsonb; previous record;
 invitation quorum_private.id_invitations%rowtype;
 fingerprint text:=encode(sha256(convert_to(p::text,'UTF8')),'hex');
begin
 if who is null then raise insufficient_privilege using message='auth_required'; end if;
 if op not in ('invite_accept_id','invite_create_open','quorum_set_access') then
  return quorum_private.call_invite_core(op,p,req);
 end if;
 if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072 then
  raise exception 'invalid_payload'; end if;
 if req is null or length(req) not between 1 and 128 then raise exception 'request_id_required'; end if;
 if op='invite_accept_id' then
  select * into invitation from quorum_private.id_invitations where id=(p->>'invite_id')::uuid for update;
  -- Identity-bound invitations keep the reviewed GitHub path unchanged.
  if not found or not invitation.open then return quorum_private.call_invite_core(op,p,req); end if;
  q:=invitation.quorum_id;
  if invitation.revoked or invitation.expires_at<=now()
     or (select access from quorum_private.quorums where id=q)<>'open' then
   raise insufficient_privilege using message='invite_denied'; end if;
  if invitation.accepted_by is not null then
   if invitation.accepted_by<>who then raise insufficient_privilege using message='invite_consumed'; end if;
   -- A consumed invitation cannot resurrect a revoked member.
   perform quorum_private.member(q,who);
  end if;
 else
  if not exists(select 1 from auth.identities where user_id=who and provider='github') then
   raise insufficient_privilege using message='github_identity_required'; end if;
  q:=(p->>'quorum_id')::uuid;
  if quorum_private.member(q,who)<>'owner' then raise insufficient_privilege using message='owner_required'; end if;
  if op='quorum_set_access' then
   if coalesce(p->>'access','') not in ('github','open') then raise exception 'invalid_access'; end if;
   update quorum_private.quorums set access=p->>'access' where id=q;
   if p->>'access'='github' then
    -- Closing removes every member admitted without GitHub and unused open invitations.
    update quorum_private.memberships m set revoked=true where m.quorum_id=q and not m.revoked
     and not exists(select 1 from auth.identities i where i.user_id=m.user_id and i.provider='github');
    update quorum_private.id_invitations set revoked=true where quorum_id=q and open and accepted_by is null;
   end if;
   insert into quorum_private.audit_events(actor,operation,quorum_id) values(who,op,q);
   -- Setting a mode is naturally idempotent; a stale cached answer would mislead.
   return jsonb_build_object('quorum_id',q,'access',p->>'access');
  end if;
  if (select access from quorum_private.quorums where id=q)<>'open' then
   raise insufficient_privilege using message='quorum_not_open'; end if;
 end if;
 perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
 select * into previous from quorum_private.requests where actor=who and operation=op and request_id=req;
 if found then
  if previous.fingerprint<>fingerprint then raise exception 'idempotency_conflict'; end if;
  return previous.result;
 end if;
 if op='invite_create_open' then
  insert into quorum_private.id_invitations(quorum_id,target_github_id,open,created_by,expires_at)
   values(q,null,true,who,now()+interval '15 minutes') returning * into invitation;
  result:=jsonb_build_object('invite_id',invitation.id,'expires_at',invitation.expires_at);
 else
  if invitation.accepted_by is null then
   insert into quorum_private.memberships(quorum_id,user_id,role) values(q,who,'member')
    on conflict(quorum_id,user_id) do update set revoked=false returning id into membership;
   update quorum_private.id_invitations set accepted_by=who where id=invitation.id;
  else
   select id into membership from quorum_private.memberships where quorum_id=q and user_id=who and not revoked;
  end if;
  result:=jsonb_build_object('quorum_id',q,'membership_id',membership);
 end if;
 insert into quorum_private.requests values(who,op,req,fingerprint,result);
 insert into quorum_private.audit_events(actor,operation,quorum_id) values(who,op,q);
 return result;
end $function$;
revoke all on function quorum_private.call(text,jsonb,text) from public,anon,authenticated,service_role;

create function public.invite_create_open(p_quorum_id uuid,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('invite_create_open',jsonb_build_object('quorum_id',p_quorum_id),p_request_id)
$$;
create function public.quorum_set_access(p_quorum_id uuid,p_access text,p_request_id text)
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('quorum_set_access',jsonb_build_object('quorum_id',p_quorum_id,'access',p_access),p_request_id)
$$;
revoke all on function public.invite_create_open(uuid,text) from public,anon;
revoke all on function public.quorum_set_access(uuid,text,text) from public,anon;
grant execute on function public.invite_create_open(uuid,text) to authenticated;
grant execute on function public.quorum_set_access(uuid,text,text) to authenticated;
