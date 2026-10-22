-- SOURCE ONLY. Administrator-reviewed extension of the 2026-10-22 pilot.
-- Installation, login, session connect and reception never apply this file.
-- Run in one transaction. No new grant, policy, table privilege or role.
--
-- Priority is message metadata only: it changes durable inbox ordering and the
-- client's delivery route, never membership, device, owner authority or any
-- permission check. Senders cannot reorder another recipient's inbox.
--
-- Before applying, confirm the reviewed dispatcher is the one you reviewed:
--   select encode(sha256(convert_to(pg_get_functiondef(
--     'quorum_private.call(text,jsonb,text)'::regprocedure),'UTF8')),'hex');
-- The structural gate below fails closed on the drift this file can detect.
do $migration$
begin
 if to_regprocedure('quorum_private.call(text,jsonb,text)') is null
   or to_regclass('quorum_private.id_invitations') is null
   or position('call_legacy_core' in
     pg_get_functiondef('quorum_private.call(text,jsonb,text)'::regprocedure)) = 0 then
  raise exception 'reviewed_dispatcher_changed'; end if;
 if to_regprocedure('quorum_private.call_priority_core(text,jsonb,text)') is not null then
  raise exception 'priority_core_present'; end if;
 -- session_inbox below keeps the native-session coordination notification
 -- field, so that reviewed extension must already be applied.
 if to_regclass('quorum_private.session_notices') is null then
  raise exception 'session_coordination_required'; end if;
 alter function quorum_private.call(text,jsonb,text) rename to call_priority_core;
end $migration$;
revoke all on function quorum_private.call_priority_core(text,jsonb,text) from public,anon,authenticated,service_role;

alter table quorum_private.messages add column priority text not null default 'normal';
alter table quorum_private.messages add constraint messages_priority_check
 check (priority in ('normal','urgent'));

-- The reviewed core keeps owning identity, membership, device, conversation and
-- idempotency checks. This wrapper only binds a validated priority to the row
-- the core inserted, inside the core's own transaction.
create function quorum_private.call(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare who uuid:=auth.uid(); prio text; replayed boolean; result jsonb;
begin
 if op<>'message_send' then return quorum_private.call_priority_core(op,p,req); end if;
 if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072 then
  raise exception 'invalid_payload'; end if;
 prio:=coalesce(p->>'priority','normal');
 if prio not in ('normal','urgent') then raise exception 'invalid_priority'; end if;
 if who is null then raise insufficient_privilege using message='auth_required'; end if;
 if req is null or length(req) not between 1 and 128 then raise exception 'request_id_required'; end if;
 -- The same advisory key the core uses, so a concurrent identical request
 -- cannot interleave between this replay probe and the core's own insert.
 perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
 select count(*)>0 into replayed from quorum_private.requests
  where actor=who and operation=op and request_id=req;
 result:=quorum_private.call_priority_core(op,p-'priority'::text,req);
 if replayed then
  -- Priority is deliberately outside the core's deduplication fingerprint, so
  -- a replay must agree with the stored message instead of rewriting it.
  if exists(select 1 from quorum_private.messages m
    where m.id=(result->>'message_id')::uuid and m.priority<>prio) then
   raise exception 'idempotency_conflict'; end if;
 elsif prio<>'normal' then
  update quorum_private.messages m set priority=prio where m.id=(result->>'message_id')::uuid;
 end if;
 return result;
end $function$;
revoke all on function quorum_private.call(text,jsonb,text) from public,anon,authenticated,service_role;

-- Adding a defaulted argument would overload the existing entry point and make
-- the unqualified call ambiguous, so the old signature is replaced outright.
drop function public.message_send(uuid,uuid,uuid,uuid,uuid,text,text);
create function public.message_send(p_quorum_id uuid,p_sender_agent_id uuid,p_conversation_id uuid,
 p_recipient_agent_id uuid,p_reply_to uuid,p_body text,p_request_id text,p_priority text default 'normal')
returns jsonb language sql security definer set search_path=pg_catalog as $$
 select quorum_private.call('message_send',jsonb_build_object('quorum_id',p_quorum_id,
  'sender_agent_id',p_sender_agent_id,'conversation_id',p_conversation_id,
  'recipient_agent_id',p_recipient_agent_id,'reply_to',p_reply_to,'body',p_body,
  'priority',coalesce(p_priority,'normal')),p_request_id)
$$;
revoke all on function public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text) from public,anon;
grant execute on function public.message_send(uuid,uuid,uuid,uuid,uuid,text,text,text) to authenticated;

-- An unreceipted normal head must not hide a later urgent message behind it.
-- Ordering stays deterministic within a priority: delivery time, then the
-- conversation's own sequence, so normal queue order is unchanged. This is the
-- reviewed coordination definition (notification included) plus only the
-- priority column and the urgent-first ordering term.
create or replace function public.session_inbox(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body,a.role as sender_role,
   exists(select 1 from quorum_private.session_notices n where n.message_id=m.id) as notification,
   m.priority
  from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  join quorum_private.agents a on a.id=m.sender_id
  where m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)
  order by (m.priority='urgent') desc,d.created_at,m.conversation_id,m.sequence limit 1
 ) x;
 return result;
end $$;
revoke all on function public.session_inbox(uuid,text) from public,anon;
grant execute on function public.session_inbox(uuid,text) to authenticated;

-- Replies may also be urgent. Reply-loop prevention, the single-answer rule and
-- the active-attempt reconciliation gate are unchanged.
drop function public.session_reply(uuid,text,uuid,text);
create function public.session_reply(p_agent_id uuid,p_session_ref text,p_message_id uuid,p_body text,
 p_priority text default 'normal')
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; original quorum_private.messages%rowtype; answer quorum_private.messages%rowtype; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if p_body is null or octet_length(p_body)=0 or octet_length(p_body)>16384 then raise exception 'invalid_message'; end if;
 if coalesce(p_priority,'normal') not in ('normal','urgent') then raise exception 'invalid_priority'; end if;
 select * into original from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id for update;
 if not found then raise insufficient_privilege using message='message_denied'; end if;
 select * into answer from quorum_private.messages where reply_to=p_message_id and sender_id=p_agent_id order by sequence limit 1;
 if found then
  if answer.body<>p_body then raise exception 'reply_already_sent'; end if;
  return jsonb_build_object('reply_message_id',answer.id,'conversation_id',answer.conversation_id);
 end if;
 if exists(select 1 from quorum_private.deliveries where message_id=p_message_id and state not in ('queued','answered')) then
  raise exception 'active_attempt_requires_reconciliation'; end if;
 result:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
  'recipient_agent_id',original.sender_id,'conversation_id',original.conversation_id,'reply_to',p_message_id,
  'body',p_body,'priority',coalesce(p_priority,'normal')),
  'native-reply:'||p_agent_id||':'||p_message_id);
 -- A native UI receipt and a completed answer are distinct records.
 update quorum_private.deliveries set requires_handler=false where message_id=(result->>'message_id')::uuid;
 update quorum_private.deliveries set state='answered' where message_id=p_message_id and recipient_id=p_agent_id;
 return jsonb_build_object('reply_message_id',result->>'message_id','conversation_id',original.conversation_id);
end $$;
revoke all on function public.session_reply(uuid,text,uuid,text,text) from public,anon;
grant execute on function public.session_reply(uuid,text,uuid,text,text) to authenticated;
