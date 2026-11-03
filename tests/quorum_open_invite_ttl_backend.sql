-- Disposable regression: execute only inside BEGIN/ROLLBACK after the
-- open_invite_ttl migration. No models or external calls.
do $test$
declare
 owner_id uuid:=gen_random_uuid(); target uuid:=gen_random_uuid(); q uuid; g uuid; result jsonb; github jsonb;
 req text:=gen_random_uuid()::text;
begin
 insert into auth.users(id,raw_user_meta_data) values(owner_id,'{}'),(target,'{}');
 insert into auth.identities(user_id,provider,provider_id,identity_data) values
 (owner_id,'github','9000000000091','{}'),(target,'github','9000000000092','{}');
 insert into quorum_private.instance_creators(github_id) values('9000000000091');
 perform set_config('request.jwt.claim.sub',owner_id::text,true);
 q:=(public.quorum_create('ttl-'||req,req)->>'quorum_id')::uuid;
 perform public.quorum_set_access(q,'open',req);
 result:=public.invite_create_open(q,req);
 if (result->>'expires_at')::timestamptz not between now()+interval '59 minutes' and now()+interval '61 minutes' then
  raise exception 'open_invite_not_60_minutes'; end if;
 if (select expires_at from quorum_private.id_invitations where id=(result->>'invite_id')::uuid)
    <>(result->>'expires_at')::timestamptz then raise exception 'open_invite_ttl_not_stored'; end if;
 -- An identical retry returns the cached issue unchanged.
 if public.invite_create_open(q,req)<>result then raise exception 'open_ttl_retry_changed'; end if;
 -- GitHub invitations keep their 24-hour expiry.
 g:=(public.quorum_create('ttl-github-'||req,req||'-g')->>'quorum_id')::uuid;
 github:=public.invite_create_id(g,'9000000000092',req||'-g');
 if (github->>'expires_at')::timestamptz not between now()+interval '23 hours 59 minutes'
    and now()+interval '24 hours 1 minute' then raise exception 'github_invite_ttl_changed'; end if;
end $test$;
