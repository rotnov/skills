-- SOURCE ONLY. Reviewed administrator deployment, never executed by the skill.
-- Requires the native/open-access binding contract. No provider/network calls.
-- pgvector is optional derived data; lexical search is always available.
create extension if not exists vector with schema extensions;
do $migration$
begin
 if to_regprocedure('quorum_private.native_session(uuid,text)') is null
  or to_regprocedure('quorum_private.verified_actor(uuid)') is null then
  raise exception 'knowledge_native_binding_required'; end if;
 if (select n.nspname from pg_extension e join pg_namespace n on n.oid=e.extnamespace where e.extname='vector')<>'extensions' then
  raise exception 'knowledge_vector_schema_required'; end if;
end $migration$;

create table quorum_private.knowledge_spaces (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 name text not null check(length(name) between 1 and 200), created_at timestamptz not null default now(),
 vector_model text, vector_dimensions integer,
 check ((vector_model is null and vector_dimensions is null) or
  (length(vector_model) between 1 and 200 and vector_dimensions between 1 and 2000))
);
create table quorum_private.knowledge_grants (
 space_id uuid references quorum_private.knowledge_spaces(id) on delete cascade,
 user_id uuid references auth.users(id), permission text not null check(permission in ('reader','writer')),
 primary key(space_id,user_id)
);
create table quorum_private.knowledge_attachments (
 space_id uuid references quorum_private.knowledge_spaces(id) on delete cascade,
 quorum_id uuid references quorum_private.quorums(id), project_ref text not null check(length(project_ref) between 1 and 500),
 primary key(space_id,quorum_id,project_ref)
);
create table quorum_private.knowledge_entries (
 id uuid primary key, space_id uuid not null references quorum_private.knowledge_spaces(id),
 current_revision integer not null check(current_revision>0), unique(space_id,id)
);
create table quorum_private.knowledge_revisions (
 entry_id uuid references quorum_private.knowledge_entries(id) on delete cascade,
 revision integer not null check(revision>0), space_id uuid not null,
 type text not null check(type in ('decision','fact','hypothesis','known_issue','procedure')),
 status text not null check(status in ('proposed','verified','disputed','superseded','withdrawn')),
 title text not null check(length(title) between 1 and 200),
 body text not null check(octet_length(body) between 1 and 8192),
 source_refs jsonb not null, versions jsonb not null, review_after timestamptz,
 conflicts_with jsonb not null default '[]', replacement_id uuid,
 author_user uuid not null, author_agent uuid not null, quorum_id uuid not null,
 created_at timestamptz not null default now(), content_sha256 text not null check(content_sha256 ~ '^[0-9a-f]{64}$'),
 verification_user uuid, verification_at timestamptz, evidence text,
 search tsvector generated always as (to_tsvector('simple',title||' '||body)) stored,
 primary key(entry_id,revision),
 foreign key(space_id,entry_id) references quorum_private.knowledge_entries(space_id,id) on delete cascade,
 check((status='verified' and verification_user is not null and verification_at is not null and length(evidence)>0)
   or (status<>'verified' and verification_user is null and verification_at is null))
);
alter table quorum_private.knowledge_entries add foreign key(id,current_revision)
 references quorum_private.knowledge_revisions(entry_id,revision) deferrable initially deferred;
create index knowledge_revisions_search on quorum_private.knowledge_revisions using gin(search);
create index knowledge_entries_space on quorum_private.knowledge_entries(space_id,id);
create table quorum_private.knowledge_vectors (
 entry_id uuid primary key references quorum_private.knowledge_entries(id) on delete cascade,
 revision integer not null, content_sha256 text not null, model text not null,
 dimensions integer not null check(dimensions between 1 and 2000), embedding extensions.vector not null,
 indexed_by uuid not null, indexed_at timestamptz not null default now(),
 foreign key(entry_id,revision) references quorum_private.knowledge_revisions(entry_id,revision) on delete cascade,
 check(extensions.vector_dims(embedding)=dimensions)
);
-- Request journals contain fingerprints and small identifiers only, never text or vectors.
create table quorum_private.knowledge_requests (
 actor uuid not null, operation text not null, request_id text not null check(length(request_id) between 1 and 128),
 fingerprint text not null, result jsonb not null, primary key(actor,operation,request_id)
);
alter table quorum_private.knowledge_spaces enable row level security;
alter table quorum_private.knowledge_grants enable row level security;
alter table quorum_private.knowledge_attachments enable row level security;
alter table quorum_private.knowledge_entries enable row level security;
alter table quorum_private.knowledge_revisions enable row level security;
alter table quorum_private.knowledge_vectors enable row level security;
alter table quorum_private.knowledge_requests enable row level security;
revoke all on quorum_private.knowledge_spaces,quorum_private.knowledge_grants,quorum_private.knowledge_attachments,
 quorum_private.knowledge_entries,quorum_private.knowledge_revisions,quorum_private.knowledge_vectors,
 quorum_private.knowledge_requests from public,anon,authenticated,service_role;

