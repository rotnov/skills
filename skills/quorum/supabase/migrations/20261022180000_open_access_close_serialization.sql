-- SOURCE ONLY. Administrator-reviewed follow-on to open access (150000).
-- Installation, login, session connect and reception never apply this file.
-- Run in one transaction, after the urgent message priority release (170000).
-- No new grant, policy, table privilege or role; no reviewed body is edited.
--
-- Closing an open quorum must not race an in-flight open invitation. The
-- open-access wrapper locks the invitation but only reads the quorum's access,
-- and a close revokes members before it reaches unused invitations. An anonymous
-- acceptance that already read 'open' could therefore commit a membership the
-- close had scanned past, and an issue that read 'open' could commit an open
-- invitation after the close revoked the unused ones.
--
-- Every one of these operations now takes the quorum row lock first, before any
-- invitation row, advisory, membership or request lock:
--   quorum_set_access          FOR NO KEY UPDATE (the mode its own update takes)
--   invite_create_open         FOR SHARE
--   invite_accept_id (open)    FOR SHARE
-- SHARE conflicts with NO KEY UPDATE but not with itself or with the KEY SHARE
-- foreign-key checks every other insert takes, so concurrent acceptances and all
-- other RPCs keep their existing locking. The unchanged reviewed core then rereads
-- the invitation and access in fresh statements: an acceptance that waited for a
-- close is denied, and a close that waited for an acceptance or issue revokes the
-- new membership or invitation. Authorization, ownership, idempotency and
-- revocation stay in that core.
--
-- That revalidation holds only under READ COMMITTED. Under REPEATABLE READ or
-- SERIALIZABLE the snapshot is fixed before the lock wait ends, and a row-lock
-- wait does not by itself raise a serialization failure: a close that waited for
-- an issue holding SHARE commits access='github' while its stale snapshot misses
-- the new invitation, which stays usable (reproduced on PostgreSQL 17). These
-- three operations therefore refuse any other isolation level with
-- open_access_requires_read_committed, before any lock or delegated write.
do $migration$
declare
 reviewed constant jsonb:=jsonb_build_object(
  -- Priority wrapper (170000): message_send only, otherwise delegates.
  'quorum_private.call(text,jsonb,text)','ac169502144c0d19620bc8a18a9e89f5990367053b5157982252f53a550b8934',
  -- Open-access wrapper (150000) whose lock order this file completes.
  'quorum_private.call_priority_core(text,jsonb,text)','01f0fb843a904ff3b615e432ab054c25e44d3c0494e5213526ddb232e04b512c',
  'quorum_private.call_invite_core(text,jsonb,text)','f6a10b1b7f476328eaa21e34bff6336bd734a1dd1205684bdd6f0d5ea814aadc',
  'quorum_private.call_legacy_core(text,jsonb,text)','1aa452947be35e79c190d3f59baa8a3ffc8da8ffdb5eb36d07bb2fea23a4bebe',
  -- Entry points that must keep routing through quorum_private.call.
  'public.quorum_set_access(uuid,text,text)','b4f74ea73985d7e2f03560ffdb57b3ef6f117375e3c0c2ce88b072ab0ed8b480',
  'public.invite_create_open(uuid,text)','b3f06f990f5523723b27eb36a585ffca801087239e6784166e940ad96ac47349',
  'public.invite_accept_id(uuid,text)','d61c6972108e7feafa0a77ab32c436c8ec4971cacedf2c219fd4f7d6dc9e0fa4',
  -- The mandatory 0.3.0/2 release gate: 170000 is applied.
  'public.session_requirements()','6e9752bcaedbbdfc4b2b8197cb44598306ee8d79044d654836017aab86031eef');
 f text;
begin
 for f in select jsonb_object_keys(reviewed) loop
  if to_regprocedure(f) is null or encode(sha256(convert_to((select prosrc from pg_proc
     where oid=to_regprocedure(f)),'UTF8')),'hex')<>reviewed->>f then
   raise exception 'reviewed_dispatcher_changed: %', f; end if;
 end loop;
 if to_regprocedure('quorum_private.call_serial_core(text,jsonb,text)') is not null then
  raise exception 'serial_core_present'; end if;
 alter function quorum_private.call(text,jsonb,text) rename to call_serial_core;
end $migration$;
revoke all on function quorum_private.call_serial_core(text,jsonb,text) from public,anon,authenticated,service_role;

create function quorum_private.call(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
declare who uuid:=auth.uid(); q uuid; is_open boolean;
begin
 -- Requests the core rejects before reading a row take no lock here, so their
 -- errors and precedence are unchanged.
 if op not in ('quorum_set_access','invite_create_open','invite_accept_id') or who is null
    or p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072
    or req is null or length(req) not between 1 and 128 then
  return quorum_private.call_serial_core(op,p,req);
 end if;
 -- The core's fresh-statement rereads need READ COMMITTED; see the header.
 if current_setting('transaction_isolation')<>'read committed' then
  raise exception 'open_access_requires_read_committed';
 end if;
 if op='invite_accept_id' then
  -- quorum_id and open never change after insert; the core relocks the row.
  select i.quorum_id,i.open into q,is_open from quorum_private.id_invitations i
   where i.id=(p->>'invite_id')::uuid;
  if is_open then perform 1 from quorum_private.quorums where id=q for share; end if;
 elsif exists(select 1 from auth.identities where user_id=who and provider='github') then
  q:=(p->>'quorum_id')::uuid;
  -- Only the owner reaches the core's writes; nobody else may hold this lock.
  if exists(select 1 from quorum_private.memberships
            where quorum_id=q and user_id=who and role='owner' and not revoked) then
   if op='quorum_set_access' then
    perform 1 from quorum_private.quorums where id=q for no key update;
   else
    perform 1 from quorum_private.quorums where id=q for share;
   end if;
  end if;
 end if;
 return quorum_private.call_serial_core(op,p,req);
end $function$;
revoke all on function quorum_private.call(text,jsonb,text) from public,anon,authenticated,service_role;
