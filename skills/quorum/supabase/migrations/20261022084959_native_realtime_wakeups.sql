-- Owner-applied extension. Never run by skill installation or session start.
-- Realtime is a hint, not the message store: no text, ID or credential payload.
create or replace function quorum_private.receive_wakeup(p_topic text)
returns boolean language sql stable security definer set search_path=pg_catalog as $$
 select auth.uid() is not null
  and exists(select 1 from auth.identities where user_id=auth.uid() and provider='github')
  and exists(
   select 1 from quorum_private.agents a
   join quorum_private.devices d on d.id=a.device_id
   join quorum_private.memberships m on m.quorum_id=a.quorum_id and m.user_id=a.owner_id
   where p_topic='quorum:agent:'||a.id::text
    and a.owner_id=auth.uid() and d.owner_id=auth.uid()
    and a.engine in ('codex','claude') and not a.revoked and not d.revoked and not m.revoked
  )
$$;
revoke all on function quorum_private.receive_wakeup(text) from public,anon,authenticated,service_role;
-- Referenced by the stored RLS expression; this grants no schema USAGE, table
-- access or publicly exposed RPC. Callers can learn only their own access bit.
grant execute on function quorum_private.receive_wakeup(text) to authenticated;

-- Do not alter any Realtime-owned object or existing unrelated policy. Realtime
-- manages its table, RLS and grants; only this application's policy is added.
create policy quorum_agent_wake_receive on realtime.messages for select to authenticated
 using (extension='broadcast' and topic=(select realtime.topic())
        and (select quorum_private.receive_wakeup(realtime.topic())));
-- No INSERT policy: the client is receive-only. Never add a broad allow policy.

create or replace function quorum_private.notify_inbox()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
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
revoke all on function quorum_private.notify_inbox() from public,anon,authenticated,service_role;

create trigger quorum_delivery_wake after insert on quorum_private.deliveries
 for each row execute function quorum_private.notify_inbox();
-- A native Claude receipt removes a submitted head from the durable inbox.
-- Wake this listener to drain an already queued next message immediately.
create trigger quorum_receipt_wake after insert on quorum_private.session_receipts
 for each row execute function quorum_private.notify_inbox();
