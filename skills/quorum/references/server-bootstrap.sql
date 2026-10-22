-- Schema-only baseline of the Quorum pilot, exported 2026-10-22.
-- Apply only to a fresh Supabase project, together with all later migrations.
-- No production users, configuration, memberships, invitations or credentials.
-- The following creator-gate migration must be applied before exposing this service.
CREATE SCHEMA quorum_private;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
SET LOCAL check_function_bodies = off;
CREATE TABLE quorum_private."quorums" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "slug" text NOT NULL,
  "owner_id" uuid NOT NULL,
  "capacity" integer DEFAULT 2 NOT NULL,
  "allowlisted_repo" text
);
CREATE TABLE quorum_private."memberships" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "user_id" uuid NOT NULL,
  "role" text,
  "revoked" boolean DEFAULT false NOT NULL
);
CREATE TABLE quorum_private."invitations" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "target_github_id" text NOT NULL,
  "token_hash" text NOT NULL,
  "expires_at" timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
  "accepted_by" uuid,
  "revoked" boolean DEFAULT false
);
CREATE TABLE quorum_private."agents" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "device_id" uuid,
  "owner_id" uuid NOT NULL,
  "project_ref" text NOT NULL,
  "role" text NOT NULL,
  "engine" text,
  "session_ref" text NOT NULL,
  "revoked" boolean DEFAULT false
);
CREATE TABLE quorum_private."devices" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "owner_id" uuid NOT NULL,
  "label" text NOT NULL,
  "revoked" boolean DEFAULT false NOT NULL
);
CREATE TABLE quorum_private."conversations" (
  "id" uuid NOT NULL,
  "quorum_id" uuid,
  "sender_id" uuid,
  "recipient_id" uuid,
  "next_sequence" bigint DEFAULT 0 NOT NULL
);
CREATE TABLE quorum_private."messages" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "conversation_id" uuid,
  "sender_id" uuid,
  "recipient_id" uuid,
  "reply_to" uuid,
  "sequence" bigint NOT NULL,
  "body" text NOT NULL
);
CREATE TABLE quorum_private."deliveries" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "message_id" uuid,
  "recipient_id" uuid,
  "requires_handler" boolean DEFAULT true NOT NULL,
  "state" text DEFAULT 'queued'::text NOT NULL,
  "generation" bigint DEFAULT 1,
  "attempt_id" uuid,
  "lease_until" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE quorum_private."attempts" (
  "id" uuid NOT NULL,
  "delivery_id" uuid,
  "generation" bigint NOT NULL,
  "device_id" uuid
);
CREATE TABLE quorum_private."context_versions" (
  "id" bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  "quorum_id" uuid,
  "project_ref" text NOT NULL,
  "author_id" uuid NOT NULL,
  "body" text NOT NULL,
  "source_ref" text NOT NULL
);
CREATE TABLE quorum_private."work_items" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "repository" text NOT NULL,
  "issue_number" bigint NOT NULL,
  "human_owner_id" uuid NOT NULL,
  "criteria" text NOT NULL,
  "dependencies" jsonb DEFAULT '[]'::jsonb NOT NULL
);
CREATE TABLE quorum_private."capacity_reservations" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "quorum_id" uuid,
  "work_id" uuid,
  "agent_id" uuid,
  "generation" bigint DEFAULT 1 NOT NULL,
  "state" text DEFAULT 'implementing'::text NOT NULL,
  "head_sha" text,
  "reviewed_sha" text,
  "reviewer_id" uuid,
  "evidence" text
);
CREATE TABLE quorum_private."audit_events" (
  "id" bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  "actor" uuid NOT NULL,
  "operation" text NOT NULL,
  "quorum_id" uuid,
  "occurred_at" timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE quorum_private."requests" (
  "actor" uuid NOT NULL,
  "operation" text NOT NULL,
  "request_id" text NOT NULL,
  "fingerprint" text NOT NULL,
  "result" jsonb
);
CREATE TABLE quorum_private."session_receipts" (
  "agent_id" uuid NOT NULL,
  "message_id" uuid NOT NULL,
  "queued_at" timestamp with time zone DEFAULT now() NOT NULL
);
ALTER TABLE quorum_private."quorums" ADD CONSTRAINT "quorums_capacity_check" CHECK (((capacity >= 1) AND (capacity <= 100)));
ALTER TABLE quorum_private."quorums" ADD CONSTRAINT "quorums_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."quorums" ADD CONSTRAINT "quorums_slug_check" CHECK ((slug ~ '^[a-z][a-z0-9-]{0,47}$'::text));
ALTER TABLE quorum_private."quorums" ADD CONSTRAINT "quorums_slug_key" UNIQUE (slug);
ALTER TABLE quorum_private."memberships" ADD CONSTRAINT "memberships_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."memberships" ADD CONSTRAINT "memberships_quorum_id_user_id_key" UNIQUE (quorum_id, user_id);
ALTER TABLE quorum_private."memberships" ADD CONSTRAINT "memberships_role_check" CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text])));
ALTER TABLE quorum_private."invitations" ADD CONSTRAINT "invitations_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."invitations" ADD CONSTRAINT "invitations_target_github_id_check" CHECK ((target_github_id ~ '^[0-9]+$'::text));
ALTER TABLE quorum_private."invitations" ADD CONSTRAINT "invitations_token_hash_key" UNIQUE (token_hash);
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_engine_check" CHECK ((engine = ANY (ARRAY['claude'::text, 'codex'::text])));
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_quorum_id_device_id_session_ref_key" UNIQUE (quorum_id, device_id, session_ref);
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_role_check" CHECK ((role ~ '^[a-z][a-z0-9-]{0,47}$'::text));
ALTER TABLE quorum_private."devices" ADD CONSTRAINT "devices_label_check" CHECK (((length(label) >= 1) AND (length(label) <= 100)));
ALTER TABLE quorum_private."devices" ADD CONSTRAINT "devices_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."conversations" ADD CONSTRAINT "conversations_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_body_check" CHECK ((octet_length(body) <= 16384));
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_conversation_id_sequence_key" UNIQUE (conversation_id, sequence);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_attempt_id_key" UNIQUE (attempt_id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_message_id_key" UNIQUE (message_id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_state_check" CHECK ((state = ANY (ARRAY['queued'::text, 'claimed'::text, 'launch_intent'::text, 'running'::text, 'answered'::text, 'cancel_requested'::text, 'cancelled'::text, 'blocked'::text, 'unknown'::text])));
ALTER TABLE quorum_private."attempts" ADD CONSTRAINT "attempts_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."context_versions" ADD CONSTRAINT "context_versions_body_check" CHECK ((octet_length(body) <= 16384));
ALTER TABLE quorum_private."context_versions" ADD CONSTRAINT "context_versions_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."work_items" ADD CONSTRAINT "work_items_criteria_check" CHECK ((length(criteria) > 0));
ALTER TABLE quorum_private."work_items" ADD CONSTRAINT "work_items_issue_number_check" CHECK ((issue_number > 0));
ALTER TABLE quorum_private."work_items" ADD CONSTRAINT "work_items_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."work_items" ADD CONSTRAINT "work_items_repository_issue_number_key" UNIQUE (repository, issue_number);
ALTER TABLE quorum_private."capacity_reservations" ADD CONSTRAINT "capacity_reservations_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."capacity_reservations" ADD CONSTRAINT "capacity_reservations_work_id_key" UNIQUE (work_id);
ALTER TABLE quorum_private."audit_events" ADD CONSTRAINT "audit_events_pkey" PRIMARY KEY (id);
ALTER TABLE quorum_private."requests" ADD CONSTRAINT "requests_pkey" PRIMARY KEY (actor, operation, request_id);
ALTER TABLE quorum_private."requests" ADD CONSTRAINT "requests_request_id_check" CHECK (((length(request_id) >= 1) AND (length(request_id) <= 128)));
ALTER TABLE quorum_private."session_receipts" ADD CONSTRAINT "session_receipts_pkey" PRIMARY KEY (agent_id, message_id);
ALTER TABLE quorum_private."memberships" ADD CONSTRAINT "memberships_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."invitations" ADD CONSTRAINT "invitations_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_device_id_fkey" FOREIGN KEY (device_id) REFERENCES quorum_private.devices(id);
ALTER TABLE quorum_private."agents" ADD CONSTRAINT "agents_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."conversations" ADD CONSTRAINT "conversations_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."conversations" ADD CONSTRAINT "conversations_recipient_id_fkey" FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."conversations" ADD CONSTRAINT "conversations_sender_id_fkey" FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_conversation_id_fkey" FOREIGN KEY (conversation_id) REFERENCES quorum_private.conversations(id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_recipient_id_fkey" FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_reply_to_fkey" FOREIGN KEY (reply_to) REFERENCES quorum_private.messages(id);
ALTER TABLE quorum_private."messages" ADD CONSTRAINT "messages_sender_id_fkey" FOREIGN KEY (sender_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_message_id_fkey" FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."deliveries" ADD CONSTRAINT "deliveries_recipient_id_fkey" FOREIGN KEY (recipient_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."attempts" ADD CONSTRAINT "attempts_delivery_id_fkey" FOREIGN KEY (delivery_id) REFERENCES quorum_private.deliveries(id);
ALTER TABLE quorum_private."attempts" ADD CONSTRAINT "attempts_device_id_fkey" FOREIGN KEY (device_id) REFERENCES quorum_private.devices(id);
ALTER TABLE quorum_private."context_versions" ADD CONSTRAINT "context_versions_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."work_items" ADD CONSTRAINT "work_items_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."capacity_reservations" ADD CONSTRAINT "capacity_reservations_agent_id_fkey" FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."capacity_reservations" ADD CONSTRAINT "capacity_reservations_quorum_id_fkey" FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);
ALTER TABLE quorum_private."capacity_reservations" ADD CONSTRAINT "capacity_reservations_work_id_fkey" FOREIGN KEY (work_id) REFERENCES quorum_private.work_items(id);
ALTER TABLE quorum_private."session_receipts" ADD CONSTRAINT "session_receipts_agent_id_fkey" FOREIGN KEY (agent_id) REFERENCES quorum_private.agents(id);
ALTER TABLE quorum_private."session_receipts" ADD CONSTRAINT "session_receipts_message_id_fkey" FOREIGN KEY (message_id) REFERENCES quorum_private.messages(id);
CREATE INDEX delivery_pending ON quorum_private.deliveries USING btree (recipient_id, created_at) WHERE (state = 'queued'::text);
CREATE OR REPLACE FUNCTION public.invite_create(p_quorum_id uuid, p_target_github_id text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('invite_create',jsonb_build_object('quorum_id',p_quorum_id,'target_github_id',p_target_github_id),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.device_register(p_label text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('device_register',jsonb_build_object('label',p_label),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.quorum_create(p_slug text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('quorum_create',jsonb_build_object('slug',p_slug),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.invite_accept(p_token text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('invite_accept',jsonb_build_object('token',p_token),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.member_revoke(p_quorum_id uuid, p_member_id uuid, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('member_revoke',jsonb_build_object('quorum_id',p_quorum_id,'member_id',p_member_id),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.agent_join(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('agent_join',jsonb_build_object('quorum_id',p_quorum_id,'device_id',p_device_id,'project_ref',p_project_ref,'role',p_role,'engine',p_engine,'session_ref',p_session_ref),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.agent_status(p_agent_id uuid)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('agent_status',jsonb_build_object('agent_id',p_agent_id),null) $function$
;
CREATE OR REPLACE FUNCTION public.agent_leave(p_agent_id uuid, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('agent_leave',jsonb_build_object('agent_id',p_agent_id),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.context_read(p_quorum_id uuid, p_project_ref text, p_after_version bigint)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('context_read',jsonb_build_object('quorum_id',p_quorum_id,'project_ref',p_project_ref,'after_version',p_after_version),null) $function$
;
CREATE OR REPLACE FUNCTION public.message_send(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('message_send',jsonb_build_object('quorum_id',p_quorum_id,'sender_agent_id',p_sender_agent_id,'conversation_id',p_conversation_id,'recipient_agent_id',p_recipient_agent_id,'reply_to',p_reply_to,'body',p_body),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.inbox_claim(p_device_id uuid, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('inbox_claim',jsonb_build_object('device_id',p_device_id),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.attempt_update(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('attempt_update',jsonb_build_object('attempt_id',p_attempt_id,'generation',p_generation,'event',p_event),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.delivery_complete(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('delivery_complete',jsonb_build_object('attempt_id',p_attempt_id,'generation',p_generation,'reply_body',p_reply_body),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.work_register(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('work_register',jsonb_build_object('quorum_id',p_quorum_id,'repository',p_repository,'issue_number',p_issue_number,'human_owner_id',p_human_owner_id,'criteria',p_criteria,'dependencies',p_dependencies),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.work_admit(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('work_admit',jsonb_build_object('quorum_id',p_quorum_id,'work_id',p_work_id,'agent_id',p_agent_id),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION public.work_transition(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('work_transition',jsonb_build_object('assignment_id',p_assignment_id,'generation',p_generation,'event',p_event,'evidence_ref',p_evidence_ref),p_request_id) $function$
;
CREATE OR REPLACE FUNCTION quorum_private.member(q uuid, who uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare r text; begin
 select role into r from quorum_private.memberships where quorum_id=q and user_id=who and not revoked;
 if r is null then raise insufficient_privilege using message='membership_required'; end if;
 return r;
end $function$
;
CREATE OR REPLACE FUNCTION quorum_private.owned_agent(a uuid, who uuid)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare q uuid; begin
 select ag.quorum_id into q from quorum_private.agents ag join quorum_private.devices d on d.id=ag.device_id
 where ag.id=a and ag.owner_id=who and d.owner_id=who and not ag.revoked and not d.revoked;
 if q is null then raise insufficient_privilege using message='agent_denied'; end if;
 perform quorum_private.member(q,who); return q;
end $function$
;
CREATE OR REPLACE FUNCTION quorum_private.call(op text, p jsonb, req text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
 if not exists(select 1 from auth.identities where user_id=who and provider='github') then
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
end $function$
;
CREATE OR REPLACE FUNCTION public.conversation_read(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint)
 RETURNS jsonb
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$ select quorum_private.call('conversation_read',jsonb_build_object('quorum_id',p_quorum_id,'agent_id',p_agent_id,'conversation_id',p_conversation_id,'after_sequence',p_after_sequence),null) $function$
;
CREATE OR REPLACE FUNCTION public.quorum_lookup(p_slug text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare q uuid; begin
 select id into q from quorum_private.quorums where slug=p_slug;
 perform quorum_private.member(q,auth.uid());
 return jsonb_build_object('quorum_id',q);
end $function$
;
CREATE OR REPLACE FUNCTION public.quorum_check_owner(p_quorum_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
begin
 if quorum_private.member(p_quorum_id,auth.uid())<>'owner' then raise insufficient_privilege; end if;
 return 'true'::jsonb;
end $function$
;
CREATE OR REPLACE FUNCTION public.resolve_role(p_quorum_id uuid, p_role text, p_project_ref text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare ids uuid[]; begin
 perform quorum_private.member(p_quorum_id,auth.uid());
 select array_agg(a.id) into ids from quorum_private.agents a
 join quorum_private.devices d on d.id=a.device_id
 join quorum_private.memberships m on m.user_id=a.owner_id and m.quorum_id=a.quorum_id
 where a.quorum_id=p_quorum_id and a.role=p_role and a.project_ref=p_project_ref and not a.revoked and not d.revoked and not m.revoked;
 if coalesce(cardinality(ids),0)<>1 then raise exception 'recipient_ambiguous_or_missing'; end if;
 return jsonb_build_object('agent_id',ids[1]);
end $function$
;
CREATE OR REPLACE FUNCTION public.session_inbox(p_agent_id uuid, p_session_ref text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
end $function$
;
CREATE OR REPLACE FUNCTION quorum_private.native_session(p_agent_id uuid, p_session_ref text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare q uuid;
begin
 if auth.uid() is null or not exists(select 1 from auth.identities where user_id=auth.uid() and provider='github') then
  raise insufficient_privilege using message='github_identity_required'; end if;
 q:=quorum_private.owned_agent(p_agent_id,auth.uid());
 if not exists(select 1 from quorum_private.agents where id=p_agent_id and engine in ('codex','claude') and session_ref=p_session_ref) then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 return q;
end $function$
;
CREATE OR REPLACE FUNCTION public.session_ack(p_agent_id uuid, p_session_ref text, p_message_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages where id=p_message_id and quorum_id=q and recipient_id=p_agent_id) then
  raise insufficient_privilege using message='message_denied'; end if;
 insert into quorum_private.session_receipts(agent_id,message_id) values(p_agent_id,p_message_id) on conflict do nothing;
 return jsonb_build_object('queued',true);
end $function$
;
CREATE OR REPLACE FUNCTION public.session_reply(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
end $function$
;
REVOKE ALL ON SCHEMA quorum_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA quorum_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA quorum_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION "public"."invite_create"(p_quorum_id uuid, p_target_github_id text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."invite_create"(p_quorum_id uuid, p_target_github_id text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."invite_create"(p_quorum_id uuid, p_target_github_id text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."device_register"(p_label text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."device_register"(p_label text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."device_register"(p_label text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."quorum_create"(p_slug text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."quorum_create"(p_slug text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."quorum_create"(p_slug text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."invite_accept"(p_token text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."invite_accept"(p_token text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."invite_accept"(p_token text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."member_revoke"(p_quorum_id uuid, p_member_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."member_revoke"(p_quorum_id uuid, p_member_id uuid, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."member_revoke"(p_quorum_id uuid, p_member_id uuid, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."agent_join"(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."agent_join"(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."agent_join"(p_quorum_id uuid, p_device_id uuid, p_project_ref text, p_role text, p_engine text, p_session_ref text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."agent_status"(p_agent_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."agent_status"(p_agent_id uuid) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."agent_status"(p_agent_id uuid) TO "service_role";
REVOKE ALL ON FUNCTION "public"."agent_leave"(p_agent_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."agent_leave"(p_agent_id uuid, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."agent_leave"(p_agent_id uuid, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."context_read"(p_quorum_id uuid, p_project_ref text, p_after_version bigint) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."context_read"(p_quorum_id uuid, p_project_ref text, p_after_version bigint) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."context_read"(p_quorum_id uuid, p_project_ref text, p_after_version bigint) TO "service_role";
REVOKE ALL ON FUNCTION "public"."message_send"(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."message_send"(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."message_send"(p_quorum_id uuid, p_sender_agent_id uuid, p_conversation_id uuid, p_recipient_agent_id uuid, p_reply_to uuid, p_body text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."inbox_claim"(p_device_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."inbox_claim"(p_device_id uuid, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."inbox_claim"(p_device_id uuid, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."attempt_update"(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."attempt_update"(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."attempt_update"(p_attempt_id uuid, p_generation bigint, p_event text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."delivery_complete"(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."delivery_complete"(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."delivery_complete"(p_attempt_id uuid, p_generation bigint, p_reply_body text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."work_register"(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."work_register"(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."work_register"(p_quorum_id uuid, p_repository text, p_issue_number bigint, p_human_owner_id uuid, p_criteria text, p_dependencies jsonb, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."work_admit"(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."work_admit"(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."work_admit"(p_quorum_id uuid, p_work_id uuid, p_agent_id uuid, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."work_transition"(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."work_transition"(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."work_transition"(p_assignment_id uuid, p_generation bigint, p_event text, p_evidence_ref text, p_request_id text) TO "service_role";
REVOKE ALL ON FUNCTION "quorum_private"."member"(q uuid, who uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION "quorum_private"."owned_agent"(a uuid, who uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION "quorum_private"."call"(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION "public"."conversation_read"(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."conversation_read"(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."conversation_read"(p_quorum_id uuid, p_agent_id uuid, p_conversation_id uuid, p_after_sequence bigint) TO "service_role";
REVOKE ALL ON FUNCTION "public"."quorum_lookup"(p_slug text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."quorum_lookup"(p_slug text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."quorum_lookup"(p_slug text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."quorum_check_owner"(p_quorum_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."quorum_check_owner"(p_quorum_id uuid) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."quorum_check_owner"(p_quorum_id uuid) TO "service_role";
REVOKE ALL ON FUNCTION "public"."resolve_role"(p_quorum_id uuid, p_role text, p_project_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."resolve_role"(p_quorum_id uuid, p_role text, p_project_ref text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."resolve_role"(p_quorum_id uuid, p_role text, p_project_ref text) TO "service_role";
REVOKE ALL ON FUNCTION "public"."session_inbox"(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."session_inbox"(p_agent_id uuid, p_session_ref text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."session_inbox"(p_agent_id uuid, p_session_ref text) TO "service_role";
REVOKE ALL ON FUNCTION "quorum_private"."native_session"(p_agent_id uuid, p_session_ref text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION "public"."session_ack"(p_agent_id uuid, p_session_ref text, p_message_id uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."session_ack"(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."session_ack"(p_agent_id uuid, p_session_ref text, p_message_id uuid) TO "service_role";
REVOKE ALL ON FUNCTION "public"."session_reply"(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION "public"."session_reply"(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text) TO "authenticated";
GRANT EXECUTE ON FUNCTION "public"."session_reply"(p_agent_id uuid, p_session_ref text, p_message_id uuid, p_body text) TO "service_role";
ALTER TABLE quorum_private."quorums" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."memberships" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."invitations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."agents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."devices" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."conversations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."messages" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."deliveries" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."attempts" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."context_versions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."work_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."capacity_reservations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."audit_events" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."requests" ENABLE ROW LEVEL SECURITY;
ALTER TABLE quorum_private."session_receipts" ENABLE ROW LEVEL SECURITY;
