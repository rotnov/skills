-- Owner-applied engine extension. No new table/policy/RPC or broader grants.
-- All existing GitHub, ownership, membership, device and exact-session checks remain.
alter table quorum_private.agents drop constraint agents_engine_check;
alter table quorum_private.agents add constraint agents_engine_check
 check (engine in ('claude','codex','grok'));

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
 if not exists(select 1 from quorum_private.agents where id=p_agent_id and engine in ('codex','claude','grok') and session_ref=p_session_ref) then
  raise insufficient_privilege using message='session_binding_denied'; end if;
 return q;
end $function$
;
revoke all on function quorum_private.native_session(uuid,text) from public,anon,authenticated,service_role;

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
    and a.engine in ('codex','claude','grok') and not a.revoked and not d.revoked and not m.revoked
  )
$$;
revoke all on function quorum_private.receive_wakeup(text) from public,anon,authenticated,service_role;
-- Referenced by the stored RLS expression; this grants no schema USAGE, table
-- access or publicly exposed RPC. Callers can learn only their own access bit.
grant execute on function quorum_private.receive_wakeup(text) to authenticated;
