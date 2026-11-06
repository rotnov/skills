-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after
-- 20261106140000_session_message_unreceipted.sql. It requires only the task
-- authority bound (20261106130000) and touches nothing the lookup added.
-- No required-release change, table grant, policy, task authority or model launch. (#146)
--
-- Since the reconnect dedupe (20261106120000) the Hello result `peers` also counts
-- peers that were not greeted again, so "Hello sent to N" overstated what went out.
-- The heartbeat `hello` object gains `skipped`: how many of `peers` the dedupe left
-- out on this connection. `peers` keeps its meaning, so an older client reads the same
-- result, and the roster text, which clients match exactly, is unchanged. A connection
-- recorded before this release returns null, so a client treats the split as unreported;
-- a client on a server without this file sees no key and does the same.
--
-- The reviewed Hello wrapper moves to a private function kept for rollback. The new
-- body keeps its name, so the public peer-status heartbeat calls it unchanged, and is
-- that body plus the three lines marked "#146". The core keeps returning its private
-- skipped list; the wrapper stores only the count. Lock order and writers are unchanged.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The peer-status heartbeat (160000), kept public and called unchanged.
 'public.session_heartbeat(uuid,text,uuid,integer,text,integer,timestamptz,uuid,timestamptz)','2957604dce5af878d0da3713b17545e11a745a95c4fc1aad95c222f6aa5394a2',
 -- The Hello wrapper (120000) copied below.
 'quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)','2feca63b79981667839a2c93d3c49728b9c135f39de97ee968025bd9eb183a5f',
 -- The core (120000) whose private skipped list the wrapper counts.
 'quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)','b3791a5b3b5a93433a6a3dd854af5ffed93e9843bc905b811e201b20d55873a5',
 -- The dispatcher the copied body sends through (role-assignment wrapper, 20261105140000).
 'quorum_private.call(text,jsonb,text)','c7e71c9dcc515b6f0eb1f5b64b0e4cb431c94422b1bb763fc2687f5c2b86c99a',
 -- The binding check the copied body relies on.
 'quorum_private.native_session(uuid,text)','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c',
 -- The mandatory 0.3.0/2 release (170000); unchanged here.
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.session_heartbeat_hello_before_split(uuid,text,uuid,integer,text)') is not null
    or exists(select 1 from information_schema.columns where table_schema='quorum_private'
     and table_name='session_hellos' and column_name='skipped') then
  raise exception 'hello_split_present'; end if;
 if to_regprocedure('quorum_private.session_task_authority_before_project_bound(uuid,text,uuid)') is null then
  raise exception 'task_authority_bound_required'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- Null for a connection recorded before this release: the split is then unreported.
alter table quorum_private.session_hellos add column skipped integer
 constraint session_hellos_skipped_check check (skipped between 0 and 128);

alter function quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)
 rename to session_heartbeat_hello_before_split;
revoke all on function quorum_private.session_heartbeat_hello_before_split(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;

-- The reviewed 120000 Hello wrapper; lines marked #146 store and return the skipped count.
create function quorum_private.session_heartbeat_before_status(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer; skipped jsonb; -- #126
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 -- #126: peers the core did not greet again on this connection; never returned to a client.
 skipped:=coalesce(result->'hello_skipped','[]'::jsonb);
 result:=result-'hello_skipped';
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles,skipped) -- #146
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb),jsonb_array_length(skipped) -- #146
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and (exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
     or skipped@>to_jsonb(array[a.id::text])) -- #126
   ) y
  ) x
  on conflict do nothing;
  get diagnostics created=row_count;
  if created=1 then
   select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
   select string_agg(r.value#>>'{}',', ' order by r.ordinality) into listing
    from jsonb_array_elements(hello.available_roles) with ordinality r;
   -- A self-addressed transport notice: never a task, never a reply chain.
   sent:=quorum_private.call('message_send',jsonb_build_object('quorum_id',q,'sender_agent_id',p_agent_id,
    'recipient_agent_id',p_agent_id,
    'conversation_id',md5('quorum-roster:'||p_agent_id||':'||p_connection_id)::uuid,'reply_to',null,
    'body',case when hello.available=0 then 'Quorum roster: no other native session is available in project '
      ||own.project_ref||'. Hello reached '||hello.peers||' registered peer(s). Transport notice; no reply required.'
     else 'Quorum roster: '||hello.available||' peer(s) available in project '||own.project_ref||': '||listing
      ||case when hello.available>16 then ', ...' else '' end
      ||'. Transport notice; no reply required.' end),
    'roster:'||p_agent_id||':'||p_connection_id);
   insert into quorum_private.session_notices values((sent->>'message_id')::uuid,p_agent_id);
   update quorum_private.deliveries set requires_handler=false where message_id=(sent->>'message_id')::uuid;
   update quorum_private.session_hellos set roster_message_id=(sent->>'message_id')::uuid
    where agent_id=p_agent_id and connection_id=p_connection_id;
  end if;
 end if;
 select * into hello from quorum_private.session_hellos where agent_id=p_agent_id and connection_id=p_connection_id;
 if not found then return result||jsonb_build_object('hello',null); end if;
 return result||jsonb_build_object('hello',jsonb_build_object('project_ref',own.project_ref,
  'peers',hello.peers,'skipped',hello.skipped,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles, -- #146
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $function$;
revoke all on function quorum_private.session_heartbeat_before_status(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;
