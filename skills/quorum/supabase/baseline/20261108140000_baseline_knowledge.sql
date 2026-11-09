-- GENERATED. Do not edit. Quorum server baseline, optional project knowledge (pgvector).
-- Covers references/server-bootstrap.sql and supabase/migrations through 20261108140000.
-- Sources sha256 32c66895f79f44b19c12ba01cec091b445016bf0c5f938d44843984cdbb1512a (tests/quorum_baseline_support.py --check).
-- Regenerate: QUORUM_BASELINE_DOCKER=1 uv run --frozen python tests/quorum_baseline_support.py --write
-- Optional. Apply after 20261108140000_baseline.sql, in one transaction, before any newer migration.
-- See references/server.md#fresh-project and #baseline-maintenance-window.
DO $baseline$ BEGIN
 IF pg_catalog.to_regclass('quorum_private.schema_baseline') IS NULL THEN
  RAISE EXCEPTION 'quorum_core_baseline_required';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM quorum_private.schema_baseline WHERE part='core' AND covers_through='20261108140000') THEN
  RAISE EXCEPTION 'quorum_core_baseline_required';
 END IF;
 IF EXISTS(SELECT 1 FROM quorum_private.schema_baseline WHERE part='knowledge') THEN
  RAISE EXCEPTION 'quorum_knowledge_baseline_present';
 END IF;
END $baseline$;
SET LOCAL check_function_bodies = off;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

