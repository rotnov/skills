-- Disposable PostgreSQL only: rows a phase-one server stored with the fixed number (#172).
-- Execute inside BEGIN/ROLLBACK after 20261106180000_release_reports.sql and before
-- 20261108120000_hash_only_release.sql; quorum_hash_only_backend.sql reads them afterwards.
-- The runner sets quorum_test.release to the phase-one release {protocol_version, skill_version}.
create temporary table hash_only_fixture(key text primary key, value text not null);
do $test$
declare
 owner_id uuid:=gen_random_uuid(); q uuid:=gen_random_uuid(); dev uuid:=gen_random_uuid();
 lead uuid:=gen_random_uuid(); lead_sid text:=gen_random_uuid()::text;
 peer uuid:=gen_random_uuid(); peer_sid text:=gen_random_uuid()::text;
 run_id uuid:=gen_random_uuid(); digest text:=repeat('d',64);
 required jsonb:=current_setting('quorum_test.release')::jsonb;
 p integer:=(required->>'protocol_version')::integer; v text:=required->>'skill_version';
 op uuid; notice uuid; message uuid;
begin
 if v is null then raise exception 'fixture_needs_the_phase_one_release'; end if;
 insert into auth.users(id) values(owner_id);
 insert into auth.identities(user_id,provider,provider_id) values(owner_id,'github','9000000000172');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'hash-only-before',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (lead,q,dev,owner_id,'hash','lead','claude',lead_sid),(peer,q,dev,owner_id,'hash','peer','codex',peer_sid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 if public.session_heartbeat(peer,peer_sid,gen_random_uuid(),p,v,0,null,run_id,now()) ? 'code' then
  raise exception 'before_heartbeat_refused'; end if;
 op:=(public.session_update_request(lead,lead_sid,q,p,v,digest,'hash-only-before')->>'operation_id')::uuid;
 select notice_id into notice from quorum_private.update_recipients where operation_id=op and recipient_id=peer;
 select message_id into message from quorum_private.update_notices where id=notice;
 if (select body from quorum_private.messages where id=message) not like '%, skill '||v||', sha256 '||digest||'.' then
  raise exception 'before_notice_text_unexpected'; end if;
 perform public.session_update_report(peer,peer_sid,q,notice,'refreshed',
  jsonb_build_object('protocol_version',p,'skill_version',v,'content_sha256',digest),'before-refresh');
 perform public.session_update_report(peer,peer_sid,q,notice,'active',
  jsonb_build_object('protocol_version',p,'skill_version',v,'content_sha256',digest,'run_id',run_id),'before-active');
 if (select skill_version from quorum_private.session_health where agent_id=peer)<>v
    or (select runtime_evidence->>'skill_version' from quorum_private.update_notices where id=notice)<>v then
  raise exception 'before_rows_unexpected'; end if;
 insert into hash_only_fixture values('owner',owner_id::text),('quorum',q::text),('lead',lead::text),('lead_sid',lead_sid),
  ('peer',peer::text),('peer_sid',peer_sid),('run_id',run_id::text),('digest',digest),('operation',op::text),
  ('notice',notice::text),('message',message::text),('old_release',v);
end $test$;
