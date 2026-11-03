-- Disposable PostgreSQL only. Runs after the Cursor engine extension under the
-- current release: a bc- session keeps the same owner/session contract as the
-- other native clients, including wakeups, health, peers and revocation.
do $test$
declare a quorum_private.agents%rowtype; b quorum_private.agents%rowtype; peer_engine text;
 target uuid:=gen_random_uuid(); sid text:='bc-'||gen_random_uuid()::text; c uuid:=gen_random_uuid();
 sent jsonb; result jsonb;
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
begin
 -- Both pilot members must still be active.
 select x.* into a from quorum_private.agents x
  join quorum_private.devices d on d.id=x.device_id and d.owner_id=x.owner_id and not d.revoked
  join quorum_private.memberships m on m.quorum_id=x.quorum_id and m.user_id=x.owner_id and not m.revoked
  where not x.revoked and exists(select 1 from quorum_private.agents y
   join quorum_private.devices e on e.id=y.device_id and e.owner_id=y.owner_id and not e.revoked
   join quorum_private.memberships n on n.quorum_id=y.quorum_id and n.user_id=y.owner_id and not n.revoked
   where y.quorum_id=x.quorum_id and y.owner_id<>x.owner_id and not y.revoked) limit 1;
 if not found then raise exception 'two_pilot_members_required'; end if;
 select y.* into b from quorum_private.agents y
  join quorum_private.devices e on e.id=y.device_id and e.owner_id=y.owner_id and not e.revoked
  join quorum_private.memberships n on n.quorum_id=y.quorum_id and n.user_id=y.owner_id and not n.revoked
  where y.quorum_id=a.quorum_id and y.owner_id<>a.owner_id and not y.revoked limit 1;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(target,a.quorum_id,a.device_id,a.owner_id,b.project_ref,'cursor-test','cursor',sid);
 select engine into peer_engine from quorum_private.agents where id=b.id;
 update quorum_private.agents set engine='codex' where id=b.id;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 begin perform public.session_inbox(target,sid); raise exception 'cross_owner_allowed';
 exception when insufficient_privilege then null; end;
 sent:=public.message_send(a.quorum_id,b.id,c,target,null,'cursor transport test','cursor-test:'||c);
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 begin perform public.session_inbox(target,'bc-'||gen_random_uuid()::text); raise exception 'foreign_session_allowed';
 exception when insufficient_privilege then null; end;
 if jsonb_array_length(public.session_inbox(target,sid))<>1 then raise exception 'cursor_inbox_missing'; end if;
 if not quorum_private.receive_wakeup('quorum:agent:'||target) then raise exception 'cursor_wake_denied'; end if;
 result:=public.session_heartbeat(target,sid,gen_random_uuid(),p,v);
 if (result->>'online')::boolean is not true then raise exception 'cursor_heartbeat_refused'; end if;
 -- The connection's Hello reaches the native peer in the same project.
 if not exists(select 1 from quorum_private.session_notices n join quorum_private.deliveries d on d.message_id=n.message_id
   where n.agent_id=target and d.recipient_id=b.id) then raise exception 'cursor_hello_missing'; end if;
 perform public.session_ack(target,sid,(sent->>'message_id')::uuid);
 if jsonb_array_length(public.session_inbox(target,sid))<>0 then raise exception 'cursor_ack_not_persisted'; end if;
 perform set_config('request.jwt.claim.sub',b.owner_id::text,true);
 if not exists(select 1 from jsonb_array_elements(public.session_peers(b.id,b.session_ref)) x
   where x->>'agent_id'=target::text and x->>'engine'='cursor' and x->>'availability'='online') then
  raise exception 'cursor_peer_missing'; end if;
 perform set_config('request.jwt.claim.sub',a.owner_id::text,true);
 update quorum_private.agents set revoked=true where id=target;
 if quorum_private.receive_wakeup('quorum:agent:'||target) then raise exception 'revoked_cursor_wake'; end if;
 begin perform public.session_inbox(target,sid); raise exception 'revoked_cursor_inbox';
 exception when insufficient_privilege then null; end;
 update quorum_private.agents set engine=peer_engine where id=b.id;
end $test$;