CREATE FUNCTION public.knowledge_admin(p_agent_id uuid, p_session_ref text, p_operation text, p_payload jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare s uuid; own uuid; cached jsonb; result jsonb;
 p jsonb:=jsonb_build_object('agent_id',p_agent_id,'session_ref',p_session_ref,'payload',p_payload);
begin
 if p_operation is distinct from 'default_role' then
  return quorum_private.knowledge_admin_before_default_role(p_agent_id,p_session_ref,p_operation,p_payload,p_request_id);
 end if;
 -- The reviewed core's order: space mutation lock, binding, payload, owned space row, request journal.
 perform quorum_private.knowledge_mutation_lock((p_payload->>'space_id')::uuid);
 perform quorum_private.knowledge_binding(p_agent_id,p_session_ref);
 perform quorum_private.knowledge_fields(p_payload,array['space_id','permission'],array['space_id','permission']);
 if jsonb_typeof(p_payload->'permission')<>'string' or p_payload->>'permission' not in ('reader','writer','none') then
  raise exception 'knowledge_invalid_payload'; end if;
 s:=(p_payload->>'space_id')::uuid;
 select owner_id into own from quorum_private.knowledge_spaces where id=s for update;
 if not found or own<>auth.uid() then raise insufficient_privilege using message='knowledge_denied'; end if;
 cached:=quorum_private.knowledge_cached('admin:default_role',p,p_request_id);
 if cached is not null then return cached; end if;
 if p_payload->>'permission'='none' then
  delete from quorum_private.knowledge_member_defaults where space_id=s;
 else
  insert into quorum_private.knowledge_member_defaults(space_id,permission) values(s,p_payload->>'permission')
   on conflict(space_id) do update set permission=excluded.permission,updated_at=now();
 end if;
 result:=jsonb_build_object('space_id',s,'operation','default_role','permission',p_payload->>'permission');
 return quorum_private.knowledge_save('admin:default_role',p,p_request_id,result);
end $$;

CREATE FUNCTION public.knowledge_catalog(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_after_id uuid DEFAULT NULL::uuid, p_limit integer DEFAULT 5, p_context_budget integer DEFAULT 3000) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION public.knowledge_index(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer, p_content_sha256 text, p_model text, p_dimensions integer, p_vector jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION public.knowledge_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer DEFAULT NULL::integer, p_offset integer DEFAULT 0, p_length integer DEFAULT 4096, p_audit boolean DEFAULT false, p_include_history boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION public.knowledge_search(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_query text, p_limit integer DEFAULT 5, p_context_budget integer DEFAULT 3000, p_model text DEFAULT NULL::text, p_query_vector jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION public.knowledge_source_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_source_id uuid, p_offset integer DEFAULT 0, p_length integer DEFAULT 4096, p_audit boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
declare r quorum_private.knowledge_sources%rowtype;
begin
 perform quorum_private.knowledge_scope(p_agent_id,p_session_ref,p_space_id,false,coalesce(p_audit,false));
 if p_audit is null or p_offset is null or p_offset<0 or p_length is null or p_length not between 1 and 4096 then raise exception 'knowledge_invalid_payload'; end if;
 select * into r from quorum_private.knowledge_sources where space_id=p_space_id and source_id=p_source_id;
 if not found or (r.withdrawn and not p_audit) then raise insufficient_privilege using message='knowledge_denied'; end if;
 return (to_jsonb(r)-'body')||jsonb_build_object('body',substring(r.body from p_offset+1 for p_length),'offset',p_offset,'next_offset',case when p_offset::bigint+p_length<length(r.body) then p_offset+p_length else null end);
end $$;

CREATE FUNCTION public.knowledge_source_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_payload jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
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
end $_$;

CREATE FUNCTION public.knowledge_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_operation text, p_payload jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_admin_before_default_role(p_agent_id uuid, p_session_ref text, p_operation text, p_payload jsonb, p_request_id text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_binding(a uuid, sid text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_cached(op text, p jsonb, req text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_fields(p jsonb, allowed text[], required text[]) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
begin
 if p is null or jsonb_typeof(p)<>'object' or octet_length(p::text)>131072
  or exists(select 1 from jsonb_object_keys(p) k where not k=any(allowed))
  or exists(select 1 from unnest(required) k where not p ? k or p->k='null'::jsonb) then
  raise exception 'knowledge_invalid_payload'; end if;
end $$;

CREATE FUNCTION quorum_private.knowledge_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
begin
 if TG_TABLE_NAME='knowledge_sources' then
  if (to_jsonb(new)-'withdrawn') is distinct from (to_jsonb(old)-'withdrawn') or (old.withdrawn and not new.withdrawn) then raise exception 'knowledge_source_immutable'; end if;
 elsif TG_TABLE_NAME='knowledge_entries' then
  if new.entry_kind<>old.entry_kind or new.id<>old.id or new.space_id<>old.space_id then raise exception 'knowledge_kind_immutable'; end if;
 else raise exception 'knowledge_revision_immutable'; end if;
 return new;
end $$;

CREATE FUNCTION quorum_private.knowledge_mutation_lock(s uuid) RETURNS void
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 select pg_advisory_xact_lock(hashtextextended('knowledge-space:'||s::text,0));
$$;

CREATE TABLE quorum_private.knowledge_revisions (
    entry_id uuid NOT NULL,
    revision integer NOT NULL,
    space_id uuid NOT NULL,
    type text NOT NULL,
    status text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    source_refs jsonb NOT NULL,
    versions jsonb NOT NULL,
    review_after timestamp with time zone,
    conflicts_with jsonb DEFAULT '[]'::jsonb NOT NULL,
    replacement_id uuid,
    author_user uuid NOT NULL,
    author_agent uuid NOT NULL,
    quorum_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    content_sha256 text NOT NULL,
    verification_user uuid,
    verification_at timestamp with time zone,
    evidence text,
    search tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ((title || ' '::text) || body))) STORED,
    source_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    related_pages uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    CONSTRAINT knowledge_revisions_body_check CHECK (((octet_length(body) >= 1) AND (octet_length(body) <= 8192))),
    CONSTRAINT knowledge_revisions_check CHECK ((((status = 'verified'::text) AND (verification_user IS NOT NULL) AND (verification_at IS NOT NULL) AND (length(evidence) > 0)) OR ((status <> 'verified'::text) AND (verification_user IS NULL) AND (verification_at IS NULL)))),
    CONSTRAINT knowledge_revisions_check1 CHECK (((cardinality(source_ids) <= 8) AND (cardinality(related_pages) <= 8))),
    CONSTRAINT knowledge_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT knowledge_revisions_revision_check CHECK ((revision > 0)),
    CONSTRAINT knowledge_revisions_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'verified'::text, 'disputed'::text, 'superseded'::text, 'withdrawn'::text]))),
    CONSTRAINT knowledge_revisions_title_check CHECK (((length(title) >= 1) AND (length(title) <= 200))),
    CONSTRAINT knowledge_revisions_type_check CHECK ((type = ANY (ARRAY['decision'::text, 'fact'::text, 'hypothesis'::text, 'known_issue'::text, 'procedure'::text])))
);

