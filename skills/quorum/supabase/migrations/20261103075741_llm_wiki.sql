-- SOURCE ONLY. Additive retained-source wiki; no provider/network calls.
create table quorum_private.knowledge_sources (
 source_id uuid primary key, space_id uuid not null references quorum_private.knowledge_spaces(id),
 reference text not null check(length(reference) between 1 and 512),
 author text not null check(length(author) between 1 and 200), source_date timestamptz not null check(isfinite(source_date)),
 body text not null check(octet_length(body) between 1 and 8192),
 is_excerpt boolean not null, excerpt_scope text not null check(length(excerpt_scope)<=512 and (not is_excerpt or length(excerpt_scope)>0)),
 content_sha256 text not null check(content_sha256=encode(sha256(convert_to(body,'UTF8')),'hex')),
 capturer_user uuid not null, capturer_agent uuid not null, quorum_id uuid not null,
 captured_at timestamptz not null default now(), withdrawn boolean not null default false,
 unique(space_id,source_id)
);
-- Purged source identities cannot be reused to silently rebind old evidence.
-- Tombstones retain identity only, never purged text or attribution.
create table quorum_private.knowledge_source_tombstones (
 source_id uuid primary key, space_id uuid not null references quorum_private.knowledge_spaces(id)
);
-- Page UUIDs have the same permanent identity contract for every entry kind.
-- Keep no purged revisions, content, hashes or attribution here.
create table quorum_private.knowledge_page_tombstones (
 entry_id uuid primary key, space_id uuid not null references quorum_private.knowledge_spaces(id)
);
alter table quorum_private.knowledge_page_tombstones enable row level security;
revoke all on quorum_private.knowledge_page_tombstones from public,anon,authenticated,service_role;
alter table quorum_private.knowledge_source_tombstones enable row level security;
revoke all on quorum_private.knowledge_source_tombstones from public,anon,authenticated,service_role;
alter table quorum_private.knowledge_sources enable row level security;
revoke all on quorum_private.knowledge_sources from public,anon,authenticated,service_role;
alter table quorum_private.knowledge_entries add column entry_kind text not null default 'legacy' check(entry_kind in ('legacy','wiki_page'));
alter table quorum_private.knowledge_revisions add column source_ids uuid[] not null default '{}', add column related_pages uuid[] not null default '{}';
alter table quorum_private.knowledge_revisions add check(cardinality(source_ids)<=8 and cardinality(related_pages)<=8);
create function quorum_private.knowledge_immutable() returns trigger language plpgsql set search_path=pg_catalog as $$
begin
 if TG_TABLE_NAME='knowledge_sources' then
  if (to_jsonb(new)-'withdrawn') is distinct from (to_jsonb(old)-'withdrawn') or (old.withdrawn and not new.withdrawn) then raise exception 'knowledge_source_immutable'; end if;
 elsif TG_TABLE_NAME='knowledge_entries' then
  if new.entry_kind<>old.entry_kind or new.id<>old.id or new.space_id<>old.space_id then raise exception 'knowledge_kind_immutable'; end if;
 else raise exception 'knowledge_revision_immutable'; end if;
 return new;
end $$;
create trigger knowledge_source_immutable before update on quorum_private.knowledge_sources for each row execute function quorum_private.knowledge_immutable();
create trigger knowledge_kind_immutable before update on quorum_private.knowledge_entries for each row execute function quorum_private.knowledge_immutable();
create trigger knowledge_revision_immutable before update on quorum_private.knowledge_revisions for each row execute function quorum_private.knowledge_immutable();

-- All mutation routes serialize by space BEFORE taking scope row locks. This
-- consistent order protects relation validation against concurrent withdrawal,
-- purge and page updates, including existing legacy RPC routes.
create function quorum_private.knowledge_mutation_lock(s uuid) returns void language sql set search_path=pg_catalog as $$
 select pg_advisory_xact_lock(hashtextextended('knowledge-space:'||s::text,0));
$$;
create function quorum_private.knowledge_visible(s uuid,e uuid,v integer default null)
returns boolean language sql stable security definer set search_path=pg_catalog as $$
 select exists(select 1 from quorum_private.knowledge_entries ke join quorum_private.knowledge_revisions kr
 on kr.entry_id=ke.id and kr.revision=coalesce(v,ke.current_revision)
 where ke.space_id=s and ke.id=e and kr.status<>'withdrawn'
 and not exists(select 1 from unnest(kr.source_ids) x where not exists(
  select 1 from quorum_private.knowledge_sources ks where ks.space_id=s and ks.source_id=x and not ks.withdrawn)));