create function quorum_private.knowledge_fields(p jsonb,allowed text[],required text[])
returns void language plpgsql set search_path=pg_catalog as $$
begin
 if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072
  or exists(select 1 from jsonb_object_keys(p) k where not k=any(allowed))
  or exists(select 1 from unnest(required) k where not p ? k or p->k='null'::jsonb) then
  raise exception 'knowledge_invalid_payload'; end if;
end $$;
create function quorum_private.knowledge_binding(a uuid,sid text)
returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid;
begin
 if current_setting('transaction_isolation')<>'read committed' then raise exception 'knowledge_requires_read_committed'; end if;
 q:=quorum_private.native_session(a,sid);
 -- Serialize with revocation. Recheck the rows after any wait, before data or cache access.
 perform 1 from quorum_private.agents ag join quorum_private.devices d on d.id=ag.device_id
 join quorum_private.memberships m on m.quorum_id=ag.quorum_id and m.user_id=ag.owner_id
 where ag.id=a and ag.owner_id=auth.uid() and d.owner_id=auth.uid() and ag.session_ref=sid
  and not ag.revoked and not d.revoked and not m.revoked for share of ag,d,m;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 return q;
end $$;
create function quorum_private.knowledge_scope(a uuid,sid text,s uuid,writer boolean default false,owner_only boolean default false)
returns uuid language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; own uuid; permitted text;
begin
 q:=quorum_private.knowledge_binding(a,sid);
 select owner_id into own from quorum_private.knowledge_spaces where id=s for share;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 perform 1 from quorum_private.knowledge_attachments ka join quorum_private.agents ag
  on ag.id=a and ag.quorum_id=ka.quorum_id and ag.project_ref=ka.project_ref
 where ka.space_id=s and ka.quorum_id=q for share of ka;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 if own=auth.uid() then return q; end if;
 if owner_only then raise insufficient_privilege using message='knowledge_denied'; end if;
 select permission into permitted from quorum_private.knowledge_grants
  where space_id=s and user_id=auth.uid() for share;
 if not found or (writer and permitted<>'writer') then
  raise insufficient_privilege using message='knowledge_denied'; end if;
 return q;
