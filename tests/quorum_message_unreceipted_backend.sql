-- Disposable PostgreSQL only: the exact unreceipted lookup behind confirmed absence (#142).
do $test$
declare
 owner_id uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid(); q uuid:=gen_random_uuid();
 dev uuid:=gen_random_uuid(); agent uuid:=gen_random_uuid(); peer uuid:=gen_random_uuid();
 sid text:=gen_random_uuid()::text; conversation uuid:=gen_random_uuid();
 normal uuid; urgent uuid; result jsonb; denied boolean:=false; before_receipts bigint;
begin
 insert into auth.users(id) values(owner_id),(stranger);
 insert into auth.identities(user_id,provider,provider_id) values
  (owner_id,'github','9000000000142'),(stranger,'github','9000000000143');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'exact-unreceipted',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (agent,q,dev,owner_id,'absent','receiver','grok',sid),
  (peer,q,dev,owner_id,'absent','sender','codex',gen_random_uuid()::text);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 normal:=(public.message_send(q,peer,conversation,agent,null,'older normal','absent-normal','normal')->>'message_id')::uuid;
 urgent:=(public.message_send(q,peer,conversation,agent,null,'newer urgent','absent-urgent','urgent')->>'message_id')::uuid;

 -- The head is the newer urgent message, and the older one is still answered exactly.
 if (public.session_inbox(agent,sid)->0->>'id')::uuid is distinct from urgent then
  raise exception 'urgent_not_head'; end if;
 result:=public.session_message_unreceipted(agent,sid,normal);
 if result is distinct from jsonb_build_object('message_id',normal,'unreceipted',true) then
  raise exception 'exact_lookup_failed: %',result; end if;

 -- Reading writes no receipt, and a message for nobody here is not unreceipted.
 select count(*) into before_receipts from quorum_private.session_receipts;
 if (public.session_message_unreceipted(agent,sid,gen_random_uuid())->>'unreceipted')::boolean then
  raise exception 'unknown_message_listed'; end if;
 if (select count(*) from quorum_private.session_receipts)<>before_receipts then
  raise exception 'lookup_wrote_a_receipt'; end if;

 perform public.session_ack(agent,sid,normal);
 if (public.session_message_unreceipted(agent,sid,normal)->>'unreceipted')::boolean then
  raise exception 'receipted_message_listed'; end if;
 if not (public.session_message_unreceipted(agent,sid,urgent)->>'unreceipted')::boolean then
  raise exception 'urgent_lost'; end if;

 -- Only the caller's own bound session may ask.
 begin perform public.session_message_unreceipted(agent,gen_random_uuid()::text,urgent);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_session_allowed'; end if;
 denied:=false;
 perform set_config('request.jwt.claim.sub',stranger::text,true);
 begin perform public.session_message_unreceipted(agent,sid,urgent);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_owner_allowed'; end if;
 perform set_config('request.jwt.claim.sub',owner_id::text,true);

 if has_function_privilege('anon','public.session_message_unreceipted(uuid,text,uuid)','EXECUTE')
    or not has_function_privilege('authenticated','public.session_message_unreceipted(uuid,text,uuid)','EXECUTE') then
  raise exception 'wrong_execute_grant'; end if;
end $test$;