$$;
create function quorum_private.knowledge_uuid_links(j jsonb) returns uuid[] language plpgsql set search_path=pg_catalog as $$
declare ids uuid[];
begin
 if j is null or jsonb_typeof(j)<>'array' then raise exception 'knowledge_invalid_link'; end if;
 if jsonb_array_length(j)>8 or exists(select 1 from jsonb_array_elements(j) x where jsonb_typeof(x)<>'string') then raise exception 'knowledge_invalid_link'; end if;
 begin select coalesce(array_agg(x::uuid),'{}') into ids from jsonb_array_elements_text(j) x;
 exception when invalid_text_representation then raise exception 'knowledge_invalid_link'; end;
 if cardinality(ids)<>(select count(distinct x) from unnest(ids) x) then raise exception 'knowledge_invalid_link'; end if;
 return ids;
end $$;
create function quorum_private.knowledge_projection(r quorum_private.knowledge_revisions) returns jsonb
language sql stable security definer set search_path=pg_catalog as $$
 -- Read at most one extra visible inverse edge to signal truncation. Apply
 -- visibility before the cap; hidden predecessors must not consume slots.
 with inverse as materialized (
  select ke.id from quorum_private.knowledge_entries ke
  join quorum_private.knowledge_revisions kr on kr.entry_id=ke.id and kr.revision=ke.current_revision
  where ke.space_id=r.space_id and kr.replacement_id=r.entry_id and kr.status='superseded'
   and quorum_private.knowledge_visible(r.space_id,ke.id)
  order by ke.id limit 9
 ), bounded as (select id,row_number() over(order by id) as position from inverse)
 select jsonb_build_object('entry_id',r.entry_id,'entry_kind',(select entry_kind from quorum_private.knowledge_entries where id=r.entry_id),
 'revision',r.revision,'type',r.type,'status',r.status,'title',r.title,'content_sha256',r.content_sha256,
 'source_refs',r.source_refs,'versions',r.versions,'author_user',r.author_user,'author_agent',r.author_agent,'quorum_id',r.quorum_id,
 'created_at',r.created_at,'review_after',r.review_after,'verification_user',r.verification_user,'verification_at',r.verification_at,'evidence',r.evidence,
 'source_ids',coalesce((select jsonb_agg(x) from unnest(r.source_ids) x join quorum_private.knowledge_sources ks on ks.source_id=x and ks.space_id=r.space_id where not ks.withdrawn),'[]'),
 'sources',coalesce((select jsonb_agg(jsonb_build_object('source_id',ks.source_id,'reference',ks.reference,'author',ks.author,'source_date',ks.source_date,'content_sha256',ks.content_sha256,'is_excerpt',ks.is_excerpt,'excerpt_scope',ks.excerpt_scope)) from unnest(r.source_ids) x join quorum_private.knowledge_sources ks on ks.source_id=x and ks.space_id=r.space_id where not ks.withdrawn),'[]'),
 'related_pages',coalesce((select jsonb_agg(x) from unnest(r.related_pages) x where quorum_private.knowledge_visible(r.space_id,x)),'[]'),
 'conflicts_with',coalesce((select jsonb_agg(x) from jsonb_array_elements_text(r.conflicts_with) x where quorum_private.knowledge_visible(r.space_id,x::uuid)),'[]'),
 'replacement_id',case when quorum_private.knowledge_visible(r.space_id,r.replacement_id) then r.replacement_id else null end,
 'supersedes',coalesce((select jsonb_agg(id order by id) from bounded where position<=8),'[]'),
 'supersedes_truncated',(select count(*)>8 from inverse));
$$;

create function public.knowledge_source_write(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; cached jsonb; result jsonb; h text; ident uuid;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'space_id',p_space_id,'payload',p_payload);
begin
 perform quorum_private.knowledge_mutation_lock(p_space_id);
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,true);
 perform quorum_private.knowledge_fields(p_payload,array['source_id','reference','author','source_date','body','is_excerpt','excerpt_scope'],array['source_id','reference','author','source_date','body','is_excerpt','excerpt_scope']);
 if exists(select 1 from unnest(array['source_id','reference','author','source_date','body','excerpt_scope']) k where jsonb_typeof(p_payload->k)<>'string')
 or jsonb_typeof(p_payload->'is_excerpt')<>'boolean'
 or (p_payload->>'source_date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})$' then raise exception 'knowledge_invalid_payload'; end if;
 cached:=quorum_private.knowledge_cached('source:write',p,p_request_id);
 if cached is not null then
  if not exists(select 1 from quorum_private.knowledge_sources where space_id=p_space_id and source_id=(p_payload->>'source_id')::uuid and not withdrawn) then raise insufficient_privilege using message='knowledge_denied'; end if;
  return cached; end if;
 ident:=(p_payload->>'source_id')::uuid;
 if exists(select 1 from quorum_private.knowledge_sources where source_id=ident) or exists(select 1 from quorum_private.knowledge_source_tombstones where source_id=ident) then raise exception 'knowledge_source_immutable'; end if;
 h:=encode(sha256(convert_to(p_payload->>'body','UTF8')),'hex');
 insert into quorum_private.knowledge_sources(source_id,space_id,reference,author,source_date,body,is_excerpt,excerpt_scope,content_sha256,capturer_user,capturer_agent,quorum_id)
 values(ident,p_space_id,p_payload->>'reference',p_payload->>'author',(p_payload->>'source_date')::timestamptz,p_payload->>'body',(p_payload->>'is_excerpt')::boolean,p_payload->>'excerpt_scope',h,auth.uid(),p_agent_id,q);
 result:=jsonb_build_object('source_id',ident,'content_sha256',h);
 return quorum_private.knowledge_save('source:write',p,p_request_id,result);
end $$;
create function public.knowledge_source_read(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_source_id uuid,p_offset integer default 0,p_length integer default 4096,p_audit boolean default false)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare r quorum_private.knowledge_sources%rowtype;
begin
 perform quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,false,coalesce(p_audit,false));
 if p_audit is null or p_offset is null or p_offset<0 or p_length is null or p_length not between 1 and 4096 then raise exception 'knowledge_invalid_payload'; end if;
 select * into r from quorum_private.knowledge_sources where space_id=p_space_id and source_id=p_source_id;
 if not found or (r.withdrawn and not p_audit) then raise insufficient_privilege using message='knowledge_denied'; end if;
 return (to_jsonb(r)-'body')||jsonb_build_object('body',substring(r.body from p_offset+1 for p_length),'offset',p_offset,'next_offset',case when p_offset::bigint+p_length<length(r.body) then p_offset+p_length else null end);
