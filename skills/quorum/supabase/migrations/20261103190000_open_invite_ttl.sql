-- SOURCE ONLY. Reviewed administrator deployment in one transaction, after 180000.
-- Open invitations expire 60 minutes after issue instead of 15: a first-time
-- recipient installs, refreshes and starts its worker inside that window.
-- No reviewed body is edited: a row trigger sets only the expiry of newly
-- inserted open invitations, so the reviewed core returns and caches the new
-- value and identical retries keep it. GitHub invitations, existing rows,
-- acceptance, revocation, access and grants are unchanged.
do $migration$
declare actual text; reviewed constant jsonb:=jsonb_build_object(
 -- The open-access core (150000, renamed by 170000) that inserts open invitations.
 'quorum_private.call_priority_core(text,jsonb,text)','01f0fb843a904ff3b615e432ab054c25e44d3c0494e5213526ddb232e04b512c');
 f text;
begin
 if to_regprocedure('quorum_private.open_invitation_ttl()') is not null then
  raise exception 'open_invite_ttl_present'; end if;
 for f in select jsonb_object_keys(reviewed) loop
  select encode(sha256(convert_to(prosrc,'UTF8')),'hex') into actual from pg_proc where oid=to_regprocedure(f);
  if actual is distinct from reviewed->>f then raise exception 'reviewed_dispatcher_changed: %',f; end if;
 end loop;
end $migration$;

create function quorum_private.open_invitation_ttl()
returns trigger language plpgsql set search_path=pg_catalog as $function$
begin
 new.expires_at:=now()+interval '60 minutes';
 return new;
end $function$;
revoke all on function quorum_private.open_invitation_ttl() from public,anon,authenticated,service_role;

create trigger open_invitation_ttl before insert on quorum_private.id_invitations
 for each row when (new.open) execute function quorum_private.open_invitation_ttl();
