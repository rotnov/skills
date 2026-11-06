-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106130000_task_authority_project_bound.sql.
-- No required-release change, table grant, policy, task authority or model launch. (#142)
--
-- `session reconcile --confirmed-absent` must learn whether one exact message is
-- still unreceipted. session_inbox returns only the priority-selected head, so a
-- newer urgent message hid the answer and denied the owner's attestation.
-- session_message_unreceipted answers that one question for the caller's own
-- native session. It returns the message UUID the caller supplied and one boolean,
-- never a body or sender, and the same `false` for a receipted message and for
-- one that is not addressed to this agent. Reads take no locks and it writes
-- nothing, so an older client is unaffected and a client on a server without
-- this function falls back to the head check.
--
-- The predicate is the reviewed inbox predicate (170000) for one message ID.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The inbox predicate copied below (170000), kept behind the update wrapper (20261030140000).
 'quorum_private.session_inbox_before_updates(uuid,text)','93b2f7f232bc3b318114192dac7a597bf8ab700cd0a4158daf081f94337f3e79',
 -- The binding check the new function relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The task authority this release runs beside: the project-bound body (20261106130000).
 'public.session_task_authority(uuid,text,uuid)','5d0c0bdfdff7083906dad012f02ee4ef36f672f6e2c3fc822cea452115f2965b',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('public.session_message_unreceipted(uuid,text,uuid)') is not null then
  raise exception 'message_unreceipted_present'; end if;
 if to_regprocedure('quorum_private.session_task_authority_before_project_bound(uuid,text,uuid)') is null then
  raise exception 'task_authority_bound_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create function public.session_message_unreceipted(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 return jsonb_build_object('message_id',p_message_id,'unreceipted',exists(
  select 1 from quorum_private.messages m join quorum_private.deliveries d on d.message_id=m.id
  where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id
   and not exists(select 1 from quorum_private.session_receipts r where r.agent_id=p_agent_id and r.message_id=m.id)));
end $function$;
revoke all on function public.session_message_unreceipted(uuid,text,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_message_unreceipted(uuid,text,uuid) to authenticated;
