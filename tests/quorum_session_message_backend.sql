-- Disposable PostgreSQL only: one bounded message read behind pointer delivery (#150).
do $test$
declare
 owner_id uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid(); q uuid:=gen_random_uuid();
 dev uuid:=gen_random_uuid(); agent uuid:=gen_random_uuid(); peer uuid:=gen_random_uuid();
 sid text:=gen_random_uuid()::text; peer_sid text:=gen_random_uuid()::text; conversation uuid:=gen_random_uuid();
 body text:=left(repeat('"\',8192),16384); first_id uuid; result jsonb; denied boolean:=false; before_receipts bigint;
begin
 insert into auth.users(id) values(owner_id),(stranger);
 insert into auth.identities(user_id,provider,provider_id) values
  (owner_id,'github','9000000000150'),(stranger,'github','9000000000151');
 insert into quorum_private.quorums(id,slug,owner_id) values(q,'single-message',owner_id);
 insert into quorum_private.memberships(quorum_id,user_id,role) values(q,owner_id,'owner');
 insert into quorum_private.devices(id,owner_id,label) values(dev,owner_id,'synthetic');
 insert into quorum_private.agents(id,quorum_id,device_id,owner_id,project_ref,role,engine,session_ref) values
  (agent,q,dev,owner_id,'single','receiver','codex',sid),
  (peer,q,dev,owner_id,'single','sender','codex',peer_sid);
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 for i in 1..16 loop
  result:=public.message_send(q,peer,conversation,agent,null,body,'single-'||i,'normal');
  if i=1 then first_id:=(result->>'message_id')::uuid; end if;
 end loop;

 -- The page these 16 bodies make is over the client's limit; one message is not.
 if octet_length(public.conversation_read(q,agent,conversation,0)::text)<262144 then
  raise exception 'page_fits_the_limit'; end if;
 result:=public.session_message(agent,sid,first_id);
 if result->>'body'<>body or (result->>'id')::uuid<>first_id or (result->>'sequence')::bigint<1
    or result->>'recipient_id'<>agent::text or result->>'conversation_id'<>conversation::text then
  raise exception 'single_read_failed'; end if;
 if octet_length(result::text)>=262144 then raise exception 'single_read_over_limit'; end if;

 -- Reading writes no receipt; a receipted message stays readable.
 select count(*) into before_receipts from quorum_private.session_receipts;
 perform public.session_message(agent,sid,first_id);
 if (select count(*) from quorum_private.session_receipts)<>before_receipts then
  raise exception 'read_wrote_a_receipt'; end if;
 if (public.session_inbox(agent,sid)->0->>'id')::uuid is distinct from first_id then
  raise exception 'read_moved_the_inbox'; end if;
 perform public.session_ack(agent,sid,first_id);
 if public.session_message(agent,sid,first_id)->>'body'<>body then raise exception 'receipted_unreadable'; end if;

 -- Its sender, or anyone else's agent, gets the same null as an unknown message.
 if public.session_message(peer,peer_sid,first_id) is not null then raise exception 'sender_read_it'; end if;
 if public.session_message(agent,sid,gen_random_uuid()) is not null then raise exception 'unknown_read'; end if;

 -- Only the caller's own bound session may ask.
 begin perform public.session_message(agent,gen_random_uuid()::text,first_id);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_session_allowed'; end if;
 denied:=false;
 perform set_config('request.jwt.claim.sub',stranger::text,true);
 begin perform public.session_message(agent,sid,first_id);
 exception when insufficient_privilege then denied:=true; end;
 if not denied then raise exception 'other_owner_allowed'; end if;
end $test$;