CREATE FUNCTION quorum_private.knowledge_projection(r quorum_private.knowledge_revisions) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_save(op text, p jsonb, req text, result jsonb) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
begin
 insert into quorum_private.knowledge_requests values(auth.uid(),op,req,encode(sha256(convert_to(p::text,'UTF8')),'hex'),result);
 return result;
end $$;

CREATE FUNCTION quorum_private.knowledge_scope(a uuid, sid text, s uuid, writer boolean DEFAULT false, owner_only boolean DEFAULT false) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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
 if not found then
  -- knowledge_binding proved a live agent, device and non-revoked membership of q,
  -- and q is attached for this agent's project: the default applies to that member.
  select permission into permitted from quorum_private.knowledge_member_defaults
   where space_id=s for share;
  if not found then raise insufficient_privilege using message='knowledge_denied'; end if;
 end if;
 if writer and permitted<>'writer' then
  raise insufficient_privilege using message='knowledge_denied'; end if;
 return q;
end $$;

CREATE FUNCTION quorum_private.knowledge_scope_before_default_role(a uuid, sid text, s uuid, writer boolean DEFAULT false, owner_only boolean DEFAULT false) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
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

CREATE FUNCTION quorum_private.knowledge_uuid_links(j jsonb) RETURNS uuid[]
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
declare ids uuid[];
begin
 if j is null or jsonb_typeof(j)<>'array' then raise exception 'knowledge_invalid_link'; end if;
 if jsonb_array_length(j)>8 or exists(select 1 from jsonb_array_elements(j) x where jsonb_typeof(x)<>'string') then raise exception 'knowledge_invalid_link'; end if;
 begin select coalesce(array_agg(x::uuid),'{}') into ids from jsonb_array_elements_text(j) x;
 exception when invalid_text_representation then raise exception 'knowledge_invalid_link'; end;
 if cardinality(ids)<>(select count(distinct x) from unnest(ids) x) then raise exception 'knowledge_invalid_link'; end if;
 return ids;
end $$;

