-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after 180000.
-- Additive: the reviewed heartbeat still sends every Hello; this wrapper reports
-- who received it and gives the newcomer one quiet roster notice per connection.
-- No required-release change, table access, task authority or model launch.
do $migration$
declare f text; actual text; reviewed constant jsonb:=jsonb_build_object(
 'public.session_heartbeat(uuid,text,uuid,integer,text)','944211db94640dca91d75e007a4c5de8a1ee28511ea6c78a5b3fb2f7d96768c5',
 'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
begin
 if to_regprocedure('quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)') is not null then
  raise exception 'hello_extension_present'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

-- One row per connection that the reviewed core announced. It survives a lost
-- heartbeat response, so a retry with the same connection still reports it.
create table quorum_private.session_hellos (
 agent_id uuid not null, connection_id uuid not null,
 peers integer not null check(peers between 0 and 128),
 roles jsonb not null check(jsonb_typeof(roles)='array' and jsonb_array_length(roles)<=16),
 available integer not null check(available between 0 and 128),
 available_roles jsonb not null check(jsonb_typeof(available_roles)='array' and jsonb_array_length(available_roles)<=16),
 roster_message_id uuid unique references quorum_private.messages(id),
 created_at timestamptz not null default now(),
 primary key(agent_id,connection_id),
 foreign key(agent_id,connection_id) references quorum_private.session_connections(agent_id,connection_id)
);
alter table quorum_private.session_hellos enable row level security;
revoke all on quorum_private.session_hellos from public,anon,authenticated,service_role;

alter function public.session_heartbeat(uuid,text,uuid,integer,text) set schema quorum_private;
alter function quorum_private.session_heartbeat(uuid,text,uuid,integer,text) rename to session_heartbeat_before_hello;
revoke all on function quorum_private.session_heartbeat_before_hello(uuid,text,uuid,integer,text)
 from public,anon,authenticated,service_role;

create function public.session_heartbeat(p_agent_id uuid, p_session_ref text, p_connection_id uuid,
 p_protocol_version integer, p_skill_version text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare result jsonb; q uuid; own quorum_private.agents%rowtype; hello quorum_private.session_hellos%rowtype;
 sent jsonb; listing text; created integer;
begin
 -- The reviewed core validates the binding and release and sends the Hellos.
 result:=quorum_private.session_heartbeat_before_hello(p_agent_id,p_session_ref,p_connection_id,
  p_protocol_version,p_skill_version);
 if result ? 'code' then return result; end if;
 q:=quorum_private.native_session(p_agent_id,p_session_ref);
 select * into own from quorum_private.agents where id=p_agent_id;
 if (result->>'new_connection')::boolean then
  -- Count the Hellos the core just sent, by their exact idempotency keys.
  insert into quorum_private.session_hellos(agent_id,connection_id,peers,roles,available,available_roles)
  select p_agent_id,p_connection_id,count(*),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.n<=16),'[]'::jsonb),
   count(*) filter (where x.online),
   coalesce(jsonb_agg(x.role order by x.role) filter (where x.online and x.m<=16),'[]'::jsonb)
  from (
   select y.*, row_number() over(order by y.role,y.id) n,
    row_number() over(partition by y.online order by y.role,y.id) m
   from (
    select a.id,a.role,
     coalesce(h.last_seen>now()-interval '60 seconds',false) as online
    from quorum_private.agents a
    left join quorum_private.session_health h on h.agent_id=a.id
    where a.quorum_id=q and a.id<>p_agent_id and exists(
     select 1 from quorum_private.requests r where r.actor=auth.uid() and r.operation='message_send'
      and r.request_id='hello:'||p_agent_id||':'||p_connection_id||':'||a.id)
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
  'peers',hello.peers,'roles',hello.roles,'available',hello.available,'available_roles',hello.available_roles,
  'roster_message_id',hello.roster_message_id,'announced_at',hello.created_at));
end $function$;
revoke all on function public.session_heartbeat(uuid,text,uuid,integer,text) from public,anon,authenticated,service_role;
grant execute on function public.session_heartbeat(uuid,text,uuid,integer,text) to authenticated;
