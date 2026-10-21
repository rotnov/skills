-- Owner-applied extension for the existing Quorum pilot backend.
-- This is not executed by skill installation or by session connect/start.
create table if not exists quorum_private.session_receipts (
 agent_id uuid not null references quorum_private.agents(id),
 message_id uuid not null references quorum_private.messages(id),
 queued_at timestamptz not null default now(),
 primary key(agent_id,message_id)
);
alter table quorum_private.session_receipts enable row level security;
revoke all on quorum_private.session_receipts from public,anon,authenticated;

create or replace function quorum_private.native_session(p_agent_id uuid,p_session_ref text)
returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid;
begin
 if auth.uid() is null or not exists(select 1 from auth.identities where user_id=auth.uid() and provider='github') then
  raise insufficient_privilege using message='github_identity_required'; end if;
 q:=quorum_private.owned_agent(p_agent_id,auth.uid());
 if not exists(select 1 from quorum_private.agents where id=p_agent_id and engine in ('codex','claude') and session_ref=p_session_ref) then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 return q;
end $$;
revoke all on function quorum_private.native_session(uuid,text) from public,anon,authenticated;

create or replace function public.session_inbox(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body,a.role as sender_role
  from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  join quorum_private.agents a on a.id=m.sender_id
  where m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)
  order by d.created_at,m.conversation_id,m.sequence limit 1
 ) x;
 return result;
end $$;
revoke all on function public.session_inbox(uuid,text) from public,anon;
grant execute on function public.session_inbox(uuid,text) to authenticated;

create or replace function public.session_ack(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id) then
  raise insufficient_privilege using message='message_denied'; end if;
 insert into quorum_private.session_receipts(agent_id,message_id) values(p_agent_id,p_message_id) on conflict do nothing;
 return jsonb_build_object('queued',true);
end $$;
revoke all on function public.session_ack(uuid,text,uuid) from public,anon;
grant execute on function public.session_ack(uuid,text,uuid) to authenticated;

create or replace function public.session_reply(p_agent_id uuid,p_session_ref text,p_message_id uuid,p_body text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; original quorum_private.messages%rowtype; answer quorum_private.messages%rowtype; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if p_body is null or octet_length(p_body)=0 or octet_length(p_body)>16384 then raise exception 'invalid_message'; end if;
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
  'recipient_agent_id',original.sender_id,'conversation_id',original.conversation_id,'reply_to',p_message_id,'body',p_body),
  'native-reply:'||p_agent_id||':'||p_message_id);
 -- A native UI receipt and a completed answer are distinct records.
 update quorum_private.deliveries set requires_handler=false where message_id=(result->>'message_id')::uuid;
 update quorum_private.deliveries set state='answered' where message_id=p_message_id and recipient_id=p_agent_id;
 return jsonb_build_object('reply_message_id',result->>'message_id','conversation_id',original.conversation_id);
end $$;
revoke all on function public.session_reply(uuid,text,uuid,text) from public,anon;
grant execute on function public.session_reply(uuid,text,uuid,text) to authenticated;
