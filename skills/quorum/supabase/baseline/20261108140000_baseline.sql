-- GENERATED. Do not edit. Quorum server baseline, core.
-- Covers references/server-bootstrap.sql and supabase/migrations through 20261108140000.
-- Sources sha256 32c66895f79f44b19c12ba01cec091b445016bf0c5f938d44843984cdbb1512a (tests/quorum_baseline_support.py --check).
-- Regenerate: QUORUM_BASELINE_DOCKER=1 uv run --frozen python tests/quorum_baseline_support.py --write
-- Fresh Supabase project only, in one transaction, before any migration newer than 20261108140000.
-- See references/server.md#fresh-project and #baseline-maintenance-window.
DO $baseline$ BEGIN
 IF pg_catalog.to_regnamespace('quorum_private') IS NOT NULL THEN
  RAISE EXCEPTION 'quorum_schema_present' USING HINT='An existing server adopts the baseline instead: supabase/baseline/20261108140000_adopt.sql';
 END IF;
END $baseline$;
SET LOCAL check_function_bodies = off;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA IF NOT EXISTS extensions;

CREATE SCHEMA quorum_private;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE FUNCTION public.agent_join(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text, p_supersede boolean DEFAULT false, p_keep_agent_id uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('agent_join',jsonb_build_object('quorum_id',p_quorum_id,'device_id',p_device_id,
  'project_ref',p_project_ref,'role',p_role,'engine',p_engine,'session_ref',p_session_ref,
  'supersede',coalesce(p_supersede,false),'keep_agent_id',p_keep_agent_id),p_request_id)
$$;

CREATE FUNCTION public.agent_leave(p_agent_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('agent_leave',jsonb_build_object('agent_id',p_agent_id),p_request_id) $$;

CREATE FUNCTION public.agent_retire(p_quorum_id uuid, p_agent_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('agent_retire',jsonb_build_object('quorum_id',p_quorum_id,'agent_id',p_agent_id),p_request_id)
$$;

CREATE FUNCTION public.agent_status(p_agent_id uuid) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('agent_status',jsonb_build_object('agent_id',p_agent_id),null) $$;

CREATE FUNCTION public.attempt_update(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('attempt_update',jsonb_build_object('attempt_id',p_attempt_id,'generation',p_generation,'event',p_event),p_request_id) $$;

CREATE FUNCTION public.context_read(p_quorum_id uuid, p_project_ref text, p_after_version bigint) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('context_read',jsonb_build_object('quorum_id',p_quorum_id,'project_ref',p_project_ref,'after_version',p_after_version),null) $$;

CREATE FUNCTION public.conversation_read(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('conversation_read',jsonb_build_object('quorum_id',p_quorum_id,'agent_id',p_agent_id,'conversation_id',p_conversation_id,'after_sequence',p_after_sequence),null) $$;

CREATE FUNCTION public.delivery_complete(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('delivery_complete',jsonb_build_object('attempt_id',p_attempt_id,'generation',p_generation,'reply_body',p_reply_body),p_request_id) $$;

CREATE FUNCTION public.device_register(p_label text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('device_register',jsonb_build_object('label',p_label),p_request_id) $$;

CREATE FUNCTION public.inbox_claim(p_device_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('inbox_claim',jsonb_build_object('device_id',p_device_id),p_request_id) $$;

CREATE FUNCTION public.invite_accept(p_token text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('invite_accept',jsonb_build_object('token',p_token),p_request_id) $$;

CREATE FUNCTION public.invite_accept_id(p_invite_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('invite_accept_id',jsonb_build_object('invite_id',p_invite_id),p_request_id)
$$;

CREATE FUNCTION public.invite_create(p_quorum_id uuid, p_target_github_id text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('invite_create',jsonb_build_object('quorum_id',p_quorum_id,'target_github_id',p_target_github_id),p_request_id) $$;

CREATE FUNCTION public.invite_create_id(p_quorum_id uuid, p_target_github_id text, p_request_id text, p_role text DEFAULT NULL::text, p_project_ref text DEFAULT NULL::text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('invite_create_id',jsonb_build_object('quorum_id',p_quorum_id,
  'target_github_id',p_target_github_id,'role',p_role,'project_ref',p_project_ref),p_request_id)
$$;

CREATE FUNCTION public.invite_create_open(p_quorum_id uuid, p_request_id text, p_role text DEFAULT NULL::text, p_project_ref text DEFAULT NULL::text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('invite_create_open',jsonb_build_object('quorum_id',p_quorum_id,
  'role',p_role,'project_ref',p_project_ref),p_request_id)
$$;

CREATE FUNCTION public.invite_revoke_id(p_invite_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('invite_revoke_id',jsonb_build_object('invite_id',p_invite_id),p_request_id)
$$;

CREATE FUNCTION public.member_revoke(p_quorum_id uuid, p_member_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('member_revoke',jsonb_build_object('quorum_id',p_quorum_id,'member_id',p_member_id),p_request_id) $$;

CREATE FUNCTION public.message_send(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text, p_priority text DEFAULT 'normal'::text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('message_send',jsonb_build_object('quorum_id',p_quorum_id,
  'sender_agent_id',p_sender_agent_id,'conversation_id',p_conversation_id,
  'recipient_agent_id',p_recipient_agent_id,'reply_to',p_reply_to,'body',p_body,
  'priority',coalesce(p_priority,'normal')),p_request_id)
$$;

CREATE FUNCTION public.quorum_agents(p_quorum_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
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
end $_$;

CREATE FUNCTION public.quorum_check_owner(p_quorum_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 if quorum_private.member(p_quorum_id,auth.uid())<>'owner' then raise insufficient_privilege; end if;
 return 'true'::jsonb;
end $$;

CREATE FUNCTION public.quorum_create(p_slug text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('quorum_create',jsonb_build_object('slug',p_slug),p_request_id) $$;

CREATE FUNCTION public.quorum_lookup(p_slug text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; begin
 select id into q from quorum_private.quorums where slug=p_slug;
 perform quorum_private.member(q,auth.uid());
 return jsonb_build_object('quorum_id',q);
end $$;

CREATE FUNCTION public.quorum_set_access(p_quorum_id uuid, p_access text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select quorum_private.call('quorum_set_access',jsonb_build_object('quorum_id',p_quorum_id,'access',p_access),p_request_id)
$$;

CREATE FUNCTION public.resolve_role(p_quorum_id uuid, p_role text, p_project_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare ids uuid[]; begin
 perform quorum_private.member(p_quorum_id,auth.uid());
 select array_agg(a.id) into ids from quorum_private.agents a
 join quorum_private.devices d on d.id=a.device_id
 join quorum_private.memberships m on m.user_id=a.owner_id and m.quorum_id=a.quorum_id
 where a.quorum_id=p_quorum_id and a.role=p_role and a.project_ref=p_project_ref and not a.revoked and not d.revoked and not m.revoked;
 if coalesce(cardinality(ids),0)<>1 then raise exception 'recipient_ambiguous_or_missing'; end if;
 return jsonb_build_object('agent_id',ids[1]);
end $$;

CREATE FUNCTION public.session_ack(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb; q uuid;
begin
 -- Ordinary receipts retain the exact reviewed implementation/semantics.
 if not exists(select 1 from quorum_private.update_notices where message_id=p_message_id and recipient_id=p_agent_id) then
  return quorum_private.session_ack_before_updates(p_agent_id,p_session_ref,p_message_id); end if;
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 select quorum_id into q from quorum_private.agents where id=p_agent_id;
 perform 1 from quorum_private.quorums where id=q for share;
 result:=quorum_private.session_ack_before_updates(p_agent_id,p_session_ref,p_message_id);
 -- The reviewed receipt may wait on a receipt/health write. Revalidate after
 -- it returns; a refusal rolls back both original receipt and new phase writes.
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 update quorum_private.update_notices set received_at=coalesce(received_at,now())
  where message_id=p_message_id and recipient_id=p_agent_id;
 return result;
end $$;

CREATE FUNCTION public.session_delivery_changes(p_agent_id uuid, p_session_ref text, p_after bigint DEFAULT 0) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; items jsonb; last_seen bigint;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if p_after is null or p_after<0 then raise exception 'invalid_delivery_status'; end if;
 select jsonb_agg(jsonb_build_object('seq',x.seq,'message_id',x.message_id,'recipient_id',x.recipient_id,
   'recipient_role',x.role,'status',x.status,'status_at',x.status_at) order by x.seq),max(x.seq)
  into items,last_seen
 from (select s.seq,s.message_id,s.recipient_id,a.role,s.status,s.status_at
       from quorum_private.delivery_statuses s join quorum_private.agents a on a.id=s.recipient_id
       where s.sender_id=p_agent_id and s.quorum_id=q and s.seq>p_after order by s.seq limit 64) x;
 return jsonb_build_object('rows',coalesce(items,'[]'::jsonb),'cursor',coalesce(last_seen,p_after),
  'more',exists(select 1 from quorum_private.delivery_statuses s
                where s.sender_id=p_agent_id and s.quorum_id=q and s.seq>coalesce(last_seen,p_after)));
end $$;

CREATE FUNCTION public.session_delivery_mark(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_status text, p_reason text DEFAULT NULL::text, p_proof text DEFAULT NULL::text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; m quorum_private.messages%rowtype;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if p_status is null or p_status not in ('queued','read','in_progress','deferred','needs_owner','declined','done','failed')
    or (p_status='read')<>(p_proof is not null)
    or (p_proof is not null and p_proof not in ('received','body_read','reply'))
    or (p_status in ('queued','read') and p_reason is not null)
    or (p_status in ('deferred','declined') and p_reason is null)
    or (p_reason is not null and octet_length(p_reason) not between 1 and 512) then
  raise exception 'invalid_delivery_status'; end if;
 select * into m from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id;
 if not found then raise insufficient_privilege using message='message_denied'; end if;
 if not quorum_private.delivery_status_applies(m.id) then raise exception 'delivery_status_not_applicable'; end if;
 perform quorum_private.delivery_status_apply(m,p_status,p_reason,p_proof);
 return quorum_private.delivery_status_view(m.id);
end $$;

CREATE FUNCTION public.session_delivery_status(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages m where m.id=p_message_id and m.quorum_id=q
               and p_agent_id in (m.sender_id,m.recipient_id))
    or not quorum_private.delivery_status_applies(p_message_id) then
  return null; end if;
 return quorum_private.delivery_status_view(p_message_id);
end $$;

CREATE FUNCTION public.session_delivery_stuck(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; unread_limit constant interval:=interval '900 seconds'; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 with outgoing as materialized (
  select m.id,m.recipient_id,a.role,coalesce(s.status,'sent') status,coalesce(s.status_at,d.created_at) status_at,
   s.message_id is not null reported,
   exists(select 1 from quorum_private.session_receipts r where r.agent_id=m.recipient_id and r.message_id=m.id) receipted
  from quorum_private.deliveries d
  join quorum_private.messages m on m.id=d.message_id
  join quorum_private.agents a on a.id=m.recipient_id
  left join quorum_private.delivery_statuses s on s.message_id=m.id
  where d.quorum_id=q and d.created_at>now()-interval '7 days' and m.sender_id=p_agent_id and m.reply_to is null
   and quorum_private.delivery_status_applies(m.id)),
 unsettled as (
  select *,status in ('sent','queued','in_progress','needs_owner') timed,
   status in ('sent','queued','in_progress','needs_owner') and status_at<=now()-unread_limit overdue
  from outgoing where status not in ('answered','declined','done','failed') and (reported or not receipted))
 select jsonb_build_object('limit_seconds',extract(epoch from unread_limit)::integer,'open',count(*),
  'stuck_total',count(*) filter (where overdue),
  'stuck',coalesce((select jsonb_agg(y.item) from (
    select jsonb_build_object('message_id',o.id,'recipient_id',o.recipient_id,'recipient_role',o.role,
     'status',o.status,'status_at',o.status_at,'unread',o.status in ('sent','queued')) item
    from unsettled o where o.overdue order by o.status_at desc limit 32) y),'[]'::jsonb),
  'next_check_seconds',ceil(extract(epoch from
    min(status_at) filter (where timed and not overdue)+unread_limit-now()))::integer)
 into result from unsettled;
 return result;
end $$;

CREATE FUNCTION public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text DEFAULT NULL::text, p_awaiting_owner integer DEFAULT NULL::integer, p_reception_hold_until timestamp with time zone DEFAULT NULL::timestamp with time zone, p_client_run_id uuid DEFAULT NULL::uuid, p_runtime_started_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; own quorum_private.agents%rowtype;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 -- #196: a new connection on the required protocol is announced by presence alone, so the
 -- reviewed core counts every same-project peer as already greeted and sends no Hello.
 if p_connection_id is not null
    and p_protocol_version is not distinct from (public.session_requirements()->>'protocol_version')::integer
    and not exists(select 1 from quorum_private.session_connections c
     where c.agent_id=p_agent_id and c.connection_id=p_connection_id) then
  select * into own from quorum_private.agents where id=p_agent_id;
  insert into quorum_private.session_hello_sent(sender_id,recipient_id,sent_at)
  select p_agent_id,a.id,now() from quorum_private.agents a
  where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
  order by a.id
  on conflict(sender_id,recipient_id) do update set sent_at=excluded.sent_at;
 end if;
 return quorum_private.session_heartbeat_before_presence(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version,p_awaiting_owner,p_reception_hold_until,p_client_run_id,p_runtime_started_at);
end $$;

CREATE FUNCTION public.session_inbox(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb;
begin
 select coalesce(jsonb_agg(item.value#-'{maintenance,target,skill_version}' order by item.ordinality),'[]'::jsonb)
  into result
 from jsonb_array_elements(quorum_private.session_inbox_before_hash_only(p_agent_id,p_session_ref)) with ordinality item;
 return result;
end $$;

CREATE FUNCTION public.session_message(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select to_jsonb(x) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body
  from quorum_private.messages m
  where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id) x;
 return result;
end $$;

CREATE FUNCTION public.session_message_unreceipted(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 return jsonb_build_object('message_id',p_message_id,'unreceipted',exists(
  select 1 from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)));
end $$;

CREATE FUNCTION public.session_peers(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare required jsonb; result jsonb;
begin
 result:=quorum_private.session_peers_before_hash_only(p_agent_id,p_session_ref);
 required:=public.session_requirements();
 select coalesce(jsonb_agg(peer.value||jsonb_build_object('client',(peer.value->'client')-'skill_version',
   'required',required,'update_required',(peer.value#>>'{client,protocol_version}')::integer
    is distinct from (required->>'protocol_version')::integer) order by peer.ordinality),'[]'::jsonb) into result
 from jsonb_array_elements(result) with ordinality peer;
 return result;
end $$;

CREATE FUNCTION public.session_release_report(p_agent_id uuid, p_session_ref text, p_content_sha256 text, p_client_run_id uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
begin
 perform quorum_private.native_session(p_agent_id,p_session_ref);
 if p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'release_report_invalid'; end if;
 insert into quorum_private.session_release(agent_id,content_sha256,client_run_id,reported_at)
 values(p_agent_id,p_content_sha256,p_client_run_id,now())
 on conflict(agent_id) do update set content_sha256=excluded.content_sha256,client_run_id=excluded.client_run_id,
  reported_at=excluded.reported_at;
 return jsonb_build_object('recorded',true);
end $_$;

CREATE FUNCTION public.session_release_status(p_agent_id uuid, p_session_ref text, p_operation_id uuid DEFAULT NULL::uuid, p_after_agent uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; project text; target jsonb; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select project_ref into project from quorum_private.agents where id=p_agent_id;
 if p_operation_id is not null and not exists(select 1 from quorum_private.update_operations o
  join quorum_private.update_targets t on t.id=o.target_id
  where o.id=p_operation_id and o.initiator_id=p_agent_id and t.quorum_id=q) then
  raise insufficient_privilege using message='update_status_denied'; end if;
 select jsonb_build_object('protocol_version',t.protocol_version,'content_sha256',t.content_sha256,'requested_at',o.created_at)
  into target from quorum_private.update_operations o join quorum_private.update_targets t on t.id=o.target_id
  where t.quorum_id=q order by o.created_at desc,o.id desc limit 1;
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select a.id as agent_id,
   case k.via when 'heartbeat' then r.content_sha256 when 'update_report' then report.content_sha256 end as content_sha256,
   case k.via when 'heartbeat' then r.reported_at when 'update_report' then report.reported_at end as reported_at,
   k.via as reported_via
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_status s on s.agent_id=a.id
  left join quorum_private.session_release r on r.agent_id=a.id
  -- The newest runtime self-report of this exact agent; written only by session_update_report.
  left join lateral (
   select n.runtime_evidence->>'content_sha256' as content_sha256,(n.runtime_evidence->>'run_id')::uuid as run_id,
    (n.runtime_evidence->>'reported_at')::timestamptz as reported_at
   from quorum_private.update_notices n
   where n.recipient_id=a.id and n.runtime_evidence ? 'run_id' and n.runtime_evidence ? 'reported_at'
   order by (n.runtime_evidence->>'reported_at')::timestamptz desc limit 1
  ) report on true
  cross join lateral (
   select case when r.agent_id is not null and r.client_run_id is not distinct from s.client_run_id then 'heartbeat'
    when report.run_id=s.client_run_id then 'update_report' end as via
  ) k
  where a.quorum_id=q and case when p_operation_id is null
    then a.project_ref=project and not a.revoked and not d.revoked and not m.revoked and a.engine in ('codex','claude','grok')
    else exists(select 1 from quorum_private.update_recipients u where u.operation_id=p_operation_id and u.recipient_id=a.id)
     and (p_after_agent is null or a.id>p_after_agent) end
  order by a.id limit case when p_operation_id is null then 128 else 64 end
 ) x;
 return jsonb_build_object('target',target,'agents',result);
end $$;

CREATE FUNCTION public.session_reply(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text, p_priority text DEFAULT 'normal'::text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; original quorum_private.messages%rowtype; answer quorum_private.messages%rowtype; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if p_body is null or octet_length(p_body)=0 or octet_length(p_body)>16384 then raise exception 'invalid_message'; end if;
 if coalesce(p_priority,'normal') not in ('normal','urgent') then raise exception 'invalid_priority'; end if;
 select * into original from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id for update;
 if not found then raise insufficient_privilege using message='message_denied'; end if;
 select * into answer from quorum_private.messages where reply_to=p_message_id and sender_id=p_agent_id order by sequence limit 1;
 if found then
  if answer.body<>p_body or answer.priority<>coalesce(p_priority,'normal') then
   raise exception 'reply_already_sent'; end if;
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

CREATE FUNCTION public.session_requirements() RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 if not quorum_private.verified_actor(auth.uid()) then
  raise insufficient_privilege using message='github_identity_required'; end if;
 return jsonb_build_object('protocol_version',2);
end $$;

CREATE FUNCTION public.session_task_authority(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; project text; original record; allowed boolean; reason text; maintenance boolean:=false;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select a.project_ref into project from quorum_private.agents a where a.id=p_agent_id;
 if project is null or octet_length(project) not between 1 and 500 -- #143
   or octet_length(p_session_ref)>128 then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 select m.id,m.sender_id,m.reply_to into original from quorum_private.messages m
 where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id;
 if not found or original.sender_id is null then
  raise insufficient_privilege using message='task_message_denied'; end if;
 -- #69 adds this authenticated private type source. Its absence is the sole
 -- optional schema case; malformed/present schema errors must fail closed.
 if to_regclass('quorum_private.update_notices') is not null then
  select exists(select 1 from quorum_private.update_notices n where n.message_id=original.id) into maintenance;
 end if;
 if maintenance or original.reply_to is not null or exists(
     select 1 from quorum_private.session_notices n where n.message_id=original.id) then
  allowed:=false; reason:='not_task';
 else
  select exists(select 1 from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id and d.owner_id=a.owner_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.id=original.sender_id and a.quorum_id=q and a.project_ref=project
    and not a.revoked and not d.revoked and not m.revoked) into allowed;
  reason:=case when allowed then 'current_member' else 'sender_unavailable' end;
 end if;
 return jsonb_build_object('authorized',allowed,'reason',reason,'message_id',original.id,
  'agent_id',p_agent_id,'session_ref',p_session_ref,'sender_id',original.sender_id,
  'quorum_id',q,'project_ref',project);
end $$;

CREATE FUNCTION public.session_update_report(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_notice_id uuid, p_event text, p_evidence jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare q uuid; n quorum_private.update_notices%rowtype; target quorum_private.update_targets%rowtype;
 expected jsonb; previous quorum_private.update_reports%rowtype;
 -- #172: a copy that still sends the number is heard without it.
 evidence jsonb:=case when jsonb_typeof(p_evidence)='object' then p_evidence-'skill_version' else p_evidence end; -- #172
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Keep close's quorum-first ordering before the notice and authority row locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege; end if;
 select * into n from quorum_private.update_notices where id=p_notice_id and recipient_id=p_agent_id for update;
 if not found then raise insufficient_privilege using message='update_notice_denied'; end if;
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 select * into target from quorum_private.update_targets where id=n.target_id and quorum_id=q;
 if not found then raise insufficient_privilege; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 if p_event not in ('refreshed','active','blocked') or p_event is null then raise exception 'update_event_invalid'; end if;
 expected:=jsonb_build_object('protocol_version',target.protocol_version, -- #172
  'content_sha256',target.content_sha256);
 if p_event='blocked' then
  if evidence->>'code' is null or evidence->>'code' not in ('update_tool_missing','update_fetch_failed', -- #172
   'update_invalid_copy','update_validation_failed','update_cache_conflict','update_target_changed',
   'update_in_progress','update_unsafe_cache') then raise exception 'update_evidence_invalid'; end if;
  expected:=jsonb_build_object('code',evidence->>'code'); -- #172
 end if;
 if p_event='active' then
  if evidence->>'run_id' is null or evidence->>'run_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then -- #172
   raise exception 'update_evidence_invalid'; end if;
  expected:=expected||jsonb_build_object('run_id',evidence->>'run_id'); -- #172
  if public.session_requirements()<>expected-'content_sha256'::text-'run_id'::text then raise exception 'skill_update_required'; end if;
 end if;
 if evidence is distinct from expected then raise exception 'update_evidence_invalid'; end if; -- #172
 select * into previous from quorum_private.update_reports where notice_id=n.id and request_id=p_request_id;
 if found then
  if previous.event<>p_event or previous.evidence<>evidence then raise exception 'idempotency_conflict'; end if; -- #172
  return jsonb_build_object('recorded',true);
 end if;
 insert into quorum_private.update_reports values(n.id,p_request_id,p_event,evidence); -- #172
 update quorum_private.update_notices set received_at=coalesce(received_at,now()),
  refreshed_at=case when p_event='refreshed' then coalesce(refreshed_at,now()) else refreshed_at end,
  active_at=case when p_event='active' then now() else active_at end,
  runtime_evidence=case when p_event='active' then evidence||jsonb_build_object('reported_at',now()) else runtime_evidence end, -- #172
  blocked_code=case when p_event='blocked' then evidence->>'code' else null end -- #172
 where id=n.id;
 return jsonb_build_object('recorded',true);
end $_$;

CREATE FUNCTION public.session_update_request(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_protocol_version integer, p_skill_version text DEFAULT NULL::text, p_content_sha256 text DEFAULT NULL::text, p_request_id text DEFAULT NULL::text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare q uuid; target quorum_private.update_targets%rowtype; op quorum_private.update_operations%rowtype;
 peer record; nid uuid; sent jsonb; failure text; queued_count integer:=0; reused integer:=0; failed_count integer:=0;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Match close's lock order before ownership, membership, target and message locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege using message='session_binding_denied'; end if;
 if p_protocol_version is null or p_protocol_version<1 -- #172: p_skill_version is never read
  or p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'update_target_invalid'; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 perform pg_advisory_xact_lock(hashtextextended('update-request:'||p_agent_id||':'||p_request_id,0));
 select * into op from quorum_private.update_operations where initiator_id=p_agent_id and request_id=p_request_id;
 if found then
  perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
  select * into target from quorum_private.update_targets where id=op.target_id;
  if target.quorum_id<>q or target.protocol_version<>p_protocol_version -- #172
   or target.content_sha256<>p_content_sha256 then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('operation_id',op.id,'queued',op.queued,'existing',op.existing,'failed',op.failed);
 end if;
 perform pg_advisory_xact_lock(hashtextextended('update-target:'||q||':'||p_content_sha256,0));
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 insert into quorum_private.update_targets(quorum_id,protocol_version,content_sha256) -- #172
 values(q,p_protocol_version,p_content_sha256) on conflict do nothing; -- #172
 select * into target from quorum_private.update_targets where quorum_id=q and content_sha256=p_content_sha256;
 if target.protocol_version<>p_protocol_version then -- #172
  raise exception 'update_target_conflict'; end if;
 insert into quorum_private.update_operations(initiator_id,target_id,request_id)
 values(p_agent_id,target.id,p_request_id) returning * into op;
 -- This snapshot includes offline and same-role peers in every project/engine;
 -- unlike session_peers it has no health filter or arbitrary 128-participant cap.
 for peer in select a.id from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  where a.quorum_id=q and a.id<>p_agent_id and not a.revoked and not d.revoked and not m.revoked order by a.id
 loop
  select id into nid from quorum_private.update_notices where target_id=target.id and recipient_id=peer.id;
  failure:=null;
  if nid is not null then reused:=reused+1;
  else
   begin
    sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
     'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
     'body','Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
      ||'no installation, lifecycle or task permission is granted. Target protocol '||p_protocol_version
      ||', sha256 '||p_content_sha256||'.','priority','normal'), -- #172
     'update:'||target.id||':'||peer.id);
    update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
    insert into quorum_private.update_notices(target_id,recipient_id,message_id)
    values(target.id,peer.id,(sent->>'message_id')::uuid) returning id into nid;
    queued_count:=queued_count+1;
   exception when insufficient_privilege then failure:='recipient_unavailable'; failed_count:=failed_count+1;
    when raise_exception then
     if sqlerrm<>'recipient_unavailable' then raise; end if;
     failure:='recipient_unavailable'; failed_count:=failed_count+1;
   end;
  end if;
  insert into quorum_private.update_recipients values(op.id,peer.id,nid,failure);
 end loop;
 update quorum_private.update_operations set queued=queued_count,existing=reused,failed=failed_count,
  unaddressable_members=(select count(*) from quorum_private.memberships m where m.quorum_id=q and not m.revoked
   and not exists(select 1 from quorum_private.agents a join quorum_private.devices d on d.id=a.device_id
    where a.quorum_id=q and a.owner_id=m.user_id and not a.revoked and not d.revoked))
 where id=op.id;
 return jsonb_build_object('operation_id',op.id,'queued',queued_count,'existing',reused,'failed',failed_count);
end $_$;

CREATE FUNCTION public.session_update_status(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_operation_id uuid DEFAULT NULL::uuid, p_notice_id uuid DEFAULT NULL::uuid, p_after_agent uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 return quorum_private.session_update_status_before_hash_only(p_agent_id,p_session_ref,p_quorum_id,
  p_operation_id,p_notice_id,p_after_agent)#-'{target,skill_version}';
end $$;

CREATE FUNCTION public.work_admit(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('work_admit',jsonb_build_object('quorum_id',p_quorum_id,'work_id',p_work_id,'agent_id',p_agent_id),p_request_id) $$;

CREATE FUNCTION public.work_register(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('work_register',jsonb_build_object('quorum_id',p_quorum_id,'repository',p_repository,'issue_number',p_issue_number,'human_owner_id',p_human_owner_id,'criteria',p_criteria,'dependencies',p_dependencies),p_request_id) $$;

CREATE FUNCTION public.work_transition(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$ select quorum_private.call('work_transition',jsonb_build_object('assignment_id',p_assignment_id,'generation',p_generation,'event',p_event,'evidence_ref',p_evidence_ref),p_request_id) $$;

CREATE FUNCTION quorum_private.call(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare
 who uuid:=auth.uid(); q uuid; result jsonb; previous record; replayed boolean; core jsonb;
 want_role text; want_project text; supersede boolean; keep uuid; own uuid[];
 invitation quorum_private.id_invitations%rowtype; membership quorum_private.memberships%rowtype;
 agent quorum_private.agents%rowtype; fingerprint text;
begin
 if op not in ('invite_create_id','invite_create_open','invite_accept_id','invite_accept','agent_join','agent_retire') then
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
 if op in ('invite_create_id','invite_create_open') then
  -- The core's own request key, taken before the replay probe: a concurrent identical
  -- request waits here until this one commits, then sees its request row and stored
  -- assignment. The key is shared only by identical requests (close takes no such
  -- key), so taking it before the core's quorum row lock creates no cycle.
  perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
  replayed:=exists(select 1 from quorum_private.requests r where r.actor=who and r.operation=op and r.request_id=req);
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

 elsif op in ('invite_accept_id','invite_accept') then
  -- quorum_id never changes after insert; the core relocks and rechecks the invitation.
  if op='invite_accept_id' then
   select * into invitation from quorum_private.id_invitations where id=(p->>'invite_id')::uuid;
   q:=invitation.quorum_id;
  else
   select i.quorum_id into q from quorum_private.invitations i
    where i.token_hash=encode(sha256(convert_to(p->>'token','UTF8')),'hex');
  end if;
  -- Fail closed: an invitation that this read did not see is refused here, never left
  -- for the core to find later without the reactivation checks below.
  if q is null then raise insufficient_privilege using message='invite_denied'; end if;
  -- Lock order, shared with close serialization and maintenance: quorum row, then
  -- agent rows (ascending ID), then the membership row. One key per accepting identity
  -- and quorum first serializes its acceptances; only acceptances take that key, so a
  -- waiter on it holds no row lock.
  perform pg_advisory_xact_lock(hashtextextended(who::text||'/accept/'||q::text,4));
  perform 1 from quorum_private.quorums where id=q for share;
  replayed:=exists(select 1 from quorum_private.requests r where r.actor=who and r.operation=op and r.request_id=req);
  if op='invite_accept_id' then
   select * into invitation from quorum_private.id_invitations where id=(p->>'invite_id')::uuid; end if;
  perform a.id from quorum_private.agents a where a.quorum_id=q and a.owner_id=who order by a.id for update of a;
  -- FOR UPDATE waits for any in-flight member_revoke or close and reads its committed
  -- result, so this decision and the core's upsert see the same membership state.
  select * into membership from quorum_private.memberships m where m.quorum_id=q and m.user_id=who for update;
  -- Reactivating a revoked membership must not revive its old agents next to a
  -- holder that claimed their role meanwhile. A revoked membership's agents are
  -- stale, so all of them are retired exactly like agent_leave, in the transaction
  -- that reactivates it: every reader sees either a revoked membership or retired
  -- agents, never live agents of a live membership it did not see claim the role.
  -- Any refusal by the core rolls this back; the member reconnects with a new agent.
  if found and membership.revoked then
   update quorum_private.agents set revoked=true where quorum_id=q and owner_id=who and not revoked;
  end if;
  result:=quorum_private.call_role_core(op,p,req);
  if op='invite_accept' then return result; end if;
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
  -- Every path to the core's insert runs the checks below under the locks; a refusal is
  -- raised here with the core's own codes and never delegated after an unlocked read.
  if not quorum_private.verified_actor(who) then
   raise insufficient_privilege using message='github_identity_required'; end if;
  -- One key per quorum, project and role serializes concurrent claims; it is
  -- always taken before the core's request lock.
  perform pg_advisory_xact_lock(hashtextextended(q::text||'/'||coalesce(p->>'project_ref','')||'/'
   ||coalesce(p->>'role',''),3));
  -- Every read below happens after the wait. Own holders are locked before the
  -- membership (agent before membership, as acceptance and maintenance lock); the
  -- membership row is held FOR SHARE so its assignment cannot change and it cannot be
  -- revoked or reactivated before the core inserts the agent.
  perform a.id from quorum_private.agents a where a.quorum_id=q and a.owner_id=who
   and a.role=p->>'role' and a.project_ref=p->>'project_ref' and not a.revoked order by a.id for update of a;
  -- The row is locked whatever its state, so a reactivation in flight is waited for and
  -- its committed result is read here (a revoked filter would skip the row unlocked).
  select * into membership from quorum_private.memberships m where m.quorum_id=q and m.user_id=who for share;
  -- Non-members never learn who holds a role.
  if not found or membership.revoked then raise insufficient_privilege using message='membership_required'; end if;
  if not (exists(select 1 from quorum_private.agents a where a.quorum_id=q
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
  -- The replay probe below runs only after the request lock, like the core's.
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
end $_$;

CREATE FUNCTION quorum_private.call_invite_core(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare
 who uuid:=auth.uid(); q uuid; membership uuid; result jsonb; previous record;
 invitation quorum_private.id_invitations%rowtype;
 fingerprint text:=encode(sha256(convert_to(p::text,'UTF8')),'hex');
begin
 if who is null then raise insufficient_privilege using message='auth_required'; end if;
 if not quorum_private.verified_actor(who) then
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
end $_$;

CREATE FUNCTION quorum_private.call_legacy_core(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare
 who uuid:=auth.uid(); q uuid; id uuid; aid uuid; did uuid; seq bigint; role_name text;
 result jsonb; previous record; invite quorum_private.invitations%rowtype;
 delivery quorum_private.deliveries%rowtype; msg quorum_private.messages%rowtype;
 agent quorum_private.agents%rowtype; conv quorum_private.conversations%rowtype;
 work quorum_private.work_items%rowtype; reservation quorum_private.capacity_reservations%rowtype;
 fingerprint text:=encode(sha256(convert_to(p::text,'UTF8')),'hex'); tok text;
 read_op bool:=op in ('context_read','agent_status','conversation_read'); ev text;
begin
 if who is null then raise insufficient_privilege using message='auth_required'; end if;
 if not quorum_private.verified_actor(who) then
   raise insufficient_privilege using message='github_identity_required'; end if;
 if octet_length(p::text)>131072 then raise exception 'payload_too_large'; end if;
 -- Resolve authorization from canonical rows, not the requested sender/tenant alone.
 if op in ('agent_join','invite_create','member_revoke','context_read','work_register','work_admit') then
   q:=(p->>'quorum_id')::uuid; role_name:=quorum_private.member(q,who);
 elsif op='conversation_read' then
   q:=quorum_private.owned_agent((p->>'agent_id')::uuid,who);
   if q<>(p->>'quorum_id')::uuid then raise insufficient_privilege; end if;
   if not exists(select 1 from quorum_private.conversations where conversations.id=(p->>'conversation_id')::uuid and quorum_id=q and (sender_id=(p->>'agent_id')::uuid or recipient_id=(p->>'agent_id')::uuid)) then raise insufficient_privilege; end if;
 elsif op='message_send' then
   q:=quorum_private.owned_agent((p->>'sender_agent_id')::uuid,who);
   if q<>(p->>'quorum_id')::uuid then raise insufficient_privilege; end if;
 elsif op in ('agent_status','agent_leave') then
   q:=quorum_private.owned_agent((p->>'agent_id')::uuid,who);
 elsif op in ('attempt_update','delivery_complete') then
   select d.* into delivery from quorum_private.deliveries d join quorum_private.attempts a on a.id=d.attempt_id
   join quorum_private.devices dev on dev.id=a.device_id
   where a.id=(p->>'attempt_id')::uuid and dev.owner_id=who and not dev.revoked for update of d;
   if not found then raise insufficient_privilege using message='attempt_denied'; end if;
   q:=quorum_private.owned_agent(delivery.recipient_id,who);
   if delivery.generation<>(p->>'generation')::bigint then raise exception 'stale_generation'; end if;
 elsif op='work_transition' then
   select * into reservation from quorum_private.capacity_reservations where capacity_reservations.id=(p->>'assignment_id')::uuid for update;
   if not found then raise exception 'assignment_missing'; end if;
   q:=reservation.quorum_id; perform quorum_private.member(q,who);
   if reservation.generation<>(p->>'generation')::bigint then raise exception 'stale_generation'; end if;
 elsif op='invite_accept' then
   select * into invite from quorum_private.invitations where token_hash=encode(sha256(convert_to(p->>'token','UTF8')),'hex') for update;
   if not found or invite.revoked or invite.expires_at<=now() then raise insufficient_privilege using message='invite_denied'; end if;
   if not exists(select 1 from auth.identities where user_id=who and provider='github' and provider_id=invite.target_github_id) then
     raise insufficient_privilege using message='invite_target_mismatch'; end if;
   q:=invite.quorum_id;
 elsif op='inbox_claim' then
   if not exists(select 1 from quorum_private.devices where devices.id=(p->>'device_id')::uuid and owner_id=who and not revoked) then
     raise insufficient_privilege using message='device_denied'; end if;
 elsif op not in ('quorum_create','device_register') then raise exception 'unknown_operation';
 end if;
 if op in ('invite_create','member_revoke','work_register') and role_name<>'owner' then
   raise insufficient_privilege using message='owner_required'; end if;
 if not read_op then
   if req is null or length(req) not between 1 and 128 then raise exception 'request_id_required'; end if;
   perform pg_advisory_xact_lock(hashtextextended(who::text||op||req,0));
   select * into previous from quorum_private.requests where actor=who and operation=op and request_id=req;
   if found then
     if previous.fingerprint<>fingerprint then raise exception 'idempotency_conflict'; end if;
     -- Claims remain revocable even when replaying the original response.
     if op='inbox_claim' and previous.result is not null then
       perform quorum_private.owned_agent((previous.result->'delivery'->>'recipient_agent_id')::uuid,who);
     elsif q is not null and op<>'invite_accept' then perform quorum_private.member(q,who); end if;
     return previous.result;
   end if;
 end if;
 case op
 when 'quorum_create' then
   insert into quorum_private.quorums(slug,owner_id) values(p->>'slug',who) returning quorums.id into q;
   insert into quorum_private.memberships(quorum_id,user_id,role) values(q,who,'owner') returning memberships.id into id;
   result:=jsonb_build_object('quorum_id',q,'membership_id',id);
 when 'device_register' then
   insert into quorum_private.devices(owner_id,label) values(who,p->>'label') returning devices.id into did;
   result:=jsonb_build_object('device_id',did);
 when 'invite_create' then
   tok:=encode(extensions.gen_random_bytes(32),'hex');
   insert into quorum_private.invitations(quorum_id,target_github_id,token_hash)
   values(q,p->>'target_github_id',encode(sha256(convert_to(tok,'UTF8')),'hex')) returning * into invite;
   result:=jsonb_build_object('invite_id',invite.id,'token',tok,'expires_at',invite.expires_at);
 when 'invite_accept' then
   if invite.accepted_by is not null then raise insufficient_privilege using message='invite_consumed'; end if;
   insert into quorum_private.memberships(quorum_id,user_id,role) values(q,who,'member')
   on conflict(quorum_id,user_id) do update set revoked=false returning memberships.id into id;
   update quorum_private.invitations set accepted_by=who where invitations.id=invite.id;
   result:=jsonb_build_object('quorum_id',q,'membership_id',id);
 when 'member_revoke' then
   if exists(select 1 from quorum_private.memberships where memberships.id=(p->>'member_id')::uuid and quorum_id=q and role='owner') then
     raise exception 'cannot_revoke_owner'; end if;
   update quorum_private.memberships set revoked=true where memberships.id=(p->>'member_id')::uuid and quorum_id=q;
   result:=jsonb_build_object('revoked_at',now());
 when 'agent_join' then
   if not exists(select 1 from quorum_private.devices where devices.id=(p->>'device_id')::uuid and owner_id=who and not revoked) then raise insufficient_privilege; end if;
   insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
   values(q,(p->>'device_id')::uuid,who,p->>'project_ref',p->>'role',p->>'engine',p->>'session_ref')
   on conflict(quorum_id,device_id,session_ref) do nothing returning agents.id into aid;
   if aid is null then
     select * into agent from quorum_private.agents where quorum_id=q and device_id=(p->>'device_id')::uuid and session_ref=p->>'session_ref';
     if agent.revoked or agent.role<>p->>'role' or agent.engine<>p->>'engine' or agent.project_ref<>p->>'project_ref' then raise exception 'binding_conflict'; end if;
     aid:=agent.id;
   end if;
   result:=jsonb_build_object('agent_id',aid);
 when 'agent_status' then
   result:=jsonb_build_object('agent_id',p->>'agent_id','registered',true);
 when 'agent_leave' then
   update quorum_private.agents set revoked=true where agents.id=(p->>'agent_id')::uuid;
   result:=jsonb_build_object('left',true);
 when 'context_read' then
   select coalesce(jsonb_agg(row_to_json(x)),'[]') into result from
   (select context_versions.id,body,author_id,source_ref from quorum_private.context_versions where quorum_id=q and project_ref=p->>'project_ref' and context_versions.id>coalesce((p->>'after_version')::bigint,0) order by context_versions.id limit 3) x;
 when 'conversation_read' then
   select coalesce(jsonb_agg(row_to_json(x)),'[]') into result from (select messages.id,sequence,body,sender_id,recipient_id,reply_to from quorum_private.messages where conversation_id=(p->>'conversation_id')::uuid and quorum_id=q and sequence>(p->>'after_sequence')::bigint order by sequence limit 16) x;
 when 'message_send' then
   select * into agent from quorum_private.agents where agents.id=(p->>'recipient_agent_id')::uuid and quorum_id=q and not revoked;
   if not found then raise exception 'recipient_unavailable'; end if;
   perform quorum_private.owned_agent(agent.id,agent.owner_id);
   insert into quorum_private.conversations(id,quorum_id,sender_id,recipient_id)
   values((p->>'conversation_id')::uuid,q,(p->>'sender_agent_id')::uuid,agent.id) on conflict do nothing;
   select * into conv from quorum_private.conversations where conversations.id=(p->>'conversation_id')::uuid for update;
   if conv.quorum_id<>q or not (((p->>'sender_agent_id')::uuid=conv.sender_id and agent.id=conv.recipient_id)
     or ((p->>'sender_agent_id')::uuid=conv.recipient_id and agent.id=conv.sender_id)) then raise insufficient_privilege; end if;
   if p->>'reply_to' is not null and not exists(select 1 from quorum_private.messages where messages.id=(p->>'reply_to')::uuid and conversation_id=conv.id) then raise exception 'invalid_reply'; end if;
   update quorum_private.conversations set next_sequence=next_sequence+1 where conversations.id=conv.id returning next_sequence into seq;
   insert into quorum_private.messages(quorum_id,conversation_id,sender_id,recipient_id,reply_to,sequence,body)
   values(q,conv.id,(p->>'sender_agent_id')::uuid,agent.id,(p->>'reply_to')::uuid,seq,p->>'body') returning messages.id into id;
   insert into quorum_private.deliveries(quorum_id,message_id,recipient_id) values(q,id,agent.id) returning deliveries.id into did;
   result:=jsonb_build_object('message_id',id,'delivery_id',did,'sequence',seq);
 when 'inbox_claim' then
   -- Lost leases hold capacity and require reconciliation. Never infer nonlaunch.
   update quorum_private.deliveries d set state='unknown' from quorum_private.agents a
   where d.recipient_id=a.id and a.device_id=(p->>'device_id')::uuid and d.lease_until<now()
   and d.state in ('claimed','launch_intent','running','cancel_requested');
   select d.* into delivery from quorum_private.deliveries d join quorum_private.agents a on a.id=d.recipient_id
   join quorum_private.memberships m on m.quorum_id=d.quorum_id and m.user_id=who
   where a.device_id=(p->>'device_id')::uuid and a.owner_id=who and not a.revoked and not m.revoked and d.state='queued' and d.requires_handler
   and not exists(select 1 from quorum_private.messages current_msg
     join quorum_private.messages earlier on earlier.conversation_id=current_msg.conversation_id and earlier.sequence<current_msg.sequence
     join quorum_private.deliveries pending on pending.message_id=earlier.id
     where current_msg.id=d.message_id and pending.requires_handler and pending.state not in ('answered','cancelled'))
   order by d.created_at,d.id for update of d skip locked limit 1;
   if not found then result:=null;
   else
     q:=delivery.quorum_id;
     perform pg_advisory_xact_lock(hashtextextended((p->>'device_id'),1));
     if exists(select 1 from quorum_private.deliveries d join quorum_private.agents a on a.id=d.recipient_id
       where a.device_id=(p->>'device_id')::uuid and d.state in ('claimed','launch_intent','running','unknown','cancel_requested')) then result:=null;
     else
       -- Serialize conversations and bound overall incoming work.
       perform pg_advisory_xact_lock(hashtextextended(q::text,2));
       if (select count(*) from quorum_private.deliveries where quorum_id=q and state in ('claimed','launch_intent','running','unknown','cancel_requested'))>=2 then result:=null;
       else
         select * into msg from quorum_private.messages where messages.id=delivery.message_id;
         if exists(select 1 from quorum_private.deliveries d join quorum_private.messages m on m.id=d.message_id
           where m.conversation_id=msg.conversation_id and m.sequence<msg.sequence and d.requires_handler and d.state not in ('answered','cancelled')) then result:=null;
         else
           aid:=gen_random_uuid();
           update quorum_private.deliveries set state='claimed',attempt_id=aid,lease_until=now()+interval '60 seconds' where deliveries.id=delivery.id;
           insert into quorum_private.attempts values(aid,delivery.id,delivery.generation,(p->>'device_id')::uuid);
           result:=jsonb_build_object('attempt_id',aid,'generation',delivery.generation,'lease_until',now()+interval '60 seconds',
             'delivery',jsonb_build_object('conversation_id',msg.conversation_id,'body',msg.body,'recipient_agent_id',delivery.recipient_id));
         end if;
       end if;
     end if;
   end if;
 when 'attempt_update' then
   ev:=p->>'event';
   if ev='heartbeat' and delivery.state in ('claimed','launch_intent','running') and delivery.lease_until>now() then
     update quorum_private.deliveries set lease_until=now()+interval '60 seconds' where deliveries.id=delivery.id;
   elsif (ev='launch_intent' and delivery.state='claimed') or (ev='started' and delivery.state='launch_intent')
      or (ev='unknown' and delivery.state in ('claimed','launch_intent','running','cancel_requested'))
      or (ev='cancel_requested' and delivery.state in ('claimed','launch_intent','running'))
      or (ev='stopped' and delivery.state='cancel_requested') then
     update quorum_private.deliveries set state=case ev when 'started' then 'running' when 'stopped' then 'cancelled' else ev end where deliveries.id=delivery.id;
   else raise exception 'invalid_attempt_transition'; end if;
   result:=jsonb_build_object('state',(select state from quorum_private.deliveries where deliveries.id=delivery.id));
 when 'delivery_complete' then
   if delivery.state<>'running' or delivery.lease_until<=now() then raise exception 'completion_unconfirmed'; end if;
   select * into msg from quorum_private.messages where messages.id=delivery.message_id;
   update quorum_private.conversations set next_sequence=next_sequence+1 where conversations.id=msg.conversation_id returning next_sequence into seq;
   insert into quorum_private.messages(quorum_id,conversation_id,sender_id,recipient_id,reply_to,sequence,body)
   values(q,msg.conversation_id,msg.recipient_id,msg.sender_id,msg.id,seq,p->>'reply_body') returning messages.id into id;
   insert into quorum_private.deliveries(quorum_id,message_id,recipient_id,requires_handler) values(q,id,msg.sender_id,false);
   update quorum_private.deliveries set state='answered' where deliveries.id=delivery.id;
   result:=jsonb_build_object('reply_message_id',id);
 when 'work_register' then
   if not exists(select 1 from quorum_private.quorums where quorums.id=q and allowlisted_repo=p->>'repository') then raise exception 'scratch_repository_required'; end if;
   perform quorum_private.member(q,(p->>'human_owner_id')::uuid);
   if jsonb_typeof(p->'dependencies')<>'array' then raise exception 'invalid_dependencies'; end if;
   insert into quorum_private.work_items(quorum_id,repository,issue_number,human_owner_id,criteria,dependencies)
   values(q,p->>'repository',(p->>'issue_number')::bigint,(p->>'human_owner_id')::uuid,p->>'criteria',p->'dependencies') returning work_items.id into id;
   result:=jsonb_build_object('work_id',id);
 when 'work_admit' then
   if quorum_private.owned_agent((p->>'agent_id')::uuid,who)<>q then raise insufficient_privilege; end if;
   perform 1 from quorum_private.quorums where quorums.id=q for update;
   select * into work from quorum_private.work_items where work_items.id=(p->>'work_id')::uuid and quorum_id=q;
   if not found or work.human_owner_id<>who then raise insufficient_privilege; end if;
   if exists(select 1 from jsonb_array_elements_text(work.dependencies) dep where not exists
     (select 1 from quorum_private.capacity_reservations r where r.work_id=dep::uuid and r.quorum_id=q and r.state='accepted')) then raise exception 'dependency_not_accepted'; end if;
   if exists(select 1 from quorum_private.capacity_reservations where work_id=work.id) then raise exception 'already_assigned'; end if;
   if (select count(*) from quorum_private.capacity_reservations where quorum_id=q and state not in ('accepted','cancelled')) >= (select capacity from quorum_private.quorums where quorums.id=q) then raise exception 'capacity_full'; end if;
   insert into quorum_private.capacity_reservations(quorum_id,work_id,agent_id) values(q,work.id,(p->>'agent_id')::uuid) returning capacity_reservations.id into id;
   result:=jsonb_build_object('assignment_id',id,'generation',1);
 when 'work_transition' then
   ev:=p->>'event';
   select * into agent from quorum_private.agents where agents.id=reservation.agent_id;
   if ev in ('review_pass','qa_pass','qa_fail') then
     if agent.owner_id=who then raise insufficient_privilege using message='independent_verifier_required'; end if;
   elsif agent.owner_id<>who then raise insufficient_privilege; end if;
   if reservation.state in ('accepted','cancelled') then raise exception 'assignment_closed'; end if;
   if ev in ('submit','new_head') and (p->>'evidence_ref') ~ '^[0-9a-f]{40}$' then
     update quorum_private.capacity_reservations set state='review',head_sha=p->>'evidence_ref',reviewed_sha=null,reviewer_id=null where capacity_reservations.id=reservation.id;
   elsif ev='review_pass' and reservation.state='review' and reservation.head_sha=p->>'evidence_ref' then
     update quorum_private.capacity_reservations set reviewed_sha=head_sha,reviewer_id=who where capacity_reservations.id=reservation.id;
   elsif ev='merge' and reservation.state='review' and reservation.reviewed_sha=reservation.head_sha and reservation.head_sha=p->>'evidence_ref' then
     update quorum_private.capacity_reservations set state='qa_pending' where capacity_reservations.id=reservation.id;
   elsif ev in ('qa_pass','qa_fail') and reservation.state='qa_pending' and length(p->>'evidence_ref') between 1 and 2048 then
     update quorum_private.capacity_reservations set state=case ev when 'qa_pass' then 'accepted' else 'changes_requested' end,evidence=p->>'evidence_ref' where capacity_reservations.id=reservation.id;
   else raise exception 'invalid_work_transition'; end if;
   result:=jsonb_build_object('state',(select state from quorum_private.capacity_reservations where capacity_reservations.id=reservation.id));
 end case;
 if not read_op then
   insert into quorum_private.requests values(who,op,req,fingerprint,
     case when op='invite_create' then (result-'token')||jsonb_build_object('code','token_unavailable') else result end);
   insert into quorum_private.audit_events(actor,operation,quorum_id) values(who,op,q);
 end if;
 return result;
end $_$;

CREATE FUNCTION quorum_private.call_priority_core(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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
end $$;

CREATE FUNCTION quorum_private.call_role_core(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare who uuid:=auth.uid(); q uuid; is_open boolean;
begin
 -- Requests the core rejects before reading a row take no lock here, so their
 -- errors and precedence are unchanged.
 if op not in ('quorum_set_access','invite_create_open','invite_accept_id') or who is null
    or p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072
    or req is null or length(req) not between 1 and 128 then
  return quorum_private.call_serial_core(op,p,req);
 end if;
 -- The core's fresh-statement rereads need READ COMMITTED; see the header.
 if current_setting('transaction_isolation')<>'read committed' then
  raise exception 'open_access_requires_read_committed';
 end if;
 if op='invite_accept_id' then
  -- quorum_id and open never change after insert; the core relocks the row.
  select i.quorum_id,i.open into q,is_open from quorum_private.id_invitations i
   where i.id=(p->>'invite_id')::uuid;
  if is_open then perform 1 from quorum_private.quorums where id=q for share; end if;
 elsif exists(select 1 from auth.identities where user_id=who and provider='github') then
  q:=(p->>'quorum_id')::uuid;
  -- Only the owner reaches the core's writes; nobody else may hold this lock.
  if exists(select 1 from quorum_private.memberships
            where quorum_id=q and user_id=who and role='owner' and not revoked) then
   if op='quorum_set_access' then
    perform 1 from quorum_private.quorums where id=q for no key update;
   else
    perform 1 from quorum_private.quorums where id=q for share;
   end if;
  end if;
 end if;
 return quorum_private.call_serial_core(op,p,req);
end $$;

CREATE FUNCTION quorum_private.call_serial_core(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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
end $$;

CREATE FUNCTION quorum_private.delivery_status_answered() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare m quorum_private.messages%rowtype;
begin
 select * into m from quorum_private.messages where id=new.message_id;
 if found and quorum_private.delivery_status_applies(m.id) then
  perform quorum_private.delivery_status_apply(m,'answered',null,null); end if;
 return new;
exception when others then return new;
end $$;

CREATE FUNCTION quorum_private.delivery_status_applies(p_message_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select not exists(select 1 from quorum_private.session_notices n where n.message_id=p_message_id)
  and not exists(select 1 from quorum_private.update_notices u where u.message_id=p_message_id)
$$;

CREATE TABLE quorum_private.messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    conversation_id uuid,
    sender_id uuid,
    recipient_id uuid,
    reply_to uuid,
    sequence bigint NOT NULL,
    body text NOT NULL,
    priority text DEFAULT 'normal'::text NOT NULL,
    CONSTRAINT messages_body_check CHECK ((octet_length(body) <= 16384)),
    CONSTRAINT messages_priority_check CHECK ((priority = ANY (ARRAY['normal'::text, 'urgent'::text])))
);

CREATE FUNCTION quorum_private.delivery_status_apply(p_message quorum_private.messages, p_status text, p_reason text, p_proof text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare next_seq bigint; was quorum_private.delivery_statuses%rowtype; now_is quorum_private.delivery_statuses%rowtype;
 fresh boolean:=false; moved boolean;
begin
 -- Held to commit: it orders this sender's sequences and any first insert of a row.
 insert into quorum_private.delivery_status_streams as stream(sender_id,last_seq) values(p_message.sender_id,1)
  on conflict(sender_id) do update set last_seq=stream.last_seq+1 returning last_seq into next_seq;
 select * into was from quorum_private.delivery_statuses where message_id=p_message.id;
 if not found then
  fresh:=true;
  was.message_id:=p_message.id; was.quorum_id:=p_message.quorum_id; was.sender_id:=p_message.sender_id;
  was.recipient_id:=p_message.recipient_id; was.status:='sent'; was.status_at:=now();
 end if;
 now_is:=was;
 if p_status='queued' then
  now_is.queued_at:=coalesce(was.queued_at,now());
  if was.status='sent' then now_is.status:='queued'; end if;
 elsif p_status='read' then
  now_is.read_at:=coalesce(was.read_at,now());
  now_is.proof:=coalesce(was.proof,p_proof);
  if was.status in ('sent','queued') then now_is.status:='read'; end if;
 elsif p_status='answered' then
  now_is.answered_at:=coalesce(was.answered_at,now());
  if was.status not in ('declined','done','failed') then now_is.status:='answered'; now_is.reason:=null; end if;
 elsif was.status in ('declined','done','failed') then
  if was.status<>p_status then raise exception 'delivery_status_final'; end if;
  return;
 else
  now_is.status:=p_status; now_is.reason:=p_reason;
 end if;
 if not fresh and now_is is not distinct from was then return; end if;
 moved:=fresh or now_is.status<>was.status;
 if now_is.status<>was.status then now_is.status_at:=now(); end if;
 -- Only a new status enters the feed: each row there is one fact for the sender, and later times are not.
 if moved then now_is.seq:=next_seq; end if;
 insert into quorum_private.delivery_statuses values(now_is.*)
  on conflict(message_id) do update set status=excluded.status,status_at=excluded.status_at,reason=excluded.reason,
   proof=excluded.proof,queued_at=excluded.queued_at,read_at=excluded.read_at,answered_at=excluded.answered_at,
   seq=excluded.seq;
 if moved then
  begin
   perform realtime.send('{}'::jsonb,'status_changed','quorum:agent:'||p_message.sender_id::text,true);
  exception when others then null; -- A hint is never worth a durable status.
  end;
 end if;
end $$;

CREATE FUNCTION quorum_private.delivery_status_view(p_message_id uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select jsonb_build_object('message_id',m.id,'recipient_id',m.recipient_id,'recipient_role',a.role,
  'status',coalesce(s.status,'sent'),'status_at',coalesce(s.status_at,d.created_at),'sent_at',d.created_at,
  'queued_at',s.queued_at,'read_at',s.read_at,'answered_at',s.answered_at,'reason',s.reason,'proof',s.proof,
  'receipted',exists(select 1 from quorum_private.session_receipts r
                     where r.agent_id=m.recipient_id and r.message_id=m.id))
 from quorum_private.messages m
 join quorum_private.deliveries d on d.message_id=m.id
 join quorum_private.agents a on a.id=m.recipient_id
 left join quorum_private.delivery_statuses s on s.message_id=m.id
 where m.id=p_message_id
$$;

CREATE FUNCTION quorum_private.member(q uuid, who uuid) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare r text; begin
 select role into r from quorum_private.memberships where quorum_id=q and user_id=who and not revoked;
 if r is null then raise insufficient_privilege using message='membership_required'; end if;
 return r;
end $$;

CREATE FUNCTION quorum_private.native_session(p_agent_id uuid, p_session_ref text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid;
begin
 if auth.uid() is null or not quorum_private.verified_actor(auth.uid()) then
  raise insufficient_privilege using message='github_identity_required'; end if;
 q:=quorum_private.owned_agent(p_agent_id,auth.uid());
 if not exists(select 1 from quorum_private.agents where id=p_agent_id and engine in ('codex','claude','grok') and session_ref=p_session_ref) then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 return q;
end $$;

CREATE FUNCTION quorum_private.notify_inbox() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare recipient uuid;
begin
 if tg_table_name='session_receipts' then recipient:=new.agent_id;
 else recipient:=new.recipient_id; end if;
 perform realtime.send('{}'::jsonb,'inbox_changed','quorum:agent:'||recipient::text,true);
 return new;
exception when others then
 -- A missing daily Realtime partition or an unavailable broadcaster must never
 -- discard the durable inbox write. Clients reconcile on start/reconnect and
 -- periodically even when no hint was delivered.
 return new;
end $$;

CREATE FUNCTION quorum_private.open_invitation_ttl() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
begin
 new.expires_at:=now()+interval '60 minutes';
 return new;
end $$;

CREATE FUNCTION quorum_private.owned_agent(a uuid, who uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; begin
 select ag.quorum_id into q from quorum_private.agents ag join quorum_private.devices d on d.id=ag.device_id
 where ag.id=a and ag.owner_id=who and d.owner_id=who and not ag.revoked and not d.revoked;
 if q is null then raise insufficient_privilege using message='agent_denied'; end if;
 perform quorum_private.member(q,who); return q;
end $$;

CREATE FUNCTION quorum_private.receive_wakeup(p_topic text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select auth.uid() is not null
  and quorum_private.verified_actor(auth.uid())
  and exists(
   select 1 from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where p_topic='quorum:agent:'||a.id::text
    and a.owner_id=auth.uid() and d.owner_id=auth.uid()
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
  )
$$;

CREATE FUNCTION quorum_private.session_ack_before_updates(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id) then
  raise insufficient_privilege using message='message_denied'; end if;
 insert into quorum_private.session_receipts(agent_id,message_id) values(p_agent_id,p_message_id) on conflict do nothing;
 insert into quorum_private.session_health(agent_id,last_seen) values(p_agent_id,now())
 on conflict(agent_id) do update set last_seen=excluded.last_seen;
 return jsonb_build_object('queued',true);
end $$;

CREATE FUNCTION quorum_private.session_health_streak() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 if tg_op='INSERT' or old.last_seen is null or new.last_seen>=old.last_seen+interval '60 seconds' then
  -- No previous health, or a real absence: a new streak starts at this write.
  insert into quorum_private.session_streaks(agent_id,online_since) values(new.agent_id,new.last_seen)
  on conflict(agent_id) do update set online_since=excluded.online_since;
 else
  -- Continuous presence keeps the streak; an agent first seen by this release starts one now.
  insert into quorum_private.session_streaks(agent_id,online_since) values(new.agent_id,new.last_seen)
  on conflict(agent_id) do nothing;
 end if;
 return null;
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_before_hash_only(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text, p_awaiting_owner integer DEFAULT NULL::integer, p_reception_hold_until timestamp with time zone DEFAULT NULL::timestamp with time zone, p_client_run_id uuid DEFAULT NULL::uuid, p_runtime_started_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb;
begin
 -- Refused before the core runs, so an invalid status changes no health, connection or Hello.
 if p_awaiting_owner is not null and p_awaiting_owner not between 0 and 32 then
  raise exception 'invalid_peer_status'; end if;
 -- The reviewed heartbeat validates the exact owned binding and the release, and locks
 -- the agent's health row before this status write.
 result:=quorum_private.session_heartbeat_before_status(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 insert into quorum_private.session_status(agent_id,reported_at,awaiting_owner,reception_hold_until,
  client_run_id,runtime_started_at)
 values(p_agent_id,now(),p_awaiting_owner,
  case when p_reception_hold_until>now() then least(p_reception_hold_until,now()+interval '30 minutes') end,
  p_client_run_id,
  case when p_runtime_started_at is not null then least(p_runtime_started_at,now()) end)
 on conflict(agent_id) do update set reported_at=excluded.reported_at,awaiting_owner=excluded.awaiting_owner,
  reception_hold_until=excluded.reception_hold_until,client_run_id=excluded.client_run_id,
  runtime_started_at=excluded.runtime_started_at;
 return result;
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_before_hello(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; own quorum_private.agents%rowtype; peer record; fresh integer; sent jsonb; required jsonb;
 since timestamptz; skipped jsonb:='[]'::jsonb; -- #126
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 required:=public.session_requirements();
 if p_protocol_version is distinct from (required->>'protocol_version')::integer
  or p_skill_version is distinct from required->>'skill_version' then
  return jsonb_build_object('code','skill_update_required','required',required);
 end if;
 if p_connection_id is null then raise exception 'connection_id_required'; end if;
 select * into own from quorum_private.agents where id=p_agent_id;
 insert into quorum_private.session_health(agent_id,last_seen,protocol_version,skill_version)
 values(p_agent_id,now(),p_protocol_version,p_skill_version)
 on conflict(agent_id) do update set last_seen=excluded.last_seen,
  protocol_version=excluded.protocol_version,skill_version=excluded.skill_version;
 insert into quorum_private.session_connections values(p_agent_id,p_connection_id) on conflict do nothing;
 get diagnostics fresh=row_count;
 if fresh=1 then
  -- #126: the streak row was written by the health trigger above, under the health row lock.
  select online_since into since from quorum_private.session_streaks where agent_id=p_agent_id;
  for peer in
   select a.id from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
   order by a.id limit 128
  loop
   -- #126: already greeted within 30 minutes of this unbroken streak; a missing streak never skips.
   if since is not null and exists(select 1 from quorum_private.session_hello_sent s
     where s.sender_id=p_agent_id and s.recipient_id=peer.id
      and s.sent_at>now()-interval '30 minutes' and s.sent_at>=since) then
    skipped:=skipped||to_jsonb(peer.id);
    continue;
   end if;
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
    'body','Hello! Listener connected: '||own.role||' (project '||own.project_ref||'). Transport notice; no reply required.'),
    'hello:'||p_agent_id||':'||p_connection_id||':'||peer.id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   -- #126
   insert into quorum_private.session_hello_sent(sender_id,recipient_id,sent_at) values(p_agent_id,peer.id,now())
   on conflict(sender_id,recipient_id) do update set sent_at=excluded.sent_at;
  end loop;
 end if;
 return jsonb_build_object('online',true,'new_connection',fresh=1,'last_seen',now(),'required',required)
  -- #126: private to the Hello wrapper below, which removes it before anything returns to a client.
  ||case when fresh=1 then jsonb_build_object('hello_skipped',skipped) else '{}'::jsonb end;
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_before_presence(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text DEFAULT NULL::text, p_awaiting_owner integer DEFAULT NULL::integer, p_reception_hold_until timestamp with time zone DEFAULT NULL::timestamp with time zone, p_client_run_id uuid DEFAULT NULL::uuid, p_runtime_started_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 -- The reviewed heartbeat validates the binding, the protocol and the status, and writes everything.
 return quorum_private.session_heartbeat_before_hash_only(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,null,p_awaiting_owner,p_reception_hold_until,p_client_run_id,p_runtime_started_at);
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_before_status(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer; skipped jsonb; -- #126
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 -- #126: peers the core did not greet again on this connection; never returned to a client.
 skipped:=coalesce(result->'hello_skipped','[]'::jsonb);
 result:=result-'hello_skipped';
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles,skipped) -- #146
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb),jsonb_array_length(skipped) -- #146
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and (exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
     or skipped@>to_jsonb(array[a.id::text])) -- #126
   ) y
  ) x
  on conflict do nothing;
  get diagnostics created=row_count;
  if created=1 then
   select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
   select string_agg(r.value#>>'{}',', ' order by r.ordinality) into listing
    from jsonb_array_elements(hello.available_roles) with ordinality r;
   -- A self-addressed transport notice: never a task, never a reply chain.
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',p_agent_id,
    'conversation_id',md5('quorum-roster:'||p_agent_id||':'||p_connection_id)::uuid,'reply_to',null,
    'body',case when hello.available=0 then 'Quorum roster: no other native session is available in project '
      ||own.project_ref||'. Hello reached '||hello.peers||' registered peer(s). Transport notice; no reply required.'
     else 'Quorum roster: '||hello.available||' peer(s) available in project '||own.project_ref||': '||listing
      ||case when hello.available>16 then ', ...' else '' end
      ||'. Transport notice; no reply required.' end),
    'roster:'||p_agent_id||':'||p_connection_id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   update quorum_private.session_hellos set roster_message_id=(sent->>'message_id')::uuid
    where agent_id=p_agent_id and connection_id=p_connection_id;
  end if;
 end if;
 select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
 if not found then return result||jsonb_build_object('hello',null); end if;
 return result||jsonb_build_object('hello',jsonb_build_object('project_ref',own.project_ref,
  'peers',hello.peers,'skipped',hello.skipped,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles, -- #146
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_core_before_dedupe(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; own quorum_private.agents%rowtype; peer record; fresh integer; sent jsonb; required jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 required:=public.session_requirements();
 if p_protocol_version is distinct from (required->>'protocol_version')::integer
  or p_skill_version is distinct from required->>'skill_version' then
  return jsonb_build_object('code','skill_update_required','required',required);
 end if;
 if p_connection_id is null then raise exception 'connection_id_required'; end if;
 select * into own from quorum_private.agents where id=p_agent_id;
 insert into quorum_private.session_health(agent_id,last_seen,protocol_version,skill_version)
 values(p_agent_id,now(),p_protocol_version,p_skill_version)
 on conflict(agent_id) do update set last_seen=excluded.last_seen,
  protocol_version=excluded.protocol_version,skill_version=excluded.skill_version;
 insert into quorum_private.session_connections values(p_agent_id,p_connection_id) on conflict do nothing;
 get diagnostics fresh=row_count;
 if fresh=1 then
  for peer in
   select a.id from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.quorum_id=q and a.project_ref=own.project_ref and a.id<>p_agent_id
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
   order by a.id limit 128
  loop
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
    'body','Hello! Listener connected: '||own.role||' (project '||own.project_ref||'). Transport notice; no reply required.'),
    'hello:'||p_agent_id||':'||p_connection_id||':'||peer.id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
  end loop;
 end if;
 return jsonb_build_object('online',true,'new_connection',fresh=1,'last_seen',now(),'required',required);
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_hello_before_dedupe(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer;
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles)
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb)
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
   ) y
  ) x
  on conflict do nothing;
  get diagnostics created=row_count;
  if created=1 then
   select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
   select string_agg(r.value#>>'{}',', ' order by r.ordinality) into listing
    from jsonb_array_elements(hello.available_roles) with ordinality r;
   -- A self-addressed transport notice: never a task, never a reply chain.
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',p_agent_id,
    'conversation_id',md5('quorum-roster:'||p_agent_id||':'||p_connection_id)::uuid,'reply_to',null,
    'body',case when hello.available=0 then 'Quorum roster: no other native session is available in project '
      ||own.project_ref||'. Hello reached '||hello.peers||' registered peer(s). Transport notice; no reply required.'
     else 'Quorum roster: '||hello.available||' peer(s) available in project '||own.project_ref||': '||listing
      ||case when hello.available>16 then ', ...' else '' end
      ||'. Transport notice; no reply required.' end),
    'roster:'||p_agent_id||':'||p_connection_id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   update quorum_private.session_hellos set roster_message_id=(sent->>'message_id')::uuid
    where agent_id=p_agent_id and connection_id=p_connection_id;
  end if;
 end if;
 select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
 if not found then return result||jsonb_build_object('hello',null); end if;
 return result||jsonb_build_object('hello',jsonb_build_object('project_ref',own.project_ref,
  'peers',hello.peers,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles,
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $$;

CREATE FUNCTION quorum_private.session_heartbeat_hello_before_split(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer; skipped jsonb; -- #126
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 -- #126: peers the core did not greet again on this connection; never returned to a client.
 skipped:=coalesce(result->'hello_skipped','[]'::jsonb);
 result:=result-'hello_skipped';
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles)
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb)
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and (exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
     or skipped@>to_jsonb(array[a.id::text])) -- #126
   ) y
  ) x
  on conflict do nothing;
  get diagnostics created=row_count;
  if created=1 then
   select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
   select string_agg(r.value#>>'{}',', ' order by r.ordinality) into listing
    from jsonb_array_elements(hello.available_roles) with ordinality r;
   -- A self-addressed transport notice: never a task, never a reply chain.
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',p_agent_id,
    'conversation_id',md5('quorum-roster:'||p_agent_id||':'||p_connection_id)::uuid,'reply_to',null,
    'body',case when hello.available=0 then 'Quorum roster: no other native session is available in project '
      ||own.project_ref||'. Hello reached '||hello.peers||' registered peer(s). Transport notice; no reply required.'
     else 'Quorum roster: '||hello.available||' peer(s) available in project '||own.project_ref||': '||listing
      ||case when hello.available>16 then ', ...' else '' end
      ||'. Transport notice; no reply required.' end),
    'roster:'||p_agent_id||':'||p_connection_id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   update quorum_private.session_hellos set roster_message_id=(sent->>'message_id')::uuid
    where agent_id=p_agent_id and connection_id=p_connection_id;
  end if;
 end if;
 select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
 if not found then return result||jsonb_build_object('hello',null); end if;
 return result||jsonb_build_object('hello',jsonb_build_object('project_ref',own.project_ref,
  'peers',hello.peers,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles,
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $$;

CREATE FUNCTION quorum_private.session_inbox_before_hash_only(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare result jsonb;
begin
 select coalesce(jsonb_agg(item.value||case when n.id is null then '{}'::jsonb else jsonb_build_object('maintenance',
  jsonb_build_object('kind','quorum.update','notice_id',n.id,'target',jsonb_build_object('protocol_version',t.protocol_version,
   'skill_version',t.skill_version,'content_sha256',t.content_sha256))) end),'[]'::jsonb) into result
 from jsonb_array_elements(quorum_private.session_inbox_before_updates(p_agent_id,p_session_ref)) item
 left join quorum_private.update_notices n on n.message_id=(item.value->>'id')::uuid
 left join quorum_private.update_targets t on t.id=n.target_id;
 return result;
end $$;

CREATE FUNCTION quorum_private.session_inbox_before_updates(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body,
   m.priority,a.role as sender_role,
   exists(select 1 from quorum_private.session_notices n where n.message_id=m.id) as notification
  from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  join quorum_private.agents a on a.id=m.sender_id
  where m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)
  order by (m.priority='urgent') desc,d.created_at,m.conversation_id,m.sequence limit 1
 ) x;
 return result;
end $$;

CREATE FUNCTION quorum_private.session_peers_before_hash_only(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; project text; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select project_ref into project from quorum_private.agents where id=p_agent_id;
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select a.id as agent_id,a.role,a.engine,a.project_ref,h.last_seen,
   jsonb_build_object('protocol_version',h.protocol_version,'skill_version',h.skill_version) as client,
   public.session_requirements() as required,
   (h.protocol_version is distinct from (public.session_requirements()->>'protocol_version')::integer
    or h.skill_version is distinct from public.session_requirements()->>'skill_version') as update_required,
   case when k.restarted then 'client_restarted_unrecovered'
        when pending.oldest<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen is null then 'unknown'
        when k.current and s.awaiting_owner>0 then 'awaiting_owner' else 'online' end as availability,
   case when k.restarted then 'run_changed_without_heartbeat'
        when pending.oldest<=now()-interval '60 seconds' then
         case when h.last_seen>now()-interval '60 seconds' and k.current and s.reception_hold_until>now()
          then 'reception_hold' else 'receipt_timeout' end
        when h.last_seen<=now()-interval '60 seconds' then 'heartbeat_timeout'
        when h.last_seen is null then 'heartbeat_not_supported' else null end as reason,
   case when k.current then s.awaiting_owner end as awaiting_owner,
   case when k.current and s.reception_hold_until>now() then s.reception_hold_until end as reception_hold_until,
   s.client_run_id is not null as client_run_reported
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_health h on h.agent_id=a.id
  left join quorum_private.session_status s on s.agent_id=a.id
  left join lateral (
   select min(dv.created_at) as oldest from quorum_private.deliveries dv
   where dv.recipient_id=a.id and dv.state='queued'
    and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=a.id and r.message_id=dv.message_id)
  ) pending on true
  -- The newest runtime self-report of this exact agent; written only by session_update_report.
  left join lateral (
   select (n.runtime_evidence->>'run_id')::uuid as run_id,(n.runtime_evidence->>'reported_at')::timestamptz as reported_at
   from quorum_private.update_notices n
   where n.recipient_id=a.id and n.runtime_evidence ? 'run_id' and n.runtime_evidence ? 'reported_at'
   order by (n.runtime_evidence->>'reported_at')::timestamptz desc limit 1
  ) report on true
  cross join lateral (
   select coalesce(s.reported_at>now()-interval '60 seconds',false) as current,
    coalesce(h.last_seen<=now()-interval '60 seconds' and s.client_run_id is not null
     and report.run_id<>s.client_run_id and report.reported_at>s.reported_at,false) as restarted
  ) k
  where a.quorum_id=q and a.project_ref=project and not a.revoked and not d.revoked and not m.revoked
   and a.engine in ('codex','claude','grok')
  order by a.id limit 128
 ) x;
 return result;
end $$;

CREATE FUNCTION quorum_private.session_peers_before_status(p_agent_id uuid, p_session_ref text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; project text; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select project_ref into project from quorum_private.agents where id=p_agent_id;
 select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) into result from (
  select a.id as agent_id,a.role,a.engine,a.project_ref,h.last_seen,
   jsonb_build_object('protocol_version',h.protocol_version,'skill_version',h.skill_version) as client,
   public.session_requirements() as required,
   (h.protocol_version is distinct from (public.session_requirements()->>'protocol_version')::integer
    or h.skill_version is distinct from public.session_requirements()->>'skill_version') as update_required,
   case when pending.oldest<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen<=now()-interval '60 seconds' then 'unavailable'
        when h.last_seen is null then 'unknown' else 'online' end as availability,
   case when pending.oldest<=now()-interval '60 seconds' then 'receipt_timeout'
        when h.last_seen<=now()-interval '60 seconds' then 'heartbeat_timeout'
        when h.last_seen is null then 'heartbeat_not_supported' else null end as reason
  from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  left join quorum_private.session_health h on h.agent_id=a.id
  left join lateral (
   select min(dv.created_at) as oldest from quorum_private.deliveries dv
   where dv.recipient_id=a.id and dv.state='queued'
    and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=a.id and r.message_id=dv.message_id)
  ) pending on true
  where a.quorum_id=q and a.project_ref=project and not a.revoked and not d.revoked and not m.revoked
   and a.engine in ('codex','claude','grok')
  order by a.id limit 128
 ) x;
 return result;
end $$;

CREATE FUNCTION quorum_private.session_task_authority_before_project_bound(p_agent_id uuid, p_session_ref text, p_message_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; project text; original record; allowed boolean; reason text; maintenance boolean:=false;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select a.project_ref into project from quorum_private.agents a where a.id=p_agent_id;
 if project is null or octet_length(project) not between 1 and 256
   or octet_length(p_session_ref)>128 then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 select m.id,m.sender_id,m.reply_to into original from quorum_private.messages m
 where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id;
 if not found or original.sender_id is null then
  raise insufficient_privilege using message='task_message_denied'; end if;
 -- #69 adds this authenticated private type source. Its absence is the sole
 -- optional schema case; malformed/present schema errors must fail closed.
 if to_regclass('quorum_private.update_notices') is not null then
  select exists(select 1 from quorum_private.update_notices n where n.message_id=original.id) into maintenance;
 end if;
 if maintenance or original.reply_to is not null or exists(
     select 1 from quorum_private.session_notices n where n.message_id=original.id) then
  allowed:=false; reason:='not_task';
 else
  select exists(select 1 from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id and d.owner_id=a.owner_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where a.id=original.sender_id and a.quorum_id=q and a.project_ref=project
    and not a.revoked and not d.revoked and not m.revoked) into allowed;
  reason:=case when allowed then 'current_member' else 'sender_unavailable' end;
 end if;
 return jsonb_build_object('authorized',allowed,'reason',reason,'message_id',original.id,
  'agent_id',p_agent_id,'session_ref',p_session_ref,'sender_id',original.sender_id,
  'quorum_id',q,'project_ref',project);
end $$;

CREATE FUNCTION quorum_private.session_update_report_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_notice_id uuid, p_event text, p_evidence jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare q uuid; n quorum_private.update_notices%rowtype; target quorum_private.update_targets%rowtype;
 expected jsonb; previous quorum_private.update_reports%rowtype;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Keep close's quorum-first ordering before the notice and authority row locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege; end if;
 select * into n from quorum_private.update_notices where id=p_notice_id and recipient_id=p_agent_id for update;
 if not found then raise insufficient_privilege using message='update_notice_denied'; end if;
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 select * into target from quorum_private.update_targets where id=n.target_id and quorum_id=q;
 if not found then raise insufficient_privilege; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 if p_event not in ('refreshed','active','blocked') or p_event is null then raise exception 'update_event_invalid'; end if;
 expected:=jsonb_build_object('protocol_version',target.protocol_version,'skill_version',target.skill_version,
  'content_sha256',target.content_sha256);
 if p_event='blocked' then
  if p_evidence->>'code' is null or p_evidence->>'code' not in ('update_tool_missing','update_fetch_failed',
   'update_invalid_copy','update_validation_failed','update_cache_conflict','update_target_changed',
   'update_in_progress','update_unsafe_cache') then raise exception 'update_evidence_invalid'; end if;
  expected:=jsonb_build_object('code',p_evidence->>'code');
 end if;
 if p_event='active' then
  if p_evidence->>'run_id' is null or p_evidence->>'run_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
   raise exception 'update_evidence_invalid'; end if;
  expected:=expected||jsonb_build_object('run_id',p_evidence->>'run_id');
  if public.session_requirements()<>expected-'content_sha256'::text-'run_id'::text then raise exception 'skill_update_required'; end if;
 end if;
 if p_evidence is distinct from expected then raise exception 'update_evidence_invalid'; end if;
 select * into previous from quorum_private.update_reports where notice_id=n.id and request_id=p_request_id;
 if found then
  if previous.event<>p_event or previous.evidence<>p_evidence then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('recorded',true);
 end if;
 insert into quorum_private.update_reports values(n.id,p_request_id,p_event,p_evidence);
 update quorum_private.update_notices set received_at=coalesce(received_at,now()),
  refreshed_at=case when p_event='refreshed' then coalesce(refreshed_at,now()) else refreshed_at end,
  active_at=case when p_event='active' then now() else active_at end,
  runtime_evidence=case when p_event='active' then p_evidence||jsonb_build_object('reported_at',now()) else runtime_evidence end,
  blocked_code=case when p_event='blocked' then p_evidence->>'code' else null end
 where id=n.id;
 return jsonb_build_object('recorded',true);
end $_$;

CREATE FUNCTION quorum_private.session_update_request_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_protocol_version integer, p_skill_version text, p_content_sha256 text, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
declare q uuid; target quorum_private.update_targets%rowtype; op quorum_private.update_operations%rowtype;
 peer record; nid uuid; sent jsonb; failure text; queued_count integer:=0; reused integer:=0; failed_count integer:=0;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'update_requires_read_committed'; end if;
 -- Match close's lock order before ownership, membership, target and message locks.
 perform 1 from quorum_private.quorums where id=p_quorum_id for share;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id then raise insufficient_privilege using message='session_binding_denied'; end if;
 if p_protocol_version is null or p_protocol_version<1 or p_skill_version is null
  or p_skill_version!~'^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
  or p_content_sha256 is null or p_content_sha256!~'^[0-9a-f]{64}$' then raise exception 'update_target_invalid'; end if;
 if p_request_id is null or length(p_request_id) not between 1 and 128 then raise exception 'request_id_required'; end if;
 perform pg_advisory_xact_lock(hashtextextended('update-request:'||p_agent_id||':'||p_request_id,0));
 select * into op from quorum_private.update_operations where initiator_id=p_agent_id and request_id=p_request_id;
 if found then
  perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
  select * into target from quorum_private.update_targets where id=op.target_id;
  if target.quorum_id<>q or target.protocol_version<>p_protocol_version or target.skill_version<>p_skill_version
   or target.content_sha256<>p_content_sha256 then raise exception 'idempotency_conflict'; end if;
  return jsonb_build_object('operation_id',op.id,'queued',op.queued,'existing',op.existing,'failed',op.failed);
 end if;
 perform pg_advisory_xact_lock(hashtextextended('update-target:'||q||':'||p_content_sha256,0));
 perform quorum_private.update_session_locked(p_agent_id,p_session_ref,q);
 insert into quorum_private.update_targets(quorum_id,protocol_version,skill_version,content_sha256)
 values(q,p_protocol_version,p_skill_version,p_content_sha256) on conflict do nothing;
 select * into target from quorum_private.update_targets where quorum_id=q and content_sha256=p_content_sha256;
 if target.protocol_version<>p_protocol_version or target.skill_version<>p_skill_version then
  raise exception 'update_target_conflict'; end if;
 insert into quorum_private.update_operations(initiator_id,target_id,request_id)
 values(p_agent_id,target.id,p_request_id) returning * into op;
 -- This snapshot includes offline and same-role peers in every project/engine;
 -- unlike session_peers it has no health filter or arbitrary 128-participant cap.
 for peer in select a.id from quorum_private.agents a
  join quorum_private.devices d on d.id=a.device_id
  join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
  where a.quorum_id=q and a.id<>p_agent_id and not a.revoked and not d.revoked and not m.revoked order by a.id
 loop
  select id into nid from quorum_private.update_notices where target_id=target.id and recipient_id=peer.id;
  failure:=null;
  if nid is not null then reused:=reused+1;
  else
   begin
    sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
     'recipient_agent_id',peer.id,'conversation_id',gen_random_uuid(),'reply_to',null,
     'body','Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
      ||'no installation, lifecycle or task permission is granted. Target protocol '||p_protocol_version
      ||', skill '||p_skill_version||', sha256 '||p_content_sha256||'.','priority','normal'),
     'update:'||target.id||':'||peer.id);
    update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
    insert into quorum_private.update_notices(target_id,recipient_id,message_id)
    values(target.id,peer.id,(sent->>'message_id')::uuid) returning id into nid;
    queued_count:=queued_count+1;
   exception when insufficient_privilege then failure:='recipient_unavailable'; failed_count:=failed_count+1;
    when raise_exception then
     if sqlerrm<>'recipient_unavailable' then raise; end if;
     failure:='recipient_unavailable'; failed_count:=failed_count+1;
   end;
  end if;
  insert into quorum_private.update_recipients values(op.id,peer.id,nid,failure);
 end loop;
 update quorum_private.update_operations set queued=queued_count,existing=reused,failed=failed_count,
  unaddressable_members=(select count(*) from quorum_private.memberships m where m.quorum_id=q and not m.revoked
   and not exists(select 1 from quorum_private.agents a join quorum_private.devices d on d.id=a.device_id
    where a.quorum_id=q and a.owner_id=m.user_id and not a.revoked and not d.revoked))
 where id=op.id;
 return jsonb_build_object('operation_id',op.id,'queued',queued_count,'existing',reused,'failed',failed_count);
end $_$;

CREATE FUNCTION quorum_private.session_update_status_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_operation_id uuid DEFAULT NULL::uuid, p_notice_id uuid DEFAULT NULL::uuid, p_after_agent uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare q uuid; target quorum_private.update_targets%rowtype; n quorum_private.update_notices%rowtype;
 op quorum_private.update_operations%rowtype; items jsonb; cursor uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if q<>p_quorum_id or (p_operation_id is null)=(p_notice_id is null) then
  raise insufficient_privilege using message='update_status_denied'; end if;
 if p_notice_id is not null then
  select * into n from quorum_private.update_notices where id=p_notice_id and recipient_id=p_agent_id;
  if not found then raise insufficient_privilege using message='update_notice_denied'; end if;
  select * into target from quorum_private.update_targets where id=n.target_id and quorum_id=q;
  if not found then raise insufficient_privilege; end if;
  return jsonb_build_object('notice_id',n.id,'recipient_agent_id',p_agent_id,'quorum_id',q,
   'target',jsonb_build_object('protocol_version',target.protocol_version,'skill_version',target.skill_version,
    'content_sha256',target.content_sha256),'queued_at',n.queued_at,'received_at',n.received_at,
   'refreshed_at',n.refreshed_at,'active_at',n.active_at,'runtime_report',n.runtime_evidence,'blocked_code',n.blocked_code,
   'state',case when n.blocked_code is not null then 'blocked' when n.active_at is not null then 'active_reported' when n.refreshed_at is not null then 'refreshed'
    when n.received_at is not null then 'owner_action_required' else 'queued' end);
 end if;
 select * into op from quorum_private.update_operations where id=p_operation_id and initiator_id=p_agent_id;
 if not found then raise insufficient_privilege using message='update_status_denied'; end if;
 select * into target from quorum_private.update_targets where id=op.target_id and quorum_id=q;
 if not found then raise insufficient_privilege; end if;
 select coalesce(jsonb_agg(to_jsonb(x) order by x.agent_id),'[]'::jsonb) into items from (
  select r.recipient_id as agent_id,notice.id as notice_id,r.failure_code,notice.queued_at,notice.received_at,notice.refreshed_at,notice.active_at,notice.blocked_code,
   notice.runtime_evidence as runtime_report,
   case when r.failure_code is not null then 'unavailable' when notice.blocked_code is not null then 'blocked' when notice.active_at is not null then 'active_reported'
    when notice.refreshed_at is not null then 'refreshed' when notice.received_at is not null then 'owner_action_required' else 'queued' end as state
  from quorum_private.update_recipients r left join quorum_private.update_notices notice on notice.id=r.notice_id
  where r.operation_id=op.id and (p_after_agent is null or r.recipient_id>p_after_agent)
  order by r.recipient_id limit 64
 ) x;
 if jsonb_array_length(items)=64 then
  cursor:=(items->63->>'agent_id')::uuid;
  if not exists(select 1 from quorum_private.update_recipients where operation_id=op.id and recipient_id>cursor) then cursor:=null; end if;
 end if;
 return jsonb_build_object('operation_id',op.id,'target',jsonb_build_object('protocol_version',target.protocol_version,
  'skill_version',target.skill_version,'content_sha256',target.content_sha256),'participants',items,'next_after_agent',cursor,
  'unaddressable_members',op.unaddressable_members,'all_participants_upgraded',false);
end $$;

CREATE FUNCTION quorum_private.update_session_locked(p_agent_id uuid, p_session_ref text, p_quorum_id uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare actor quorum_private.agents%rowtype;
begin
 select * into actor from quorum_private.agents
  where id=p_agent_id and owner_id=auth.uid() and quorum_id=p_quorum_id for share;
 if not found then raise insufficient_privilege using message='session_binding_denied'; end if;
 perform 1 from quorum_private.devices where id=actor.device_id and owner_id=actor.owner_id for share;
 perform 1 from quorum_private.memberships where quorum_id=p_quorum_id and user_id=actor.owner_id for share;
 if quorum_private.native_session(p_agent_id,p_session_ref)<>p_quorum_id then
  raise insufficient_privilege using message='session_binding_denied'; end if;
end $$;

CREATE FUNCTION quorum_private.verified_actor(who uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select who is not null and (
  exists(select 1 from auth.identities where user_id=who and provider='github')
  or exists(select 1 from quorum_private.memberships m join quorum_private.quorums q on q.id=m.quorum_id
            where m.user_id=who and not m.revoked and q.access='open'))
$$;

CREATE TABLE quorum_private.agent_supersessions (
    superseded_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE quorum_private.agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    device_id uuid,
    owner_id uuid NOT NULL,
    project_ref text NOT NULL,
    role text NOT NULL,
    engine text,
    session_ref text NOT NULL,
    revoked boolean DEFAULT false,
    CONSTRAINT agents_engine_check CHECK ((engine = ANY (ARRAY['claude'::text, 'codex'::text, 'grok'::text]))),
    CONSTRAINT agents_role_check CHECK ((role ~ '^[a-z][a-z0-9-]{0,47}$'::text))
);

CREATE TABLE quorum_private.attempts (
    id uuid NOT NULL,
    delivery_id uuid,
    generation bigint NOT NULL,
    device_id uuid
);

CREATE TABLE quorum_private.audit_events (
    id bigint NOT NULL,
    actor uuid NOT NULL,
    operation text NOT NULL,
    quorum_id uuid,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE quorum_private.audit_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME quorum_private.audit_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE quorum_private.capacity_reservations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    work_id uuid,
    agent_id uuid,
    generation bigint DEFAULT 1 NOT NULL,
    state text DEFAULT 'implementing'::text NOT NULL,
    head_sha text,
    reviewed_sha text,
    reviewer_id uuid,
    evidence text
);

CREATE TABLE quorum_private.context_versions (
    id bigint NOT NULL,
    quorum_id uuid,
    project_ref text NOT NULL,
    author_id uuid NOT NULL,
    body text NOT NULL,
    source_ref text NOT NULL,
    CONSTRAINT context_versions_body_check CHECK ((octet_length(body) <= 16384))
);

ALTER TABLE quorum_private.context_versions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME quorum_private.context_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE quorum_private.conversations (
    id uuid NOT NULL,
    quorum_id uuid,
    sender_id uuid,
    recipient_id uuid,
    next_sequence bigint DEFAULT 0 NOT NULL
);

CREATE TABLE quorum_private.deliveries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    message_id uuid,
    recipient_id uuid,
    requires_handler boolean DEFAULT true NOT NULL,
    state text DEFAULT 'queued'::text NOT NULL,
    generation bigint DEFAULT 1,
    attempt_id uuid,
    lease_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT deliveries_state_check CHECK ((state = ANY (ARRAY['queued'::text, 'claimed'::text, 'launch_intent'::text, 'running'::text, 'answered'::text, 'cancel_requested'::text, 'cancelled'::text, 'blocked'::text, 'unknown'::text])))
);

CREATE TABLE quorum_private.delivery_status_streams (
    sender_id uuid NOT NULL,
    last_seq bigint DEFAULT 0 NOT NULL
);

CREATE TABLE quorum_private.delivery_statuses (
    message_id uuid NOT NULL,
    quorum_id uuid NOT NULL,
    sender_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    status text NOT NULL,
    status_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    proof text,
    queued_at timestamp with time zone,
    read_at timestamp with time zone,
    answered_at timestamp with time zone,
    seq bigint NOT NULL,
    CONSTRAINT delivery_statuses_proof_check CHECK ((proof = ANY (ARRAY['received'::text, 'body_read'::text, 'reply'::text]))),
    CONSTRAINT delivery_statuses_reason_check CHECK (((octet_length(reason) >= 1) AND (octet_length(reason) <= 512))),
    CONSTRAINT delivery_statuses_status_check CHECK ((status = ANY (ARRAY['sent'::text, 'queued'::text, 'read'::text, 'answered'::text, 'in_progress'::text, 'deferred'::text, 'needs_owner'::text, 'declined'::text, 'done'::text, 'failed'::text])))
);

CREATE TABLE quorum_private.devices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    label text NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    CONSTRAINT devices_label_check CHECK (((length(label) >= 1) AND (length(label) <= 100)))
);

CREATE TABLE quorum_private.id_invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid NOT NULL,
    target_github_id text,
    created_by uuid NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    accepted_by uuid,
    revoked boolean DEFAULT false NOT NULL,
    open boolean DEFAULT false NOT NULL,
    assigned_role text,
    assigned_project_ref text,
    CONSTRAINT id_invitations_assigned_project_ref_check CHECK (((length(assigned_project_ref) <= 500) AND (assigned_project_ref ~ '^[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]*$'::text))),
    CONSTRAINT id_invitations_assigned_role_check CHECK ((assigned_role ~ '^[a-z][a-z0-9-]{0,47}$'::text)),
    CONSTRAINT id_invitations_assignment_check CHECK (((assigned_role IS NULL) OR (assigned_project_ref IS NOT NULL))),
    CONSTRAINT id_invitations_open_target_check CHECK ((open = (target_github_id IS NULL))),
    CONSTRAINT id_invitations_target_github_id_check CHECK ((target_github_id ~ '^[0-9]{1,20}$'::text))
);

CREATE TABLE quorum_private.instance_creators (
    github_id text NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    CONSTRAINT instance_creators_github_id_check CHECK ((github_id ~ '^[0-9]{1,20}$'::text))
);

CREATE TABLE quorum_private.invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    target_github_id text NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    accepted_by uuid,
    revoked boolean DEFAULT false,
    CONSTRAINT invitations_target_github_id_check CHECK ((target_github_id ~ '^[0-9]+$'::text))
);

CREATE TABLE quorum_private.memberships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    user_id uuid NOT NULL,
    role text,
    revoked boolean DEFAULT false NOT NULL,
    assigned_role text,
    assigned_project_ref text,
    CONSTRAINT memberships_assigned_project_ref_check CHECK (((length(assigned_project_ref) <= 500) AND (assigned_project_ref ~ '^[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]*$'::text))),
    CONSTRAINT memberships_assigned_role_check CHECK ((assigned_role ~ '^[a-z][a-z0-9-]{0,47}$'::text)),
    CONSTRAINT memberships_assignment_check CHECK (((assigned_role IS NULL) OR (assigned_project_ref IS NOT NULL))),
    CONSTRAINT memberships_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text])))
);

CREATE TABLE quorum_private.quorums (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    owner_id uuid NOT NULL,
    capacity integer DEFAULT 2 NOT NULL,
    allowlisted_repo text,
    access text DEFAULT 'github'::text NOT NULL,
    CONSTRAINT quorums_access_check CHECK ((access = ANY (ARRAY['github'::text, 'open'::text]))),
    CONSTRAINT quorums_capacity_check CHECK (((capacity >= 1) AND (capacity <= 100))),
    CONSTRAINT quorums_slug_check CHECK ((slug ~ '^[a-z][a-z0-9-]{0,47}$'::text))
);

CREATE TABLE quorum_private.requests (
    actor uuid NOT NULL,
    operation text NOT NULL,
    request_id text NOT NULL,
    fingerprint text NOT NULL,
    result jsonb,
    CONSTRAINT requests_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 128)))
);

CREATE TABLE quorum_private.session_connections (
    agent_id uuid NOT NULL,
    connection_id uuid NOT NULL
);

CREATE TABLE quorum_private.session_health (
    agent_id uuid NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    protocol_version integer,
    skill_version text
);

CREATE TABLE quorum_private.session_hello_sent (
    sender_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    sent_at timestamp with time zone NOT NULL
);

CREATE TABLE quorum_private.session_hellos (
    agent_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    peers integer NOT NULL,
    roles jsonb NOT NULL,
    available integer NOT NULL,
    available_roles jsonb NOT NULL,
    roster_message_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    skipped integer,
    CONSTRAINT session_hellos_available_check CHECK (((available >= 0) AND (available <= 128))),
    CONSTRAINT session_hellos_available_roles_check CHECK (((jsonb_typeof(available_roles) = 'array'::text) AND (jsonb_array_length(available_roles) <= 16))),
    CONSTRAINT session_hellos_peers_check CHECK (((peers >= 0) AND (peers <= 128))),
    CONSTRAINT session_hellos_roles_check CHECK (((jsonb_typeof(roles) = 'array'::text) AND (jsonb_array_length(roles) <= 16))),
    CONSTRAINT session_hellos_skipped_check CHECK (((skipped >= 0) AND (skipped <= 128)))
);

CREATE TABLE quorum_private.session_notices (
    message_id uuid NOT NULL,
    agent_id uuid NOT NULL
);

CREATE TABLE quorum_private.session_receipts (
    agent_id uuid NOT NULL,
    message_id uuid NOT NULL,
    queued_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE quorum_private.session_release (
    agent_id uuid NOT NULL,
    content_sha256 text NOT NULL,
    client_run_id uuid,
    reported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT session_release_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text))
);

CREATE TABLE quorum_private.session_status (
    agent_id uuid NOT NULL,
    reported_at timestamp with time zone NOT NULL,
    awaiting_owner integer,
    reception_hold_until timestamp with time zone,
    client_run_id uuid,
    runtime_started_at timestamp with time zone,
    CONSTRAINT session_status_awaiting_owner_check CHECK (((awaiting_owner >= 0) AND (awaiting_owner <= 32)))
);

CREATE TABLE quorum_private.session_streaks (
    agent_id uuid NOT NULL,
    online_since timestamp with time zone NOT NULL
);

CREATE TABLE quorum_private.update_notices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    target_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    message_id uuid NOT NULL,
    queued_at timestamp with time zone DEFAULT now() NOT NULL,
    received_at timestamp with time zone,
    refreshed_at timestamp with time zone,
    active_at timestamp with time zone,
    runtime_evidence jsonb,
    blocked_code text
);

CREATE TABLE quorum_private.update_operations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    initiator_id uuid NOT NULL,
    target_id uuid NOT NULL,
    request_id text NOT NULL,
    queued integer DEFAULT 0 NOT NULL,
    existing integer DEFAULT 0 NOT NULL,
    failed integer DEFAULT 0 NOT NULL,
    unaddressable_members integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT update_operations_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 128)))
);

CREATE TABLE quorum_private.update_recipients (
    operation_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    notice_id uuid,
    failure_code text,
    CONSTRAINT update_recipients_check CHECK (((notice_id IS NULL) <> (failure_code IS NULL)))
);

CREATE TABLE quorum_private.update_reports (
    notice_id uuid NOT NULL,
    request_id text NOT NULL,
    event text NOT NULL,
    evidence jsonb NOT NULL,
    CONSTRAINT update_reports_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 128)))
);

CREATE TABLE quorum_private.update_targets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid NOT NULL,
    protocol_version integer NOT NULL,
    skill_version text,
    content_sha256 text NOT NULL,
    CONSTRAINT update_targets_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT update_targets_protocol_version_check CHECK ((protocol_version > 0)),
    CONSTRAINT update_targets_skill_version_check CHECK ((skill_version ~ '^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'::text))
);

CREATE TABLE quorum_private.work_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quorum_id uuid,
    repository text NOT NULL,
    issue_number bigint NOT NULL,
    human_owner_id uuid NOT NULL,
    criteria text NOT NULL,
    dependencies jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT work_items_criteria_check CHECK ((length(criteria) > 0)),
    CONSTRAINT work_items_issue_number_check CHECK ((issue_number > 0))
);

ALTER TABLE ONLY quorum_private.agent_supersessions
    ADD CONSTRAINT agent_supersessions_pkey PRIMARY KEY (superseded_id);

ALTER TABLE ONLY quorum_private.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.agents
    ADD CONSTRAINT agents_quorum_id_device_id_session_ref_key UNIQUE (quorum_id, device_id, session_ref);

ALTER TABLE ONLY quorum_private.attempts
    ADD CONSTRAINT attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.capacity_reservations
    ADD CONSTRAINT capacity_reservations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.capacity_reservations
    ADD CONSTRAINT capacity_reservations_work_id_key UNIQUE (work_id);

ALTER TABLE ONLY quorum_private.context_versions
    ADD CONSTRAINT context_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_attempt_id_key UNIQUE (attempt_id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_message_id_key UNIQUE (message_id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.delivery_status_streams
    ADD CONSTRAINT delivery_status_streams_pkey PRIMARY KEY (sender_id);

ALTER TABLE ONLY quorum_private.delivery_statuses
    ADD CONSTRAINT delivery_statuses_pkey PRIMARY KEY (message_id);

ALTER TABLE ONLY quorum_private.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.id_invitations
    ADD CONSTRAINT id_invitations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.instance_creators
    ADD CONSTRAINT instance_creators_pkey PRIMARY KEY (github_id);

ALTER TABLE ONLY quorum_private.invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.invitations
    ADD CONSTRAINT invitations_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY quorum_private.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.memberships
    ADD CONSTRAINT memberships_quorum_id_user_id_key UNIQUE (quorum_id, user_id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_conversation_id_sequence_key UNIQUE (conversation_id, sequence);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.quorums
    ADD CONSTRAINT quorums_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.quorums
    ADD CONSTRAINT quorums_slug_key UNIQUE (slug);

ALTER TABLE ONLY quorum_private.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (actor, operation, request_id);

ALTER TABLE ONLY quorum_private.session_connections
    ADD CONSTRAINT session_connections_pkey PRIMARY KEY (agent_id, connection_id);

ALTER TABLE ONLY quorum_private.session_health
    ADD CONSTRAINT session_health_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY quorum_private.session_hello_sent
    ADD CONSTRAINT session_hello_sent_pkey PRIMARY KEY (sender_id, recipient_id);

ALTER TABLE ONLY quorum_private.session_hellos
    ADD CONSTRAINT session_hellos_pkey PRIMARY KEY (agent_id, connection_id);

ALTER TABLE ONLY quorum_private.session_hellos
    ADD CONSTRAINT session_hellos_roster_message_id_key UNIQUE (roster_message_id);

ALTER TABLE ONLY quorum_private.session_notices
    ADD CONSTRAINT session_notices_pkey PRIMARY KEY (message_id);

ALTER TABLE ONLY quorum_private.session_receipts
    ADD CONSTRAINT session_receipts_pkey PRIMARY KEY (agent_id, message_id);

ALTER TABLE ONLY quorum_private.session_release
    ADD CONSTRAINT session_release_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY quorum_private.session_status
    ADD CONSTRAINT session_status_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY quorum_private.session_streaks
    ADD CONSTRAINT session_streaks_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_message_id_key UNIQUE (message_id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_target_id_recipient_id_key UNIQUE (target_id, recipient_id);

ALTER TABLE ONLY quorum_private.update_operations
    ADD CONSTRAINT update_operations_initiator_id_request_id_key UNIQUE (initiator_id, request_id);

ALTER TABLE ONLY quorum_private.update_operations
    ADD CONSTRAINT update_operations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.update_recipients
    ADD CONSTRAINT update_recipients_pkey PRIMARY KEY (operation_id, recipient_id);

ALTER TABLE ONLY quorum_private.update_reports
    ADD CONSTRAINT update_reports_pkey PRIMARY KEY (notice_id, request_id);

ALTER TABLE ONLY quorum_private.update_targets
    ADD CONSTRAINT update_targets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.update_targets
    ADD CONSTRAINT update_targets_quorum_id_content_sha256_key UNIQUE (quorum_id, content_sha256);

ALTER TABLE ONLY quorum_private.work_items
    ADD CONSTRAINT work_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.work_items
    ADD CONSTRAINT work_items_repository_issue_number_key UNIQUE (repository, issue_number);

CREATE INDEX deliveries_quorum_recent ON quorum_private.deliveries USING btree (quorum_id, created_at DESC);

CREATE INDEX delivery_pending ON quorum_private.deliveries USING btree (recipient_id, created_at) WHERE (state = 'queued'::text);

CREATE UNIQUE INDEX delivery_statuses_feed ON quorum_private.delivery_statuses USING btree (sender_id, seq);

CREATE TRIGGER delivery_status_answered AFTER UPDATE OF state ON quorum_private.deliveries FOR EACH ROW WHEN (((new.state = 'answered'::text) AND (old.state IS DISTINCT FROM 'answered'::text))) EXECUTE FUNCTION quorum_private.delivery_status_answered();

CREATE TRIGGER open_invitation_ttl BEFORE INSERT ON quorum_private.id_invitations FOR EACH ROW WHEN (new.open) EXECUTE FUNCTION quorum_private.open_invitation_ttl();

CREATE TRIGGER quorum_delivery_wake AFTER INSERT ON quorum_private.deliveries FOR EACH ROW EXECUTE FUNCTION quorum_private.notify_inbox();

CREATE TRIGGER quorum_receipt_wake AFTER INSERT ON quorum_private.session_receipts FOR EACH ROW EXECUTE FUNCTION quorum_private.notify_inbox();

CREATE TRIGGER session_health_streak AFTER INSERT OR UPDATE OF last_seen ON quorum_private.session_health FOR EACH ROW EXECUTE FUNCTION quorum_private.session_health_streak();

ALTER TABLE ONLY quorum_private.agent_supersessions
    ADD CONSTRAINT agent_supersessions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.agent_supersessions
    ADD CONSTRAINT agent_supersessions_superseded_id_fkey FOREIGN KEY (superseded_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.agents
    ADD CONSTRAINT agents_device_id_fkey FOREIGN KEY (device_id) REFERENCES quorum_private.devices(id);

ALTER TABLE ONLY quorum_private.agents
    ADD CONSTRAINT agents_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.attempts
    ADD CONSTRAINT attempts_delivery_id_fkey FOREIGN KEY (delivery_id) REFERENCES quorum_private.deliveries(id);

ALTER TABLE ONLY quorum_private.attempts
    ADD CONSTRAINT attempts_device_id_fkey FOREIGN KEY (device_id) REFERENCES quorum_private.devices(id);

ALTER TABLE ONLY quorum_private.capacity_reservations
    ADD CONSTRAINT capacity_reservations_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.capacity_reservations
    ADD CONSTRAINT capacity_reservations_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.capacity_reservations
    ADD CONSTRAINT capacity_reservations_work_id_fkey FOREIGN KEY (work_id) REFERENCES quorum_private.work_items(id);

ALTER TABLE ONLY quorum_private.context_versions
    ADD CONSTRAINT context_versions_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.conversations
    ADD CONSTRAINT conversations_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.conversations
    ADD CONSTRAINT conversations_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.conversations
    ADD CONSTRAINT conversations_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_message_id_fkey FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.deliveries
    ADD CONSTRAINT deliveries_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.delivery_status_streams
    ADD CONSTRAINT delivery_status_streams_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.delivery_statuses
    ADD CONSTRAINT delivery_statuses_message_id_fkey FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.delivery_statuses
    ADD CONSTRAINT delivery_statuses_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.delivery_statuses
    ADD CONSTRAINT delivery_statuses_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.delivery_statuses
    ADD CONSTRAINT delivery_statuses_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.id_invitations
    ADD CONSTRAINT id_invitations_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.invitations
    ADD CONSTRAINT invitations_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.memberships
    ADD CONSTRAINT memberships_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES quorum_private.conversations(id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_reply_to_fkey FOREIGN KEY (reply_to) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.messages
    ADD CONSTRAINT messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_connections
    ADD CONSTRAINT session_connections_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_health
    ADD CONSTRAINT session_health_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_hello_sent
    ADD CONSTRAINT session_hello_sent_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_hello_sent
    ADD CONSTRAINT session_hello_sent_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_hellos
    ADD CONSTRAINT session_hellos_agent_id_connection_id_fkey FOREIGN KEY (agent_id, connection_id) REFERENCES quorum_private.session_connections(agent_id, connection_id);

ALTER TABLE ONLY quorum_private.session_hellos
    ADD CONSTRAINT session_hellos_roster_message_id_fkey FOREIGN KEY (roster_message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.session_notices
    ADD CONSTRAINT session_notices_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_notices
    ADD CONSTRAINT session_notices_message_id_fkey FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.session_receipts
    ADD CONSTRAINT session_receipts_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_receipts
    ADD CONSTRAINT session_receipts_message_id_fkey FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.session_release
    ADD CONSTRAINT session_release_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_status
    ADD CONSTRAINT session_status_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.session_streaks
    ADD CONSTRAINT session_streaks_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_message_id_fkey FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.update_notices
    ADD CONSTRAINT update_notices_target_id_fkey FOREIGN KEY (target_id) REFERENCES quorum_private.update_targets(id);

ALTER TABLE ONLY quorum_private.update_operations
    ADD CONSTRAINT update_operations_initiator_id_fkey FOREIGN KEY (initiator_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.update_operations
    ADD CONSTRAINT update_operations_target_id_fkey FOREIGN KEY (target_id) REFERENCES quorum_private.update_targets(id);

ALTER TABLE ONLY quorum_private.update_recipients
    ADD CONSTRAINT update_recipients_notice_id_fkey FOREIGN KEY (notice_id) REFERENCES quorum_private.update_notices(id);

ALTER TABLE ONLY quorum_private.update_recipients
    ADD CONSTRAINT update_recipients_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES quorum_private.update_operations(id);

ALTER TABLE ONLY quorum_private.update_recipients
    ADD CONSTRAINT update_recipients_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);

ALTER TABLE ONLY quorum_private.update_reports
    ADD CONSTRAINT update_reports_notice_id_fkey FOREIGN KEY (notice_id) REFERENCES quorum_private.update_notices(id);

ALTER TABLE ONLY quorum_private.update_targets
    ADD CONSTRAINT update_targets_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.work_items
    ADD CONSTRAINT work_items_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE quorum_private.agent_supersessions ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.agents ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.attempts ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.audit_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.capacity_reservations ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.context_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.conversations ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.deliveries ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.delivery_status_streams ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.delivery_statuses ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.devices ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.id_invitations ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.instance_creators ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.invitations ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.memberships ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.messages ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.quorums ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.requests ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_connections ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_health ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_hello_sent ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_hellos ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_notices ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_receipts ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_release ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_status ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.session_streaks ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.update_notices ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.update_operations ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.update_recipients ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.update_reports ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.update_targets ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.work_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY quorum_agent_wake_receive ON realtime.messages FOR SELECT TO authenticated USING (((extension = 'broadcast'::text) AND (topic = ( SELECT realtime.topic() AS topic)) AND ( SELECT quorum_private.receive_wakeup(realtime.topic()) AS receive_wakeup)));

REVOKE ALL ON FUNCTION public.agent_join(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text, p_supersede boolean, p_keep_agent_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.agent_join(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text, p_supersede boolean, p_keep_agent_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.agent_leave(p_agent_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.agent_leave(p_agent_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.agent_leave(p_agent_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.agent_retire(p_quorum_id uuid, p_agent_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.agent_retire(p_quorum_id uuid, p_agent_id uuid, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.agent_status(p_agent_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.agent_status(p_agent_id uuid) TO authenticated;
GRANT ALL ON FUNCTION public.agent_status(p_agent_id uuid) TO service_role;

REVOKE ALL ON FUNCTION public.attempt_update(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.attempt_update(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.attempt_update(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.context_read(p_quorum_id uuid, p_project_ref text, p_after_version bigint) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.context_read(p_quorum_id uuid, p_project_ref text, p_after_version bigint) TO authenticated;
GRANT ALL ON FUNCTION public.context_read(p_quorum_id uuid, p_project_ref text, p_after_version bigint) TO service_role;

REVOKE ALL ON FUNCTION public.conversation_read(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.conversation_read(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) TO authenticated;
GRANT ALL ON FUNCTION public.conversation_read(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) TO service_role;

REVOKE ALL ON FUNCTION public.delivery_complete(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.delivery_complete(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.delivery_complete(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.device_register(p_label text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.device_register(p_label text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.device_register(p_label text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.inbox_claim(p_device_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.inbox_claim(p_device_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.inbox_claim(p_device_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.invite_accept(p_token text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_accept(p_token text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.invite_accept(p_token text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.invite_accept_id(p_invite_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_accept_id(p_invite_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.invite_accept_id(p_invite_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.invite_create(p_quorum_id uuid, p_target_github_id text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_create(p_quorum_id uuid, p_target_github_id text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.invite_create(p_quorum_id uuid, p_target_github_id text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.invite_create_id(p_quorum_id uuid, p_target_github_id text, p_request_id text, p_role text, p_project_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_create_id(p_quorum_id uuid, p_target_github_id text, p_request_id text, p_role text, p_project_ref text) TO authenticated;

REVOKE ALL ON FUNCTION public.invite_create_open(p_quorum_id uuid, p_request_id text, p_role text, p_project_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_create_open(p_quorum_id uuid, p_request_id text, p_role text, p_project_ref text) TO authenticated;

REVOKE ALL ON FUNCTION public.invite_revoke_id(p_invite_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.invite_revoke_id(p_invite_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.invite_revoke_id(p_invite_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.member_revoke(p_quorum_id uuid, p_member_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.member_revoke(p_quorum_id uuid, p_member_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.member_revoke(p_quorum_id uuid, p_member_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.message_send(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text, p_priority text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.message_send(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text, p_priority text) TO authenticated;
GRANT ALL ON FUNCTION public.message_send(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text, p_priority text) TO service_role;

REVOKE ALL ON FUNCTION public.quorum_agents(p_quorum_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.quorum_agents(p_quorum_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.quorum_check_owner(p_quorum_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.quorum_check_owner(p_quorum_id uuid) TO authenticated;
GRANT ALL ON FUNCTION public.quorum_check_owner(p_quorum_id uuid) TO service_role;

REVOKE ALL ON FUNCTION public.quorum_create(p_slug text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.quorum_create(p_slug text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.quorum_create(p_slug text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.quorum_lookup(p_slug text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.quorum_lookup(p_slug text) TO authenticated;
GRANT ALL ON FUNCTION public.quorum_lookup(p_slug text) TO service_role;

REVOKE ALL ON FUNCTION public.quorum_set_access(p_quorum_id uuid, p_access text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.quorum_set_access(p_quorum_id uuid, p_access text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.quorum_set_access(p_quorum_id uuid, p_access text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.resolve_role(p_quorum_id uuid, p_role text, p_project_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.resolve_role(p_quorum_id uuid, p_role text, p_project_ref text) TO authenticated;
GRANT ALL ON FUNCTION public.resolve_role(p_quorum_id uuid, p_role text, p_project_ref text) TO service_role;

REVOKE ALL ON FUNCTION public.session_ack(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_ack(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_delivery_changes(p_agent_id uuid, p_session_ref text, p_after bigint) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_delivery_changes(p_agent_id uuid, p_session_ref text, p_after bigint) TO authenticated;

REVOKE ALL ON FUNCTION public.session_delivery_mark(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_status text, p_reason text, p_proof text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_delivery_mark(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_status text, p_reason text, p_proof text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_delivery_status(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_delivery_status(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_delivery_stuck(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_delivery_stuck(p_agent_id uuid, p_session_ref text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text, p_awaiting_owner integer, p_reception_hold_until timestamp with time zone, p_client_run_id uuid, p_runtime_started_at timestamp with time zone) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text, p_awaiting_owner integer, p_reception_hold_until timestamp with time zone, p_client_run_id uuid, p_runtime_started_at timestamp with time zone) TO authenticated;

REVOKE ALL ON FUNCTION public.session_inbox(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_inbox(p_agent_id uuid, p_session_ref text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_message(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_message(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_message_unreceipted(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_message_unreceipted(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_peers(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_peers(p_agent_id uuid, p_session_ref text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_release_report(p_agent_id uuid, p_session_ref text, p_content_sha256 text, p_client_run_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_release_report(p_agent_id uuid, p_session_ref text, p_content_sha256 text, p_client_run_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_release_status(p_agent_id uuid, p_session_ref text, p_operation_id uuid, p_after_agent uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_release_status(p_agent_id uuid, p_session_ref text, p_operation_id uuid, p_after_agent uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_reply(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text, p_priority text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_reply(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text, p_priority text) TO authenticated;
GRANT ALL ON FUNCTION public.session_reply(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text, p_priority text) TO service_role;

REVOKE ALL ON FUNCTION public.session_requirements() FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_requirements() TO service_role;
GRANT ALL ON FUNCTION public.session_requirements() TO authenticated;

REVOKE ALL ON FUNCTION public.session_task_authority(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_task_authority(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.session_update_report(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_notice_id uuid, p_event text, p_evidence jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_update_report(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_notice_id uuid, p_event text, p_evidence jsonb, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_update_request(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_protocol_version integer, p_skill_version text, p_content_sha256 text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_update_request(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_protocol_version integer, p_skill_version text, p_content_sha256 text, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.session_update_status(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_operation_id uuid, p_notice_id uuid, p_after_agent uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.session_update_status(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_operation_id uuid, p_notice_id uuid, p_after_agent uuid) TO authenticated;

REVOKE ALL ON FUNCTION public.work_admit(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.work_admit(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.work_admit(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.work_register(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.work_register(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.work_register(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION public.work_transition(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.work_transition(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) TO authenticated;
GRANT ALL ON FUNCTION public.work_transition(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) TO service_role;

REVOKE ALL ON FUNCTION quorum_private.call(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.call_invite_core(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.call_legacy_core(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.call_priority_core(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.call_role_core(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.call_serial_core(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.delivery_status_answered() FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.delivery_status_applies(p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.delivery_status_apply(p_message quorum_private.messages, p_status text, p_reason text, p_proof text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.delivery_status_view(p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.member(q uuid, who uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.native_session(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.notify_inbox() FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.open_invitation_ttl() FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.owned_agent(a uuid, who uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.receive_wakeup(p_topic text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION quorum_private.receive_wakeup(p_topic text) TO authenticated;

REVOKE ALL ON FUNCTION quorum_private.session_ack_before_updates(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_health_streak() FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_before_hash_only(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text, p_awaiting_owner integer, p_reception_hold_until timestamp with time zone, p_client_run_id uuid, p_runtime_started_at timestamp with time zone) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_before_hello(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_before_presence(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text, p_awaiting_owner integer, p_reception_hold_until timestamp with time zone, p_client_run_id uuid, p_runtime_started_at timestamp with time zone) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_before_status(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_core_before_dedupe(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_hello_before_dedupe(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_heartbeat_hello_before_split(p_agent_id uuid, p_session_ref text, p_connection_id uuid, p_protocol_version integer, p_skill_version text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_inbox_before_hash_only(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_inbox_before_updates(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_peers_before_hash_only(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_peers_before_status(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_task_authority_before_project_bound(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_update_report_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_notice_id uuid, p_event text, p_evidence jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_update_request_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_protocol_version integer, p_skill_version text, p_content_sha256 text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.session_update_status_before_hash_only(p_agent_id uuid, p_session_ref text, p_quorum_id uuid, p_operation_id uuid, p_notice_id uuid, p_after_agent uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.update_session_locked(p_agent_id uuid, p_session_ref text, p_quorum_id uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.verified_actor(who uuid) FROM PUBLIC, anon, authenticated, service_role;

create table quorum_private.schema_baseline (
 part text primary key check (part in ('core','knowledge')),
 covers_through text not null,
 installed_by text not null check (installed_by in ('baseline','adopt')),
 recorded_at timestamp with time zone not null default now()
);
alter table quorum_private.schema_baseline enable row level security;
revoke all on quorum_private.schema_baseline from public,anon,authenticated,service_role;
insert into quorum_private.schema_baseline(part,covers_through,installed_by) values('core','20261108140000','baseline');
