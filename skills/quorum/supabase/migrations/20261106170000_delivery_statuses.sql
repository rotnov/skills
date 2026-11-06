-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106160000_session_message.sql.
-- No required-release change, table grant, policy, task authority or model launch. (#162)
--
-- What one recipient reports about one message, for its sender. A message has exactly
-- one recipient, so one row per message is one row per message and recipient. A status
-- is a fact and never a grant: nothing here or anywhere else reads one to decide a
-- permission, and no existing dispatcher, heartbeat or inbox body changes.
--
-- A row exists only once a recipient's client or chat reports something, or a reply is
-- recorded. No heartbeat touches this table, and Hello, roster and maintenance notices
-- never get a row. session_ack keeps its meaning (it moves the queue) and writes no
-- status: only the proof-carrying session_delivery_mark records `read`, so a receipt
-- from an older client, an attestation or an automatic park never claims one.
-- `answered` alone is written by the server, from the delivery session_reply marks.
--
-- Each change bumps a per-sender sequence under that sender's stream row lock, so
-- sequences commit in order and a cursor cannot pass an uncommitted lower one. It then
-- sends an empty `status_changed` hint on the sender's private agent topic, as the
-- inbox does; a client that predates this ignores the event.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The binding check every new function relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The body that marks a delivery `answered`, which the new trigger follows.
 'public.session_reply(uuid,text,uuid,text,text)','f10c1f329a50c1f19217603b8f02221f58b943e14aa0607a04da53ed9797fac2',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regclass('quorum_private.delivery_statuses') is not null then
  raise exception 'delivery_status_present'; end if;
 if to_regprocedure('public.session_message(uuid,text,uuid)') is null then
  raise exception 'session_message_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create table quorum_private.delivery_status_streams(
 sender_id uuid primary key references quorum_private.agents(id),
 last_seq bigint not null default 0);
create table quorum_private.delivery_statuses(
 message_id uuid primary key references quorum_private.messages(id),
 quorum_id uuid not null references quorum_private.quorums(id),
 sender_id uuid not null references quorum_private.agents(id),
 recipient_id uuid not null references quorum_private.agents(id),
 status text not null check(status in ('sent','queued','read','answered','in_progress','deferred','needs_owner',
  'declined','done','failed')),
 status_at timestamptz not null default now(),
 reason text check(octet_length(reason) between 1 and 512),
 proof text check(proof in ('received','body_read','reply')),
 queued_at timestamptz, read_at timestamptz, answered_at timestamptz,
 seq bigint not null);
create unique index delivery_statuses_feed on quorum_private.delivery_statuses(sender_id,seq);
-- Bounds the stuck summary to one quorum's recent deliveries.
create index deliveries_quorum_recent on quorum_private.deliveries(quorum_id,created_at desc);
alter table quorum_private.delivery_status_streams enable row level security;
alter table quorum_private.delivery_statuses enable row level security;
revoke all on quorum_private.delivery_status_streams,quorum_private.delivery_statuses
 from public,anon,authenticated,service_role;

-- Transport and maintenance notices are the server's own and carry no status.
create function quorum_private.delivery_status_applies(p_message_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog as $function$
 select not exists(select 1 from quorum_private.session_notices n where n.message_id=p_message_id)
  and not exists(select 1 from quorum_private.update_notices u where u.message_id=p_message_id)
$function$;

-- The only writer. Delivery facts only move forward and keep their first time. An
-- explicit status replaces an earlier one until `declined`, `done` or `failed` ends it.
create function quorum_private.delivery_status_apply(p_message quorum_private.messages,p_status text,p_reason text,
 p_proof text)
returns void language plpgsql security definer set search_path=pg_catalog as $function$
declare next_seq bigint; was quorum_private.delivery_statuses%rowtype; now_is quorum_private.delivery_statuses%rowtype;
 fresh boolean:=false;
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
 if now_is.status<>was.status then now_is.status_at:=now(); end if;
 now_is.seq:=next_seq;
 insert into quorum_private.delivery_statuses values(now_is.*)
  on conflict(message_id) do update set status=excluded.status,status_at=excluded.status_at,reason=excluded.reason,
   proof=excluded.proof,queued_at=excluded.queued_at,read_at=excluded.read_at,answered_at=excluded.answered_at,
   seq=excluded.seq;
 begin
  perform realtime.send('{}'::jsonb,'status_changed','quorum:agent:'||p_message.sender_id::text,true);
 exception when others then null; -- A hint is never worth a durable status.
 end;
end $function$;

-- Follows session_reply. A status failure must never undo the reply it reports.
create function quorum_private.delivery_status_answered()
returns trigger language plpgsql security definer set search_path=pg_catalog as $function$
declare m quorum_private.messages%rowtype;
begin
 select * into m from quorum_private.messages where id=new.message_id;
 if found and quorum_private.delivery_status_applies(m.id) then
  perform quorum_private.delivery_status_apply(m,'answered',null,null); end if;
 return new;
exception when others then return new;
end $function$;
create trigger delivery_status_answered after update of state on quorum_private.deliveries
 for each row when (new.state='answered' and old.state is distinct from 'answered')
 execute function quorum_private.delivery_status_answered();

create function quorum_private.delivery_status_view(p_message_id uuid)
returns jsonb language sql stable security definer set search_path=pg_catalog as $function$
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
$function$;

-- Only the message's own recipient, from its exact native session, reports a status.
create function public.session_delivery_mark(p_agent_id uuid,p_session_ref text,p_message_id uuid,p_status text,
 p_reason text default null,p_proof text default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
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
end $function$;

-- Only its sender or recipient. Anyone else, an unknown message and a notice all get the same null.
create function public.session_delivery_status(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 if not exists(select 1 from quorum_private.messages m where m.id=p_message_id and m.quorum_id=q
               and p_agent_id in (m.sender_id,m.recipient_id))
    or not quorum_private.delivery_status_applies(p_message_id) then
  return null; end if;
 return quorum_private.delivery_status_view(p_message_id);
end $function$;

-- The sender's feed after a hint: only its own messages, oldest change first. A reason is
-- peer text, so it stays out of this feed and of the sender's local state.
create function public.session_delivery_changes(p_agent_id uuid,p_session_ref text,p_after bigint default 0)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
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
end $function$;

-- The sender's open outgoing messages from the last seven days, derived from server time
-- on each read. A reply is not chased. A receipt with no status row is a recipient client
-- that predates this release, which proves neither read nor unread, so it is left out.
create function public.session_delivery_stuck(p_agent_id uuid,p_session_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
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
end $function$;

revoke all on function quorum_private.delivery_status_applies(uuid),
 quorum_private.delivery_status_apply(quorum_private.messages,text,text,text),
 quorum_private.delivery_status_answered(),quorum_private.delivery_status_view(uuid)
 from public,anon,authenticated,service_role;
revoke all on function public.session_delivery_mark(uuid,text,uuid,text,text,text),
 public.session_delivery_status(uuid,text,uuid),public.session_delivery_changes(uuid,text,bigint),
 public.session_delivery_stuck(uuid,text) from public,anon,authenticated,service_role;
grant execute on function public.session_delivery_mark(uuid,text,uuid,text,text,text),
 public.session_delivery_status(uuid,text,uuid),public.session_delivery_changes(uuid,text,bigint),
 public.session_delivery_stuck(uuid,text) to authenticated;
