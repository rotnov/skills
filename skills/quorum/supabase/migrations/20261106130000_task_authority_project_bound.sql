-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106120000_hello_reconnect_dedupe.sql.
-- No required-release change, table grant, policy, response field or model launch. (#143)
--
-- Join admits project refs up to 500 characters from an ASCII alphabet, so up to 500
-- octets, but session_task_authority stopped at 256: an agent with a longer ref passed
-- every other RPC and was denied here with session_binding_denied. The new body is the
-- reviewed one with that bound raised to 500 (the line marked "#143"). The denial itself
-- stays for a missing or longer ref. The reviewed body moves to a private function kept
-- for rollback.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The reviewed body (20261030141000); copied below with its bound raised.
 'public.session_task_authority(uuid,text,uuid)','4366ec8288290632601454a8eccf39fccbd20cb7264ee07c5dca42118f724e09',
 -- The binding check the copied body relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.session_task_authority_before_project_bound(uuid,text,uuid)') is not null then
  raise exception 'task_authority_bound_present'; end if;
 if to_regclass('quorum_private.session_hello_sent') is null then
  raise exception 'hello_dedupe_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

alter function public.session_task_authority(uuid,text,uuid) set schema quorum_private;
alter function quorum_private.session_task_authority(uuid,text,uuid) rename to session_task_authority_before_project_bound;
revoke all on function quorum_private.session_task_authority_before_project_bound(uuid,text,uuid)
 from public,anon,authenticated,service_role;

create function public.session_task_authority(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
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
end $function$;
revoke all on function public.session_task_authority(uuid,text,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_task_authority(uuid,text,uuid) to authenticated;
