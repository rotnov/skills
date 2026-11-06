-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261022160000_open_session_requirements.sql and before
-- 20261022170000_urgent_message_priority.sql.
-- No required-release change, new table, policy, RPC or grant. (#146)
--
-- The server guide applies the Grok engine extension (094041) before open access
-- (150000), and open access accepts either body. A server that skipped the Grok file
-- therefore ends with a second native_session body, one that the priority release and
-- every later migration refuse. Applying the Grok file late would undo the open-access
-- gate, so this file takes such a server to the state the guide's order gives: the
-- same engine check and the same native_session and receive_wakeup bodies.
--
-- A server that already applied the Grok extension, live included, changes nothing
-- here: this file returns at the first check and needs no deployment. It may also run
-- after later releases, which keep native_session. The applied migrations stay byte
-- identical, so their pins keep naming the one reviewed body.
--
-- Both replacements are the exact text the Grok file changed in these bodies, and
-- each result is checked against the reviewed hash inside this transaction.
do $migration$
declare
 session constant regprocedure:=to_regprocedure('quorum_private.native_session(uuid,text)');
 wakeup constant regprocedure:=to_regprocedure('quorum_private.receive_wakeup(text)');
 without_grok constant text:=$t$engine in ('codex','claude')$t$;
 with_grok constant text:=$t$engine in ('codex','claude','grok')$t$;
 actual text;
begin
 select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=session;
 -- The Grok extension and open access, in the guide's order: nothing to do.
 if actual='60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c' then return; end if;
 -- Bootstrap and Grok bodies before open access replaced their GitHub gate.
 if actual in ('0fdd5a5c6bdd7d0e6d17eff99d5735f4596e9436145d408d136ccf6ac7ef7ab4',
               '8c2a1214c146838730713adad08bf7439b4fc6c3301637fef0ffc4d8f4596d00') then
  raise exception 'open_access_required'; end if;
 -- Only the bootstrap body after open access continues.
 if actual is distinct from '60f40e2723322c2351430ec45f17600a8d2622c68ecb61845d8d1592546c9b7a' then
  raise exception 'reviewed_dispatcher_changed: quorum_private.native_session(uuid,text)'; end if;
 alter table quorum_private.agents drop constraint agents_engine_check;
 alter table quorum_private.agents add constraint agents_engine_check
  check (engine in ('claude','codex','grok'));
 execute replace(pg_get_functiondef(session),without_grok,with_grok);
 if encode(sha256(convert_to((select prosrc from pg_proc where oid=session),'UTF8')),'hex')
    <>'60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c' then
  raise exception 'reviewed_dispatcher_changed: quorum_private.native_session(uuid,text)'; end if;
 -- The Realtime extension is optional; without it clients keep polling.
 if wakeup is not null then
  if encode(sha256(convert_to((select prosrc from pg_proc where oid=wakeup),'UTF8')),'hex')
     <>'c3c5e10e5e86c7adb3d492ac785f635ac322798273485d59b548aa487f2f0c1b' then
   raise exception 'reviewed_dispatcher_changed: quorum_private.receive_wakeup(text)'; end if;
  execute replace(pg_get_functiondef(wakeup),without_grok,with_grok);
  if encode(sha256(convert_to((select prosrc from pg_proc where oid=wakeup),'UTF8')),'hex')
     <>'2f82e7bad7cedc81609fab91f8b4d680fe40a71216ce89c90fa66317ac24eb64' then
   raise exception 'reviewed_dispatcher_changed: quorum_private.receive_wakeup(text)'; end if;
 end if;
end $migration$;
