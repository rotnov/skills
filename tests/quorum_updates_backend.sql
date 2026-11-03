-- Actual disposable PostgreSQL only. This suite does not contact a service.
create function quorum_private.synthetic_update_failure() returns trigger language plpgsql as $$
begin
 if new.recipient_id::text=current_setting('quorum_test.update_fail_recipient',true) then
  raise exception 'recipient_unavailable'; end if;
 return new;
end $$;
create trigger synthetic_update_failure before insert on quorum_private.messages
 for each row execute function quorum_private.synthetic_update_failure();
do $test$
declare
 owner_id uuid:='11111111-1111-4111-8111-111111111111';
 q uuid:=gen_random_uuid(); own uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 sid text:=gen_random_uuid()::text; other uuid:=gen_random_uuid(); peer uuid; peer_sid text;
 result jsonb; repeated jsonb; notice jsonb; status jsonb; amount integer; mid uuid;
 target jsonb:=jsonb_build_object('protocol_version',2,'skill_version','0.3.0','content_sha256',repeat('a',64));
 op uuid; nid uuid; cursor uuid; seen integer:=0;
begin
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000091');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'update-suite',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(own,q,dev,owner_id,'one','initiator','codex',sid);
 -- More than one current session_peers page; projects and native engines differ.
 for amount in 1..135 loop
  insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
  values(q,dev,owner_id,case when amount%2=0 then 'two' else 'one' end,'peer-'||amount,
   case when amount%3=0 then 'grok' when amount%3=1 then 'claude' else 'codex' end,gen_random_uuid()::text);
 end loop;
 insert into quorum_private.agents(quorum_id,device_id,owner_id,project_ref,role,engine,session_ref,revoked)
 values(q,dev,owner_id,'one','revoked','codex',gen_random_uuid()::text,true);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 result:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'first');
 if result->>'queued'<>'135' or result->>'existing'<>'0' then raise exception 'incomplete_all_quorum_fanout: %',result; end if;
 op:=(result->>'operation_id')::uuid;
 if (select count(*) from quorum_private.update_notices)<>135 then raise exception 'notice_count_wrong'; end if;
 if exists(select 1 from quorum_private.deliveries d join quorum_private.update_notices n on n.message_id=d.message_id
   where d.requires_handler) then raise exception 'maintenance_can_launch_handler'; end if;
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'first');
 if repeated<>result then raise exception 'lost_response_replay_changed'; end if;
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'again');
 if repeated->>'queued'<>'0' or repeated->>'existing'<>'135' then raise exception 'duplicate_invocation'; end if;
 if (select count(*) from quorum_private.messages)<>135 then raise exception 'update_storm'; end if;
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref)
 values(other,q,dev,owner_id,'late','late','codex',gen_random_uuid()::text);
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'first');
 if repeated<>result then raise exception 'retry_changed_snapshot'; end if;
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'include-late');
 if repeated->>'queued'<>'1' or repeated->>'existing'<>'135' then raise exception 'late_participant_missing'; end if;
 -- Report/status pagination cannot silently truncate the participant set.
 loop
  status:=public.session_update_status(own,sid,q,op,null,cursor);
  seen:=seen+jsonb_array_length(status->'participants');
  cursor:=(status->>'next_after_agent')::uuid;
  exit when cursor is null;
 end loop;
 if seen<>135 then raise exception 'status_truncated: %',seen; end if;
 select n.id,n.recipient_id,n.message_id into nid,peer,mid from quorum_private.update_notices n limit 1;
 select session_ref into peer_sid from quorum_private.agents where id=peer;
 notice:=public.session_inbox(peer,peer_sid)->0;
 if notice->'maintenance'->>'kind'<>'quorum.update' or notice->'maintenance'->'target'<>target then
  raise exception 'typed_maintenance_missing: %',notice; end if;
 if coalesce((notice->>'notification')::boolean,false) then raise exception 'update_misclassified_terminal'; end if;
 -- An actual bound receipt records consumption and owner action, never a download/runtime.
 perform public.session_ack(peer,peer_sid,mid);
 status:=public.session_update_status(peer,peer_sid,q,null,nid,null);
 if status->>'state'<>'owner_action_required' or status->'refreshed_at'<>'null'::jsonb then
  raise exception 'receipt_claimed_refresh: %',status; end if;
 perform public.session_update_report(peer,peer_sid,q,nid,'blocked','{"code":"update_tool_missing"}','blocked');
 status:=public.session_update_status(peer,peer_sid,q,null,nid,null);
 if status->>'state'<>'blocked' or status->>'blocked_code'<>'update_tool_missing' then raise exception 'blocker_missing'; end if;
 perform public.session_update_report(peer,peer_sid,q,nid,'refreshed',target,'reported');
 perform public.session_update_report(peer,peer_sid,q,nid,'refreshed',target,'reported');
 status:=public.session_update_status(peer,peer_sid,q,null,nid,null);
 if status->>'state'<>'refreshed' or status->'active_at'<>'null'::jsonb then raise exception 'download_claimed_runtime'; end if;
 perform public.session_update_report(peer,peer_sid,q,nid,'active',target||jsonb_build_object('run_id',gen_random_uuid()),'running');
 status:=public.session_update_status(peer,peer_sid,q,null,nid,null);
 if status->>'state'<>'active_reported' then raise exception 'runtime_report_missing'; end if;
 begin
  perform public.session_update_report(own,sid,q,nid,'refreshed',target,'stolen');
  raise exception 'foreign_binding_report_accepted';
 exception when insufficient_privilege then null; end;
 begin
  perform public.session_update_request(own,sid,q,2,'9.9.9',repeat('a',64),'conflict');
  raise exception 'same_hash_changed_versions';
 exception when raise_exception then
  if sqlerrm<>'update_target_conflict' then raise; end if;
 end;
 begin
  perform public.session_update_request(own,sid,q,2,'0.3.0','https://untrusted.invalid/execute','invalid');
  raise exception 'executable_target_accepted';
 exception when raise_exception then
  if sqlerrm<>'update_target_invalid' then raise; end if;
 end;
 -- Version stays unchanged while content changes: this is a distinct maintenance target.
 perform set_config('quorum_test.update_fail_recipient',peer::text,true);
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('b',64),'next-source');
 if repeated->>'queued'<>'135' or repeated->>'failed'<>'1' then raise exception 'partial_fanout_not_reported: %',repeated; end if;
 perform set_config('quorum_test.update_fail_recipient','',true);
 repeated:=public.session_update_request(own,sid,q,2,'0.3.0',repeat('b',64),'retry-recipient');
 if repeated->>'queued'<>'1' or repeated->>'existing'<>'135' then raise exception 'partial_retry_duplicated: %',repeated; end if;
 update quorum_private.memberships set revoked=true where quorum_id=q and user_id=owner_id;
 begin
  perform public.session_update_request(own,sid,q,2,'0.3.0',repeat('a',64),'first');
  raise exception 'revoked_replay_accepted';
 exception when insufficient_privilege then null; end;
 if has_table_privilege('authenticated','quorum_private.update_notices','SELECT')
  or has_function_privilege('anon','public.session_update_request(uuid,text,uuid,integer,text,text,text)','EXECUTE') then
  raise exception 'maintenance_privilege_exposed'; end if;
end $test$;
select 'updates:ok';
drop trigger synthetic_update_failure on quorum_private.messages;
drop function quorum_private.synthetic_update_failure();
