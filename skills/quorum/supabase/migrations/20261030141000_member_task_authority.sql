-- Source only: reviewed administrator application is separate from installing the skill.
-- Read-only authority for one addressed message. Historical delivery does not prove
-- the sender is still a member. No message body or credential crosses this RPC.
create or replace function public.session_task_authority(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
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
end $function$;
revoke all on function public.session_task_authority(uuid,text,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_task_authority(uuid,text,uuid) to authenticated;