end $$;

create or replace function public.knowledge_admin(p_agent_id uuid,p_session_ref text,p_operation text,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; s uuid; own uuid; cached jsonb; result jsonb; e quorum_private.knowledge_entries%rowtype;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'payload',p_payload);
 op text:='admin:'||p_operation;
begin
 perform quorum_private.knowledge_mutation_lock((p_payload->>'space_id')::uuid);
 q:=quorum_private.knowledge_binding(p_agent_id,p_session_ref);
 if p_operation='create' then
  perform quorum_private.knowledge_fields(p_payload,array['space_id','name'],array['name']);
  if not exists(select 1 from auth.identities where user_id=auth.uid() and provider='github') then
   raise insufficient_privilege using message='github_identity_required'; end if;
  if jsonb_typeof(p_payload->'name')<>'string' or length(p_payload->>'name') not between 1 and 200 then raise exception 'knowledge_invalid_payload'; end if;
 else
  if p_operation in ('grant','revoke') then
   perform quorum_private.knowledge_fields(p_payload,case when p_operation='grant' then array['space_id','user_id','permission'] else array['space_id','user_id'] end,
    case when p_operation='grant' then array['space_id','user_id','permission'] else array['space_id','user_id'] end);
  elsif p_operation in ('attach','detach') then
   perform quorum_private.knowledge_fields(p_payload,array['space_id','quorum_id','project_ref'],array['space_id','quorum_id','project_ref']);
  elsif p_operation in ('withdraw_source','purge_source') then
   perform quorum_private.knowledge_fields(p_payload,array['space_id','source_id','expected_content_sha256'],array['space_id','source_id','expected_content_sha256']);
  elsif p_operation='purge' then
   perform quorum_private.knowledge_fields(p_payload,array['space_id','entry_id','expected_revision'],array['space_id','entry_id','expected_revision']);
  elsif p_operation='configure_vector' then
   perform quorum_private.knowledge_fields(p_payload,array['space_id','model','dimensions'],array['space_id','model','dimensions']);
  else raise exception 'knowledge_invalid_operation'; end if;
  s:=(p_payload->>'space_id')::uuid;
  -- Administration bootstraps attachments, so it proves ownership without requiring one.
  select owner_id into own from quorum_private.knowledge_spaces where id=s for update;
  if not found or own<>auth.uid() then raise insufficient_privilege using message='knowledge_denied'; end if;
  if p_operation in ('attach','detach') then
   perform 1 from quorum_private.quorums where id=(p_payload->>'quorum_id')::uuid and owner_id=auth.uid() for share;
   if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
   if jsonb_typeof(p_payload->'project_ref')<>'string' or length(p_payload->>'project_ref') not between 1 and 500 then raise exception 'knowledge_invalid_payload'; end if;
  end if;
 end if;
 cached:=quorum_private.knowledge_cached(op,p,p_request_id);
 if cached is not null then return cached; end if;
 if p_operation='create' then
  s:=coalesce((p_payload->>'space_id')::uuid,gen_random_uuid());
  insert into quorum_private.knowledge_spaces(id,owner_id,name) values(s,auth.uid(),p_payload->>'name');
 elsif p_operation='grant' then
  if coalesce(p_payload->>'permission','') not in ('reader','writer') or (p_payload->>'user_id')::uuid=own then raise exception 'knowledge_invalid_payload'; end if;
  insert into quorum_private.knowledge_grants values(s,(p_payload->>'user_id')::uuid,p_payload->>'permission')
   on conflict(space_id,user_id) do update set permission=excluded.permission;
 elsif p_operation='revoke' then
  delete from quorum_private.knowledge_grants where space_id=s and user_id=(p_payload->>'user_id')::uuid;
 elsif p_operation='attach' then
  insert into quorum_private.knowledge_attachments values(s,(p_payload->>'quorum_id')::uuid,p_payload->>'project_ref') on conflict do nothing;
 elsif p_operation='detach' then
  delete from quorum_private.knowledge_attachments where space_id=s and quorum_id=(p_payload->>'quorum_id')::uuid and project_ref=p_payload->>'project_ref';
 elsif p_operation='configure_vector' then
  if jsonb_typeof(p_payload->'model')<>'string' or length(p_payload->>'model') not between 1 and 200
   or jsonb_typeof(p_payload->'dimensions')<>'number' or (p_payload->>'dimensions')::numeric<>trunc((p_payload->>'dimensions')::numeric)
   or (p_payload->>'dimensions')::integer not between 1 and 2000 then raise exception 'knowledge_invalid_payload'; end if;
  update quorum_private.knowledge_spaces set vector_model=p_payload->>'model',vector_dimensions=(p_payload->>'dimensions')::integer where id=s;
  delete from quorum_private.knowledge_vectors where entry_id in(select id from quorum_private.knowledge_entries where space_id=s);
 elsif p_operation in ('withdraw_source','purge_source') then
  perform 1 from quorum_private.knowledge_sources where space_id=s and source_id=(p_payload->>'source_id')::uuid and content_sha256=p_payload->>'expected_content_sha256' for update;
  if not found then raise exception 'knowledge_source_conflict'; end if;
  delete from quorum_private.knowledge_vectors where entry_id in(select entry_id from quorum_private.knowledge_revisions where space_id=s and (p_payload->>'source_id')::uuid=any(source_ids));
  if p_operation='withdraw_source' then update quorum_private.knowledge_sources set withdrawn=true where space_id=s and source_id=(p_payload->>'source_id')::uuid;
  else
   insert into quorum_private.knowledge_source_tombstones values((p_payload->>'source_id')::uuid,s);
   delete from quorum_private.knowledge_sources where space_id=s and source_id=(p_payload->>'source_id')::uuid; end if;
 elsif p_operation='purge' then
  -- Space locks precede identity locks. The global UUID lock also serializes
  -- attempts to reuse this identity from a different space during deletion.
  perform pg_advisory_xact_lock(hashtextextended('knowledge-page:'||(p_payload->>'entry_id')::uuid::text,0));
  select * into e from quorum_private.knowledge_entries where id=(p_payload->>'entry_id')::uuid and space_id=s for update;
  if not found then raise exception 'knowledge_entry_unavailable'; end if;
  if e.current_revision<>(p_payload->>'expected_revision')::integer then raise exception 'knowledge_revision_conflict'; end if;
  insert into quorum_private.knowledge_page_tombstones values(e.id,s);
  delete from quorum_private.knowledge_entries where id=e.id;
 end if;
 result:=jsonb_build_object('space_id',s,'operation',p_operation);
 return quorum_private.knowledge_save(op,p,p_request_id,result);