CREATE FUNCTION quorum_private.knowledge_vector(v jsonb, d integer) RETURNS extensions.vector
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
declare value extensions.vector;
begin
 if v is null or jsonb_typeof(v)<>'array' then raise exception 'knowledge_invalid_vector'; end if;
 if d not between 1 and 2000 or jsonb_array_length(v)<>d or exists(select 1 from jsonb_array_elements(v) x
   where jsonb_typeof(x)<>'number' or abs((x#>>'{}')::numeric)>3.4e38) then raise exception 'knowledge_invalid_vector'; end if;
 begin value:=v::text::extensions.vector; exception when others then raise exception 'knowledge_invalid_vector'; end;
 if extensions.vector_norm(value)=0 then raise exception 'knowledge_invalid_vector'; end if;
 return value;
end $$;

CREATE FUNCTION quorum_private.knowledge_visible(s uuid, e uuid, v integer DEFAULT NULL::integer) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 select exists(select 1 from quorum_private.knowledge_entries ke join quorum_private.knowledge_revisions kr
 on kr.entry_id=ke.id and kr.revision=coalesce(v,ke.current_revision)
 where ke.space_id=s and ke.id=e and kr.status<>'withdrawn'
 and not exists(select 1 from unnest(kr.source_ids) x where not exists(
  select 1 from quorum_private.knowledge_sources ks where ks.space_id=s and ks.source_id=x and not ks.withdrawn)));
$$;

CREATE TABLE quorum_private.knowledge_attachments (
    space_id uuid NOT NULL,
    quorum_id uuid NOT NULL,
    project_ref text NOT NULL,
    CONSTRAINT knowledge_attachments_project_ref_check CHECK (((length(project_ref) >= 1) AND (length(project_ref) <= 500)))
);

CREATE TABLE quorum_private.knowledge_entries (
    id uuid NOT NULL,
    space_id uuid NOT NULL,
    current_revision integer NOT NULL,
    entry_kind text DEFAULT 'legacy'::text NOT NULL,
    CONSTRAINT knowledge_entries_current_revision_check CHECK ((current_revision > 0)),
    CONSTRAINT knowledge_entries_entry_kind_check CHECK ((entry_kind = ANY (ARRAY['legacy'::text, 'wiki_page'::text])))
);

CREATE TABLE quorum_private.knowledge_grants (
    space_id uuid NOT NULL,
    user_id uuid NOT NULL,
    permission text NOT NULL,
    CONSTRAINT knowledge_grants_permission_check CHECK ((permission = ANY (ARRAY['reader'::text, 'writer'::text])))
);

CREATE TABLE quorum_private.knowledge_member_defaults (
    space_id uuid NOT NULL,
    permission text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT knowledge_member_defaults_permission_check CHECK ((permission = ANY (ARRAY['reader'::text, 'writer'::text])))
);

CREATE TABLE quorum_private.knowledge_page_tombstones (
    entry_id uuid NOT NULL,
    space_id uuid NOT NULL
);

CREATE TABLE quorum_private.knowledge_requests (
    actor uuid NOT NULL,
    operation text NOT NULL,
    request_id text NOT NULL,
    fingerprint text NOT NULL,
    result jsonb NOT NULL,
    CONSTRAINT knowledge_requests_request_id_check CHECK (((length(request_id) >= 1) AND (length(request_id) <= 128)))
);

CREATE TABLE quorum_private.knowledge_source_tombstones (
    source_id uuid NOT NULL,
    space_id uuid NOT NULL
);

CREATE TABLE quorum_private.knowledge_sources (
    source_id uuid NOT NULL,
    space_id uuid NOT NULL,
    reference text NOT NULL,
    author text NOT NULL,
    source_date timestamp with time zone NOT NULL,
    body text NOT NULL,
    is_excerpt boolean NOT NULL,
    excerpt_scope text NOT NULL,
    content_sha256 text NOT NULL,
    capturer_user uuid NOT NULL,
    capturer_agent uuid NOT NULL,
    quorum_id uuid NOT NULL,
    captured_at timestamp with time zone DEFAULT now() NOT NULL,
    withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT knowledge_sources_author_check CHECK (((length(author) >= 1) AND (length(author) <= 200))),
    CONSTRAINT knowledge_sources_body_check CHECK (((octet_length(body) >= 1) AND (octet_length(body) <= 8192))),
    CONSTRAINT knowledge_sources_check CHECK (((length(excerpt_scope) <= 512) AND ((NOT is_excerpt) OR (length(excerpt_scope) > 0)))),
    CONSTRAINT knowledge_sources_check1 CHECK ((content_sha256 = encode(sha256(convert_to(body, 'UTF8'::name)), 'hex'::text))),
    CONSTRAINT knowledge_sources_reference_check CHECK (((length(reference) >= 1) AND (length(reference) <= 512))),
    CONSTRAINT knowledge_sources_source_date_check CHECK (isfinite(source_date))
);

CREATE TABLE quorum_private.knowledge_spaces (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    vector_model text,
    vector_dimensions integer,
    CONSTRAINT knowledge_spaces_check CHECK (((vector_model IS NULL) AND (vector_dimensions IS NULL)) OR ((length(vector_model) BETWEEN 1 AND 200) AND (vector_dimensions BETWEEN 1 AND 2000))),
    CONSTRAINT knowledge_spaces_name_check CHECK (((length(name) >= 1) AND (length(name) <= 200)))
);

CREATE TABLE quorum_private.knowledge_vectors (
    entry_id uuid NOT NULL,
    revision integer NOT NULL,
    content_sha256 text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL,
    embedding extensions.vector NOT NULL,
    indexed_by uuid NOT NULL,
    indexed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT knowledge_vectors_check CHECK ((extensions.vector_dims(embedding) = dimensions)),
    CONSTRAINT knowledge_vectors_dimensions_check CHECK (((dimensions >= 1) AND (dimensions <= 2000)))
);

ALTER TABLE ONLY quorum_private.knowledge_attachments
    ADD CONSTRAINT knowledge_attachments_pkey PRIMARY KEY (space_id, quorum_id, project_ref);

ALTER TABLE ONLY quorum_private.knowledge_entries
    ADD CONSTRAINT knowledge_entries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.knowledge_entries
    ADD CONSTRAINT knowledge_entries_space_id_id_key UNIQUE (space_id, id);

ALTER TABLE ONLY quorum_private.knowledge_grants
    ADD CONSTRAINT knowledge_grants_pkey PRIMARY KEY (space_id, user_id);

ALTER TABLE ONLY quorum_private.knowledge_member_defaults
    ADD CONSTRAINT knowledge_member_defaults_pkey PRIMARY KEY (space_id);

ALTER TABLE ONLY quorum_private.knowledge_page_tombstones
    ADD CONSTRAINT knowledge_page_tombstones_pkey PRIMARY KEY (entry_id);

ALTER TABLE ONLY quorum_private.knowledge_requests
    ADD CONSTRAINT knowledge_requests_pkey PRIMARY KEY (actor, operation, request_id);

ALTER TABLE ONLY quorum_private.knowledge_revisions
    ADD CONSTRAINT knowledge_revisions_pkey PRIMARY KEY (entry_id, revision);

ALTER TABLE ONLY quorum_private.knowledge_source_tombstones
    ADD CONSTRAINT knowledge_source_tombstones_pkey PRIMARY KEY (source_id);

ALTER TABLE ONLY quorum_private.knowledge_sources
    ADD CONSTRAINT knowledge_sources_pkey PRIMARY KEY (source_id);

ALTER TABLE ONLY quorum_private.knowledge_sources
    ADD CONSTRAINT knowledge_sources_space_id_source_id_key UNIQUE (space_id, source_id);

ALTER TABLE ONLY quorum_private.knowledge_spaces
    ADD CONSTRAINT knowledge_spaces_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quorum_private.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_pkey PRIMARY KEY (entry_id);

CREATE INDEX knowledge_entries_space ON quorum_private.knowledge_entries USING btree (space_id, id);

CREATE INDEX knowledge_revisions_search ON quorum_private.knowledge_revisions USING gin (search);

CREATE TRIGGER knowledge_kind_immutable BEFORE UPDATE ON quorum_private.knowledge_entries FOR EACH ROW EXECUTE FUNCTION quorum_private.knowledge_immutable();

CREATE TRIGGER knowledge_revision_immutable BEFORE UPDATE ON quorum_private.knowledge_revisions FOR EACH ROW EXECUTE FUNCTION quorum_private.knowledge_immutable();

CREATE TRIGGER knowledge_source_immutable BEFORE UPDATE ON quorum_private.knowledge_sources FOR EACH ROW EXECUTE FUNCTION quorum_private.knowledge_immutable();

ALTER TABLE ONLY quorum_private.knowledge_attachments
    ADD CONSTRAINT knowledge_attachments_quorum_id_fkey FOREIGN KEY (quorum_id) REFERENCES quorum_private.quorums(id);

ALTER TABLE ONLY quorum_private.knowledge_attachments
    ADD CONSTRAINT knowledge_attachments_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_entries
    ADD CONSTRAINT knowledge_entries_id_current_revision_fkey FOREIGN KEY (id, current_revision) REFERENCES quorum_private.knowledge_revisions(entry_id, revision) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY quorum_private.knowledge_entries
    ADD CONSTRAINT knowledge_entries_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id);

ALTER TABLE ONLY quorum_private.knowledge_grants
    ADD CONSTRAINT knowledge_grants_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_grants
    ADD CONSTRAINT knowledge_grants_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);

ALTER TABLE ONLY quorum_private.knowledge_member_defaults
    ADD CONSTRAINT knowledge_member_defaults_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_page_tombstones
    ADD CONSTRAINT knowledge_page_tombstones_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id);

