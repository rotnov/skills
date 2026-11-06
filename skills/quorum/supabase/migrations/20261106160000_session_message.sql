-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106150000_hello_sent_skipped.sql.
-- No required-release change, table grant, policy, task authority or model launch. (#150)
--
-- A Codex chat reads each queued message's body from the service. conversation_read
-- returns up to 16 rows, so 16 maximum-size bodies reach the client's 262,144-byte
-- response limit and none of them can be read. session_message returns exactly one
-- message to the caller's own native session, which always fits. It returns the
-- same null for a message addressed to another agent and for one that does not
-- exist, so a caller learns nothing about either. It reads no receipt and writes
-- nothing, so a message stays readable before and after its receipt. A client on a
-- server without this function falls back to conversation_read.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The binding check the new function relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('public.session_message(uuid,text,uuid)') is not null then
  raise exception 'session_message_present'; end if;
 if to_regprocedure('quorum_private.session_heartbeat_hello_before_split(uuid,text,uuid,integer,text)') is null then
  raise exception 'hello_split_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create function public.session_message(p_agent_id uuid,p_session_ref text,p_message_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare q uuid; result jsonb;
begin
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select to_jsonb(x) into result from (
  select m.id,m.conversation_id,m.sequence,m.sender_id,m.recipient_id,m.reply_to,m.body
  from quorum_private.messages m
  where m.id=p_message_id and m.quorum_id=q and m.recipient_id=p_agent_id) x;
 return result;
end $function$;
revoke all on function public.session_message(uuid,text,uuid) from public,anon,authenticated,service_role;
grant execute on function public.session_message(uuid,text,uuid) to authenticated;
