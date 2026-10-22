-- SOURCE ONLY. Administrator-reviewed; requires open access and native coordination.
-- Installation/login never applies this migration. Run in one transaction.
-- Native coordination pins release metadata to GitHub users, so a live open-quorum
-- member's heartbeat and peer list fail before the version check. Apply this after
-- both migrations, in either order they were applied. It reuses verified_actor
-- unchanged: closing a quorum still revokes non-GitHub members and this access.
do $migration$
begin
 if to_regprocedure('quorum_private.verified_actor(uuid)') is null then
  raise exception 'open_access_required'; end if;
 if to_regprocedure('public.session_requirements()') is null
   or encode(sha256(convert_to((select prosrc from pg_proc
     where oid='public.session_requirements()'::regprocedure),'UTF8')),'hex')
     <>'bbb06c712e7d747ee9004d2d95d883a5b5ee9d84038415f38bf9383b72ff05de' then
  raise exception 'reviewed_function_changed: public.session_requirements()'; end if;
end $migration$;

create or replace function public.session_requirements()
returns jsonb language plpgsql security definer set search_path=pg_catalog as $function$
begin
 if not quorum_private.verified_actor(auth.uid()) then
  raise insufficient_privilege using message='github_identity_required'; end if;
 return jsonb_build_object('protocol_version',1,'skill_version','0.2.0');
end $function$;
revoke all on function public.session_requirements() from public,anon,authenticated;
grant execute on function public.session_requirements() to authenticated;