end $$;
create function quorum_private.knowledge_cached(op text,p jsonb,req text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare previous quorum_private.knowledge_requests%rowtype; h text;
begin
 if req is null or length(req) not between 1 and 128 then raise exception 'knowledge_request_id_required'; end if;
 h:=encode(sha256(convert_to(p::text,'UTF8')),'hex');
 perform pg_advisory_xact_lock(hashtextextended(auth.uid()::text||'knowledge:'||op||req,0));
 select * into previous from quorum_private.knowledge_requests where actor=auth.uid() and operation=op and request_id=req;
 if found then
  if previous.fingerprint<>h then raise exception 'knowledge_idempotency_conflict'; end if;
  return previous.result;
 end if;
 return null;
end $$;
create function quorum_private.knowledge_save(op text,p jsonb,req text,result jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
begin
 insert into quorum_private.knowledge_requests values(auth.uid(),op,req,encode(sha256(convert_to(p::text,'UTF8')),'hex'),result);
 return result;
end $$;

create function public.knowledge_admin(p_agent_id uuid,p_session_ref text,p_operation text,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; s uuid; own uuid; cached jsonb; result jsonb; e quorum_private.knowledge_entries%rowtype;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'payload',p_payload);
 op text:='admin:'||p_operation;
begin
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
 elsif p_operation='purge' then
  select * into e from quorum_private.knowledge_entries where id=(p_payload->>'entry_id')::uuid and space_id=s for update;
  if not found then raise exception 'knowledge_entry_unavailable'; end if;
  if e.current_revision<>(p_payload->>'expected_revision')::integer then raise exception 'knowledge_revision_conflict'; end if;
  delete from quorum_private.knowledge_entries where id=e.id;
 end if;
 result:=jsonb_build_object('space_id',s,'operation',p_operation);
 return quorum_private.knowledge_save(op,p,p_request_id,result);
end $$;

create function public.knowledge_write(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_operation text,p_payload jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare
 q uuid; entry_uuid uuid; e quorum_private.knowledge_entries%rowtype; r quorum_private.knowledge_revisions%rowtype;
 cached jsonb; result jsonb; next_revision integer; state text; typ text; title_value text; body_value text;
 sources jsonb; versions_value jsonb; conflicts jsonb; review_value timestamptz; replacement uuid;
 proof text; verifier uuid; verified_at timestamptz; h text;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'space_id',p_space_id,'payload',p_payload);
 op text:='write:'||p_operation;
begin
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,true,p_operation='verify');
 if p_operation in ('propose','revise') then
  perform quorum_private.knowledge_fields(p_payload,
   array['entry_id','expected_revision','type','title','body','source_refs','versions','review_after','conflicts_with'],
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
 if cached is not null then return cached; end if;
 entry_uuid:=(p_payload->>'entry_id')::uuid;
 if p_operation='propose' then next_revision:=1;
 else
  select * into e from quorum_private.knowledge_entries where knowledge_entries.id=entry_uuid and space_id=p_space_id for update;
  if not found then raise exception 'knowledge_entry_unavailable'; end if;
  if jsonb_typeof(p_payload->'expected_revision')<>'number' or (p_payload->>'expected_revision')::numeric<>e.current_revision then raise exception 'knowledge_revision_conflict'; end if;
  select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=e.current_revision;
  if r.status in ('superseded','withdrawn') then raise exception 'knowledge_entry_inactive'; end if;
  next_revision:=e.current_revision+1;
 end if;
 if p_operation in ('propose','revise') then
  typ:=p_payload->>'type'; title_value:=p_payload->>'title'; body_value:=p_payload->>'body';
  sources:=coalesce(p_payload->'source_refs','[]'); versions_value:=coalesce(p_payload->'versions','[]');
  conflicts:=coalesce(p_payload->'conflicts_with','[]'); review_value:=(p_payload->>'review_after')::timestamptz;
  if jsonb_typeof(p_payload->'type')<>'string' or typ not in ('decision','fact','hypothesis','known_issue','procedure')
   or jsonb_typeof(p_payload->'title')<>'string' or length(title_value) not between 1 and 200
   or jsonb_typeof(p_payload->'body')<>'string' or octet_length(body_value) not between 1 and 8192 then raise exception 'knowledge_invalid_payload'; end if;
  state:='proposed';
 else
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
 if exists(select 1 from jsonb_array_elements(conflicts) x where jsonb_typeof(x)<>'string' or x#>>'{}'=entry_uuid::text
    or not exists(select 1 from quorum_private.knowledge_entries ke where ke.id=(x#>>'{}')::uuid and ke.space_id=p_space_id)) then raise exception 'knowledge_invalid_link'; end if;
 h:=encode(sha256(convert_to(jsonb_build_object('type',typ,'title',title_value,'body',body_value,
   'source_refs',sources,'versions',versions_value,'review_after',review_value,'conflicts_with',conflicts)::text,'UTF8')),'hex');
 if p_operation='propose' then insert into quorum_private.knowledge_entries values(entry_uuid,p_space_id,1);
 else
  update quorum_private.knowledge_entries set current_revision=next_revision where knowledge_entries.id=e.id;
  delete from quorum_private.knowledge_vectors where entry_id=e.id;
 end if;
 insert into quorum_private.knowledge_revisions(entry_id,revision,space_id,type,status,title,body,source_refs,versions,
  review_after,conflicts_with,replacement_id,author_user,author_agent,quorum_id,content_sha256,verification_user,verification_at,evidence)
 values(entry_uuid,next_revision,p_space_id,typ,state,title_value,body_value,sources,versions_value,review_value,conflicts,replacement,
  auth.uid(),p_agent_id,q,h,verifier,verified_at,proof);
 result:=jsonb_build_object('entry_id',entry_uuid,'revision',next_revision,'status',state,'content_sha256',h);
 return quorum_private.knowledge_save(op,p,p_request_id,result);
end $$;

-- Explicit vector arrays only: dimension/model/config and revision/hash are checked
-- before a cached success, so reindex retries cannot resurrect obsolete content.
create function quorum_private.knowledge_vector(v jsonb,d integer)
returns extensions.vector language plpgsql set search_path=pg_catalog as $$
declare value extensions.vector;
begin
 if v is null or jsonb_typeof(v)<>'array' then raise exception 'knowledge_invalid_vector'; end if;
 if d not between 1 and 2000 or jsonb_array_length(v)<>d or exists(select 1 from jsonb_array_elements(v) x
   where jsonb_typeof(x)<>'number' or abs((x#>>'{}')::numeric)>3.4e38) then raise exception 'knowledge_invalid_vector'; end if;
 begin value:=v::text::extensions.vector; exception when others then raise exception 'knowledge_invalid_vector'; end;
 if extensions.vector_norm(value)=0 then raise exception 'knowledge_invalid_vector'; end if;
 return value;
end $$;
create function public.knowledge_index(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_entry_id uuid,p_revision integer,
 p_content_sha256 text,p_model text,p_dimensions integer,p_vector jsonb,p_request_id text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; cfg quorum_private.knowledge_spaces%rowtype; e quorum_private.knowledge_entries%rowtype;
 r quorum_private.knowledge_revisions%rowtype; v extensions.vector; cached jsonb; result jsonb;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'space_id',p_space_id,'entry_id',p_entry_id,
  'revision',p_revision,'content_sha256',p_content_sha256,'model',p_model,'dimensions',p_dimensions,'vector',p_vector);
begin
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,true);
 select * into cfg from quorum_private.knowledge_spaces where id=p_space_id;
 if cfg.vector_model is null then raise exception 'knowledge_indexing_unconfigured'; end if;
 if p_model is distinct from cfg.vector_model or p_dimensions is distinct from cfg.vector_dimensions then raise exception 'knowledge_vector_incompatible'; end if;
 select * into e from quorum_private.knowledge_entries where id=p_entry_id and space_id=p_space_id for update;
 if not found then raise exception 'knowledge_stale_revision'; end if;
 select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=e.current_revision;
 if p_revision is distinct from e.current_revision or p_content_sha256 is distinct from r.content_sha256 or r.status in ('superseded','withdrawn') then raise exception 'knowledge_stale_revision'; end if;
 v:=quorum_private.knowledge_vector(p_vector,p_dimensions);
 cached:=quorum_private.knowledge_cached('index',p,p_request_id);
 if cached is not null then return cached; end if;
 insert into quorum_private.knowledge_vectors values(e.id,e.current_revision,r.content_sha256,p_model,p_dimensions,v,auth.uid(),now())
  on conflict(entry_id) do update set revision=excluded.revision,content_sha256=excluded.content_sha256,model=excluded.model,
   dimensions=excluded.dimensions,embedding=excluded.embedding,indexed_by=excluded.indexed_by,indexed_at=excluded.indexed_at;
 result:=jsonb_build_object('entry_id',e.id,'revision',e.current_revision,'indexed',true);
 return quorum_private.knowledge_save('index',p,p_request_id,result);
end $$;

create function public.knowledge_search(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_query text,
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
 where ke.space_id=p_space_id and kr.status not in ('withdrawn','superseded');
 result:=jsonb_build_object('results',results,'ranking',case when vector_mode and coverage>0 then 'rrf' else 'lexical' end,
  'indexing',jsonb_build_object('state',case when cfg.vector_model is null then 'unconfigured' when coverage<total then 'partial' else 'ready' end,
   'indexed',coverage,'eligible',total,'extension_version',(select extversion from pg_extension where extname='vector')),'truncated',false);
 for candidate in
  with active as materialized (
   select kr.*,kv.embedding from quorum_private.knowledge_entries ke
   join quorum_private.knowledge_revisions kr on kr.entry_id=ke.id and kr.revision=ke.current_revision
   left join quorum_private.knowledge_vectors kv on kv.entry_id=ke.id and kv.revision=kr.revision and kv.content_sha256=kr.content_sha256
    and kv.model=cfg.vector_model and kv.dimensions=cfg.vector_dimensions
   where ke.space_id=p_space_id and kr.status not in ('withdrawn','superseded')
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
  item:=jsonb_build_object('entry_id',candidate.entry_id,'revision',candidate.revision,'type',candidate.type,'status',candidate.status,
   'title',candidate.title,'excerpt',left(candidate.body,600),'source_refs',candidate.source_refs,'versions',candidate.versions,
   'content_sha256',candidate.content_sha256,'author_user',candidate.author_user,'author_agent',candidate.author_agent,
   'quorum_id',candidate.quorum_id,'created_at',candidate.created_at,'review_after',candidate.review_after,
   'conflicts_with',candidate.conflicts_with,'replacement_id',candidate.replacement_id,
   'verification_user',candidate.verification_user,'verification_at',candidate.verification_at,'evidence',candidate.evidence,
   'supersedes',coalesce((select jsonb_agg(entry_id order by entry_id) from quorum_private.knowledge_revisions old
     join quorum_private.knowledge_entries oe on oe.id=old.entry_id and oe.current_revision=old.revision
     where oe.space_id=p_space_id and old.replacement_id=candidate.entry_id and old.status='superseded'),'[]'),
   'excerpt_truncated',length(candidate.body)>600);
  -- false is one character longer than true in the final serialized result.
  if length((result||jsonb_build_object('results',results||jsonb_build_array(item),'truncated',false))::text)>p_context_budget then
   truncated:=true; exit; end if;
  results:=results||jsonb_build_array(item);
 end loop;
 return result||jsonb_build_object('results',results,'truncated',truncated);
end $$;

create function public.knowledge_read(p_agent_id uuid,p_session_ref text,p_space_id uuid,p_entry_id uuid,
 p_revision integer default null,p_offset integer default 0,p_length integer default 4096,p_audit boolean default false)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare q uuid; e quorum_private.knowledge_entries%rowtype; r quorum_private.knowledge_revisions%rowtype;
begin
 q:=quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,false,coalesce(p_audit,false));
 if p_audit is null or p_offset is null or p_offset<0 or p_length is null or p_length not between 1 and 4096 then raise exception 'knowledge_invalid_payload'; end if;
 select * into e from quorum_private.knowledge_entries where id=p_entry_id and space_id=p_space_id;
 if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 select * into r from quorum_private.knowledge_revisions where entry_id=e.id and revision=coalesce(p_revision,e.current_revision);
 if not found or (not p_audit and (r.revision<>e.current_revision or r.status in ('withdrawn','superseded'))) then
  raise insufficient_privilege using message='knowledge_denied'; end if;
 return jsonb_build_object('entry_id',r.entry_id,'revision',r.revision,'type',r.type,'status',r.status,'title',r.title,
  'body',substring(r.body from p_offset+1 for p_length),'offset',p_offset,'next_offset',case when p_offset+p_length<length(r.body) then p_offset+p_length else null end,
  'content_sha256',r.content_sha256,'source_refs',r.source_refs,'versions',r.versions,'conflicts_with',r.conflicts_with,
  'replacement_id',r.replacement_id,'author_user',r.author_user,'author_agent',r.author_agent,'quorum_id',r.quorum_id,
  'created_at',r.created_at,'review_after',r.review_after,'verification_user',r.verification_user,'verification_at',r.verification_at,'evidence',r.evidence);
end $$;

revoke all on function quorum_private.knowledge_fields(jsonb,text[],text[]),quorum_private.knowledge_binding(uuid,text),
 quorum_private.knowledge_scope(uuid,text,uuid,boolean,boolean),quorum_private.knowledge_cached(text,jsonb,text),
 quorum_private.knowledge_save(text,jsonb,text,jsonb),quorum_private.knowledge_vector(jsonb,integer)
 from public,anon,authenticated,service_role;
revoke all on function public.knowledge_admin(uuid,text,text,jsonb,text),public.knowledge_write(uuid,text,uuid,text,jsonb,text),
 public.knowledge_index(uuid,text,uuid,uuid,integer,text,text,integer,jsonb,text),
 public.knowledge_search(uuid,text,uuid,text,integer,integer,text,jsonb),public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean)
 from public,anon,authenticated,service_role;
grant execute on function public.knowledge_admin(uuid,text,text,jsonb,text),public.knowledge_write(uuid,text,uuid,text,jsonb,text),
 public.knowledge_index(uuid,text,uuid,uuid,integer,text,text,integer,jsonb,text),
 public.knowledge_search(uuid,text,uuid,text,integer,integer,text,jsonb),public.knowledge_read(uuid,text,uuid,uuid,integer,integer,integer,boolean)
 to authenticated;