end $$;
create or replace function public.knowledge_write(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_operation text,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare
 q uuid; entry_uuid uuid; e quorum_private.knowledge_entries%rowtype; r quorum_private.knowledge_revisions%rowtype;
 cached jsonb; result jsonb; next_revision integer; state text; typ text; title_value text; body_value text;
 sources jsonb; versions_value jsonb; conflicts jsonb; review_value timestamptz; replacement uuid;
 wiki_sources uuid[]; related uuid[]; kind text; link uuid;
 proof text; verifier uuid; verified_at timestamptz; h text;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'space_id',p_space_id,'payload',p_payload);
 op text:='write:'||p_operation;
begin
 perform quorum_private.knowledge_mutation_lock(p_space_id);
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,true,p_operation='verify');
 if p_operation in ('propose','revise') then
  perform quorum_private.knowledge_fields(p_payload,
   array['entry_id','expected_revision','type','title','body','source_refs','versions','review_after','conflicts_with','entry_kind','source_ids','related_pages'],
   case when p_operation='propose' then array['entry_id','type','title','body'] else array['entry_id','expected_revision','type','title','body'] end);
  if p_operation='propose' and p_payload ? 'expected_revision' then raise exception 'knowledge_invalid_payload'; end if;
 elsif p_operation='verify' then
  perform quorum_private.knowledge_fields(p_payload,array['entry_id','expected_revision','owner_assertion','evidence'],array['entry_id','expected_revision','owner_assertion','evidence']);
  if p_payload->'owner_assertion'<>'true'::jsonb or jsonb_typeof(p_payload->'evidence')<>'string' or length(p_payload->>'evidence') not between 1 and 1000 then raise exception 'knowledge_owner_assertion_required'; end if;
 elsif p_operation='dispute' then
  perform quorum_private.knowledge_fields(p_payload,array['entry_id','expected_revision','evidence','conflicts_with'],array['entry_id','expected_revision','evidence','conflicts_with']);
  if jsonb_typeof(p_payload->'evidence')<>'string' or length(p_payload->>'evidence') not between 1 and 1000 then raise exception 'knowledge_invalid_payload'; end if;
 elsif p_operation='supersede' then
  perform quorum_private.knowledge_fields(p_payload,array['entry_id','expected_revision','replacement_id'],array['entry_id','expected_revision','replacement_id']);
 elsif p_operation='withdraw' then
  perform quorum_private.knowledge_fields(p_payload,array['entry_id','expected_revision'],array['entry_id','expected_revision']);
 else raise exception 'knowledge_invalid_operation'; end if;
 cached:=quorum_private.knowledge_cached(op,p,p_request_id);
 if cached is not null then
  if not quorum_private.knowledge_visible(p_space_id,(cached->>'entry_id')::uuid)
   or not quorum_private.knowledge_visible(p_space_id,(cached->>'entry_id')::uuid,(cached->>'revision')::integer)
   or not exists(select 1 from quorum_private.knowledge_revisions kr
    where kr.space_id=p_space_id and kr.entry_id=(p_payload->>'entry_id')::uuid
     and kr.entry_id=(cached->>'entry_id')::uuid and kr.revision=(cached->>'revision')::integer
     and kr.content_sha256=cached->>'content_sha256')
  then raise insufficient_privilege using message='knowledge_denied'; end if;
  return cached; end if;
 entry_uuid:=(p_payload->>'entry_id')::uuid;
 if p_operation='propose' then
  perform pg_advisory_xact_lock(hashtextextended('knowledge-page:'||entry_uuid::text,0));
  if exists(select 1 from quorum_private.knowledge_page_tombstones where entry_id=entry_uuid) then raise exception 'knowledge_page_immutable'; end if;
  kind:=coalesce(p_payload->>'entry_kind','legacy');
  if kind not in ('legacy','wiki_page') then raise exception 'knowledge_invalid_payload'; end if;
  next_revision:=1;
 else
  select * into e from quorum_private.knowledge_entries where knowledge_entries.id=entry_uuid and space_id=p_space_id for update;
  if not found then raise exception 'knowledge_entry_unavailable'; end if;
  if jsonb_typeof(p_payload->'expected_revision')<>'number' or (p_payload->>'expected_revision')::numeric<>e.current_revision then raise exception 'knowledge_revision_conflict'; end if;
  select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=e.current_revision;
  if r.status in ('superseded','withdrawn') then raise exception 'knowledge_entry_inactive'; end if;
  kind:=e.entry_kind;
  if p_payload ? 'entry_kind' then raise exception 'knowledge_kind_immutable'; end if;
  next_revision:=e.current_revision+1;
 end if;
 if p_operation in ('propose','revise') then
  if kind='wiki_page' and (not p_payload ? 'source_ids' or (p_operation='revise' and not p_payload ?& array['source_ids','related_pages','source_refs','conflicts_with'])) then raise exception 'knowledge_wiki_evidence_required'; end if;
  wiki_sources:=quorum_private.knowledge_uuid_links(coalesce(p_payload->'source_ids','[]'));
  related:=quorum_private.knowledge_uuid_links(coalesce(p_payload->'related_pages','[]'));
  if kind='wiki_page' and cardinality(wiki_sources)=0 then raise exception 'knowledge_wiki_evidence_required'; end if;
  if kind='legacy' and cardinality(wiki_sources)>0 then raise exception 'knowledge_invalid_payload'; end if;
  typ:=p_payload->>'type'; title_value:=p_payload->>'title'; body_value:=p_payload->>'body';
  sources:=coalesce(p_payload->'source_refs','[]'); versions_value:=coalesce(p_payload->'versions','[]');
  conflicts:=coalesce(p_payload->'conflicts_with','[]'); review_value:=(p_payload->>'review_after')::timestamptz;
  if jsonb_typeof(p_payload->'type')<>'string' or typ not in ('decision','fact','hypothesis','known_issue','procedure')
   or jsonb_typeof(p_payload->'title')<>'string' or length(title_value) not between 1 and 200
   or jsonb_typeof(p_payload->'body')<>'string' or octet_length(body_value) not between 1 and 8192 then raise exception 'knowledge_invalid_payload'; end if;
  state:='proposed';
 else
  wiki_sources:=r.source_ids; related:=r.related_pages;
  typ:=r.type; title_value:=r.title; body_value:=r.body; sources:=r.source_refs; versions_value:=r.versions;
  conflicts:=r.conflicts_with; review_value:=r.review_after;
  state:=case p_operation when 'verify' then 'verified' when 'dispute' then 'disputed' when 'supersede' then 'superseded' else 'withdrawn' end;
  if p_operation='verify' then proof:=p_payload->>'evidence'; verifier:=auth.uid(); verified_at:=now(); end if;
  if p_operation='dispute' then proof:=p_payload->>'evidence'; conflicts:=p_payload->'conflicts_with'; end if;
  if p_operation='supersede' then
   replacement:=(p_payload->>'replacement_id')::uuid;
   if replacement=entry_uuid or not exists(select 1 from quorum_private.knowledge_entries ke join quorum_private.knowledge_revisions kr
     on kr.entry_id=ke.id and kr.revision=ke.current_revision where ke.id=replacement and ke.space_id=p_space_id and kr.status not in ('withdrawn','superseded')) then
    raise exception 'knowledge_invalid_link'; end if;
  end if;
 end if;
 if jsonb_typeof(sources)<>'array' or jsonb_typeof(versions_value)<>'array' or jsonb_typeof(conflicts)<>'array' then raise exception 'knowledge_invalid_payload'; end if;
 if jsonb_array_length(sources)>8 or jsonb_array_length(versions_value)>8 or jsonb_array_length(conflicts)>8
  or exists(select 1 from jsonb_array_elements(sources) x where jsonb_typeof(x)<>'string' or length(x#>>'{}') not between 1 and 512)
  or exists(select 1 from jsonb_array_elements(versions_value) x where jsonb_typeof(x)<>'string' or length(x#>>'{}') not between 1 and 80) then raise exception 'knowledge_invalid_payload'; end if;
 -- Lifecycle operations preserve historical links, even after a target is
 -- withdrawn. Fresh evidence and relation assignments must resolve now.
 if p_operation in ('propose','revise') then
  if exists(select 1 from unnest(wiki_sources) x where x=entry_uuid or not exists(select 1 from quorum_private.knowledge_sources ks where ks.space_id=p_space_id and ks.source_id=x and not ks.withdrawn)) then raise exception 'knowledge_invalid_link'; end if;
  foreach link in array related loop
   if link=entry_uuid or not quorum_private.knowledge_visible(p_space_id,link) then raise exception 'knowledge_invalid_link'; end if;
  end loop;
 end if;
 if p_operation in ('propose','revise','dispute') then
  foreach link in array quorum_private.knowledge_uuid_links(conflicts) loop
   if link=entry_uuid or not quorum_private.knowledge_visible(p_space_id,link) then raise exception 'knowledge_invalid_link'; end if;
  end loop;
 end if;
 if p_operation='supersede' and not quorum_private.knowledge_visible(p_space_id,replacement) then raise exception 'knowledge_invalid_link'; end if;
 h:=encode(sha256(convert_to(jsonb_build_object('type',typ,'title',title_value,'body',body_value,
   'entry_kind',kind,'source_ids',wiki_sources,'related_pages',related,'source_refs',sources,'versions',versions_value,'review_after',review_value,'conflicts_with',conflicts)::text,'UTF8')),'hex');
 if p_operation='propose' then insert into quorum_private.knowledge_entries(id,space_id,current_revision,entry_kind) values(entry_uuid,p_space_id,1,kind);
 else
  update quorum_private.knowledge_entries set current_revision=next_revision where knowledge_entries.id=e.id;
  delete from quorum_private.knowledge_vectors where entry_id=e.id;
 end if;
 insert into quorum_private.knowledge_revisions(entry_id,revision,space_id,type,status,title,body,source_refs,versions,
  review_after,conflicts_with,replacement_id,author_user,author_agent,quorum_id,content_sha256,verification_user,verification_at,evidence,source_ids,related_pages)
 values(entry_uuid,next_revision,p_space_id,typ,state,title_value,body_value,sources,versions_value,review_value,conflicts,replacement,
  auth.uid(),p_agent_id,q,h,verifier,verified_at,proof,wiki_sources,related);
 result:=jsonb_build_object('entry_id',entry_uuid,'revision',next_revision,'status',state,'content_sha256',h);
 return quorum_private.knowledge_save(op,p,p_request_id,result);
end $$;
create or replace function public.knowledge_index(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_entry_id uuid,p_revision integer,
 p_content_sha256 text,p_model text,p_dimensions integer,p_vector jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; cfg quorum_private.knowledge_spaces%rowtype; e quorum_private.knowledge_entries%rowtype;
 r quorum_private.knowledge_revisions%rowtype; v extensions.vector; cached jsonb; result jsonb;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'space_id',p_space_id,'entry_id',p_entry_id,
  'revision',p_revision,'content_sha256',p_content_sha256,'model',p_model,'dimensions',p_dimensions,'vector',p_vector);
begin
 perform quorum_private.knowledge_mutation_lock(p_space_id);
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,true);
 select * into cfg from quorum_private.knowledge_spaces where id=p_space_id;
 if cfg.vector_model is null then raise exception 'knowledge_indexing_unconfigured'; end if;
 if p_model is distinct from cfg.vector_model or p_dimensions is distinct from cfg.vector_dimensions then raise exception 'knowledge_vector_incompatible'; end if;
 select * into e from quorum_private.knowledge_entries where id=p_entry_id and space_id=p_space_id for update;
 if not found then raise exception 'knowledge_stale_revision'; end if;
 select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=e.current_revision;
 if p_revision is distinct from e.current_revision or p_content_sha256 is distinct from r.content_sha256 or not quorum_private.knowledge_visible(p_space_id,e.id) or r.status in ('superseded','withdrawn') then raise exception 'knowledge_stale_revision'; end if;
 v:=quorum_private.knowledge_vector(p_vector,p_dimensions);
 cached:=quorum_private.knowledge_cached('index',p,p_request_id);
 if cached is not null then return cached; end if;
 insert into quorum_private.knowledge_vectors values(e.id,e.current_revision,r.content_sha256,p_model,p_dimensions,v,auth.uid(),now())
  on conflict(entry_id) do update set revision=excluded.revision,content_sha256=excluded.content_sha256,model=excluded.model,
   dimensions=excluded.dimensions,embedding=excluded.embedding,indexed_by=excluded.indexed_by,indexed_at=excluded.indexed_at;
 result:=jsonb_build_object('entry_id',e.id,'revision',e.current_revision,'indexed',true);
 return quorum_private.knowledge_save('index',p,p_request_id,result);
end $$;
create or replace function public.knowledge_search(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_query text,
 p_limit integer default 5,p_context_budget integer default 3000,p_model text default null,p_query_vector jsonb default null)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; cfg quorum_private.knowledge_spaces%rowtype; v extensions.vector; coverage integer; total integer;
 vector_mode boolean:=false; result jsonb; item jsonb; results jsonb:='[]'; candidate record; truncated boolean:=false; seen integer:=0;
begin
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id);
 if p_query is null or length(p_query) not between 1 and 1000 or p_limit is null or p_limit not between 1 and 10
  or p_context_budget is null or p_context_budget not between 512 and 6000 then raise exception 'knowledge_invalid_payload'; end if;
 select * into cfg from quorum_private.knowledge_spaces where id=p_space_id;
 if (p_model is null)<>(p_query_vector is null) then raise exception 'knowledge_vector_incompatible'; end if;
 if p_query_vector is not null then
  if cfg.vector_model is null or p_model is distinct from cfg.vector_model then raise exception 'knowledge_vector_incompatible'; end if;
  v:=quorum_private.knowledge_vector(p_query_vector,cfg.vector_dimensions); vector_mode:=true;
 end if;
 select count(*),count(kv.entry_id) into total,coverage from quorum_private.knowledge_entries ke
 join quorum_private.knowledge_revisions kr on kr.entry_id=ke.id and kr.revision=ke.current_revision
 left join quorum_private.knowledge_vectors kv on kv.entry_id=ke.id and kv.revision=kr.revision and kv.content_sha256=kr.content_sha256
  and kv.model=cfg.vector_model and kv.dimensions=cfg.vector_dimensions
 where ke.space_id=p_space_id and kr.status not in ('withdrawn','superseded') and quorum_private.knowledge_visible(p_space_id,ke.id);
 result:=jsonb_build_object('results',results,'ranking',case when vector_mode and coverage>0 then 'rrf' else 'lexical' end,
  'indexing',jsonb_build_object('state',case when cfg.vector_model is null then 'unconfigured' when coverage<total then 'partial' else 'ready' end,
   'indexed',coverage,'eligible',total,'extension_version',(select extversion from pg_extension where extname='vector')),'truncated',false);
 for candidate in
  with active as materialized (
   select kr.*,kv.embedding from quorum_private.knowledge_entries ke
   join quorum_private.knowledge_revisions kr on kr.entry_id=ke.id and kr.revision=ke.current_revision
   left join quorum_private.knowledge_vectors kv on kv.entry_id=ke.id and kv.revision=kr.revision and kv.content_sha256=kr.content_sha256
    and kv.model=cfg.vector_model and kv.dimensions=cfg.vector_dimensions
   where ke.space_id=p_space_id and kr.status not in ('withdrawn','superseded') and quorum_private.knowledge_visible(p_space_id,ke.id)
  ), lexical as (
   select entry_id,row_number() over(order by (strpos(lower(title||' '||body),lower(p_query))>0) desc,
     (status='verified') desc,ts_rank_cd(search,plainto_tsquery('simple',p_query)) desc,entry_id) rank
   from active where search@@plainto_tsquery('simple',p_query) or strpos(lower(title||' '||body),lower(p_query))>0
  ), semantic as (
   select entry_id,row_number() over(order by embedding operator(extensions.<=>) v,entry_id) rank
   from active where vector_mode and embedding is not null
  ), ranks as (
   select coalesce(l.entry_id,s.entry_id) entry_id,
    coalesce(1.0/(60+l.rank),0)+coalesce(1.0/(60+s.rank),0) score
   from lexical l full join semantic s using(entry_id)
  ) select a.*,ranks.score from ranks join active a using(entry_id)
   order by ranks.score desc,(a.status='verified') desc,a.entry_id limit 11
 loop
  seen:=seen+1;
  if seen>p_limit then truncated:=true; exit; end if;
  select quorum_private.knowledge_projection(kr) into item from quorum_private.knowledge_revisions kr where kr.entry_id=candidate.entry_id and kr.revision=candidate.revision;
  item:=item||jsonb_build_object('excerpt',left(candidate.body,600),'excerpt_truncated',length(candidate.body)>600);
  -- false is one character longer than true in the final serialized result.
  if length((result||jsonb_build_object('results',results||jsonb_build_array(item),'truncated',false))::text)>p_context_budget then
   truncated:=true; exit; end if;
  results:=results||jsonb_build_array(item);
 end loop;
 return result||jsonb_build_object('results',results,'truncated',truncated);
end $$;
drop function public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean);
create or replace function public.knowledge_read(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_entry_id uuid,
 p_revision integer default null,p_offset integer default 0,p_length integer default 4096,p_audit boolean default false,p_include_history boolean default false)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; e quorum_private.knowledge_entries%rowtype; r quorum_private.knowledge_revisions%rowtype;
begin
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,false,coalesce(p_audit,false));
 if p_include_history is null or p_audit is null or p_offset is null or p_offset<0 or p_length is null or p_length not between 1 and 4096 then raise exception 'knowledge_invalid_payload'; end if;
 select * into e from quorum_private.knowledge_entries where id=p_entry_id and space_id=p_space_id;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=coalesce(p_revision,e.current_revision);
 if not found or (not p_audit and (r.revision<>e.current_revision or not quorum_private.knowledge_visible(p_space_id,e.id,r.revision) or (r.status='superseded' and not p_include_history))) then
  raise insufficient_privilege using message='knowledge_denied'; end if;
 return quorum_private.knowledge_projection(r)||jsonb_build_object('body',substring(r.body from p_offset+1 for p_length),'offset',p_offset,'next_offset',case when p_offset::bigint+p_length<length(r.body) then p_offset+p_length else null end);