ALTER TABLE ONLY quorum_private.knowledge_revisions
    ADD CONSTRAINT knowledge_revisions_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES quorum_private.knowledge_entries(id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_revisions
    ADD CONSTRAINT knowledge_revisions_space_id_entry_id_fkey FOREIGN KEY (space_id, entry_id) REFERENCES quorum_private.knowledge_entries(space_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_source_tombstones
    ADD CONSTRAINT knowledge_source_tombstones_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id);

ALTER TABLE ONLY quorum_private.knowledge_sources
    ADD CONSTRAINT knowledge_sources_space_id_fkey FOREIGN KEY (space_id) REFERENCES quorum_private.knowledge_spaces(id);

ALTER TABLE ONLY quorum_private.knowledge_spaces
    ADD CONSTRAINT knowledge_spaces_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES auth.users(id);

ALTER TABLE ONLY quorum_private.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES quorum_private.knowledge_entries(id) ON DELETE CASCADE;

ALTER TABLE ONLY quorum_private.knowledge_vectors
    ADD CONSTRAINT knowledge_vectors_entry_id_revision_fkey FOREIGN KEY (entry_id, revision) REFERENCES quorum_private.knowledge_revisions(entry_id, revision) ON DELETE CASCADE;

ALTER TABLE quorum_private.knowledge_attachments ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_entries ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_grants ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_member_defaults ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_page_tombstones ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_requests ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_revisions ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_source_tombstones ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_sources ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_spaces ENABLE ROW LEVEL SECURITY;

ALTER TABLE quorum_private.knowledge_vectors ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION public.knowledge_admin(p_agent_id uuid, p_session_ref text, p_operation text, p_payload jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_admin(p_agent_id uuid, p_session_ref text, p_operation text, p_payload jsonb, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_catalog(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_after_id uuid, p_limit integer, p_context_budget integer) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_catalog(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_after_id uuid, p_limit integer, p_context_budget integer) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_index(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer, p_content_sha256 text, p_model text, p_dimensions integer, p_vector jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_index(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer, p_content_sha256 text, p_model text, p_dimensions integer, p_vector jsonb, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer, p_offset integer, p_length integer, p_audit boolean, p_include_history boolean) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_entry_id uuid, p_revision integer, p_offset integer, p_length integer, p_audit boolean, p_include_history boolean) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_search(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_query text, p_limit integer, p_context_budget integer, p_model text, p_query_vector jsonb) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_search(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_query text, p_limit integer, p_context_budget integer, p_model text, p_query_vector jsonb) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_source_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_source_id uuid, p_offset integer, p_length integer, p_audit boolean) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_source_read(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_source_id uuid, p_offset integer, p_length integer, p_audit boolean) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_source_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_payload jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_source_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_payload jsonb, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION public.knowledge_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_operation text, p_payload jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;
GRANT ALL ON FUNCTION public.knowledge_write(p_agent_id uuid, p_session_ref text, p_space_id uuid, p_operation text, p_payload jsonb, p_request_id text) TO authenticated;

REVOKE ALL ON FUNCTION quorum_private.knowledge_admin_before_default_role(p_agent_id uuid, p_session_ref text, p_operation text, p_payload jsonb, p_request_id text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_binding(a uuid, sid text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_cached(op text, p jsonb, req text) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_fields(p jsonb, allowed text[], required text[]) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_immutable() FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_mutation_lock(s uuid) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_projection(r quorum_private.knowledge_revisions) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_save(op text, p jsonb, req text, result jsonb) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_scope(a uuid, sid text, s uuid, writer boolean, owner_only boolean) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_scope_before_default_role(a uuid, sid text, s uuid, writer boolean, owner_only boolean) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_uuid_links(j jsonb) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_vector(v jsonb, d integer) FROM PUBLIC, anon, authenticated, service_role;

REVOKE ALL ON FUNCTION quorum_private.knowledge_visible(s uuid, e uuid, v integer) FROM PUBLIC, anon, authenticated, service_role;

insert into quorum_private.schema_baseline(part,covers_through,installed_by) values('knowledge','20261108140000','baseline');