end $$;

create function public.knowledge_catalog(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_after_id uuid default null,p_limit integer default 5,p_context_budget integer default 3000)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare r record; item jsonb; items jsonb:='[]'; result jsonb;
 cursor_id uuid:=p_after_id; truncated boolean:=false; seen integer:=0; blocked boolean:=false;
begin
 perform quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id);
 if p_limit is null or p_limit not between 1 and 10 or p_context_budget is null or p_context_budget not between 512 and 6000 then raise exception 'knowledge_invalid_payload'; end if;
 for r in select kr.*,ke.entry_kind from quorum_private.knowledge_entries ke join quorum_private.knowledge_revisions kr on kr.entry_id=ke.id and kr.revision=ke.current_revision
  where ke.space_id=p_space_id and (p_after_id is null or ke.id>p_after_id) and kr.status not in ('withdrawn','superseded') and quorum_private.knowledge_visible(p_space_id,ke.id) order by ke.id limit 11
 loop
  seen:=seen+1;
  if seen>p_limit then truncated:=true; exit; end if;
  -- Catalog is metadata-only: never expand graph/source projections just to
  -- discard them. Visibility was already checked in the scoped candidate query.
  item:=jsonb_build_object('entry_id',r.entry_id,'entry_kind',r.entry_kind,'title',r.title,
   'type',r.type,'status',r.status,'revision',r.revision,'content_sha256',r.content_sha256,'created_at',r.created_at);
  result:=jsonb_build_object('results',items||jsonb_build_array(item),'next_cursor',r.entry_id,'truncated',false,'budget_exhausted',false);
  if length(result::text)>p_context_budget then truncated:=true; blocked:=jsonb_array_length(items)=0; exit; end if;
  items:=items||jsonb_build_array(item); cursor_id:=r.entry_id;
 end loop;
 -- A blocked first item keeps the cursor and says why; callers must increase
 -- the budget instead of mistaking an empty page for exhausted results.
 return jsonb_build_object('results',items,'next_cursor',case when truncated then cursor_id else null end,'truncated',truncated,'budget_exhausted',blocked);
end $$;

revoke all on function quorum_private.knowledge_immutable(),quorum_private.knowledge_mutation_lock(uuid),
 quorum_private.knowledge_visible(uuid,uuid,integer),quorum_private.knowledge_uuid_links(jsonb),
 quorum_private.knowledge_projection(quorum_private.knowledge_revisions) from public,anon,authenticated,service_role;
revoke all on function public.knowledge_source_write(uuid,text,uuid,jsonb,text),
 public.knowledge_source_read(uuid,text,uuid,uuid,integer,integer,boolean),
 public.knowledge_catalog(uuid,text,uuid,uuid,integer,integer),
 public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean,boolean) from public,anon,authenticated,service_role;
grant execute on function public.knowledge_source_write(uuid,text,uuid,jsonb,text),
 public.knowledge_source_read(uuid,text,uuid,uuid,integer,integer,boolean),
 public.knowledge_catalog(uuid,text,uuid,uuid,integer,integer),
 public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean,boolean) to authenticated;
