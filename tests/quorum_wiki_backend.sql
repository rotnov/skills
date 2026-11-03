select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111159',false);
select public.knowledge_source_write('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59','owner-session','55555555-5555-4555-8555-555555555559',
 '{"source_id":"10000000-0000-4000-8000-000000000059","reference":"issue:59","author":"Synthetic evidence author","source_date":"2026-01-01T00:00:00Z","body":"Original wiki evidence","is_excerpt":true,"excerpt_scope":"paragraph 1"}', 'wiki-source');
create function pg_temp.wiki_fail(statement text, expected text default null) returns void language plpgsql as $$
begin
 begin execute statement;
 exception when others then
  if expected is not null and sqlerrm<>expected then raise; end if;
  return;
 end;
 raise exception 'expected_wiki_failure: %',statement;
end $$;
do $test$
declare
 a uuid:='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59'; u uuid:='11111111-1111-4111-8111-111111111159';
 b uuid:='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb59'; v uuid:='22222222-2222-4222-8222-222222222259';
 q uuid:='44444444-4444-4444-8444-444444444459';
 s uuid:='55555555-5555-4555-8555-555555555559';
 src uuid:='10000000-0000-4000-8000-000000000059'; src2 uuid:='20000000-0000-4000-8000-000000000059';
 e uuid:='30000000-0000-4000-8000-000000000059'; rel uuid:='40000000-0000-4000-8000-000000000059';
 other uuid; other_src uuid:=gen_random_uuid();
 p jsonb; source_p jsonb; result jsonb; hash text; original_hash text; cat jsonb; cursor_id uuid; seen uuid[]:='{}'; boundary text; statement text;
begin
 source_p:='{"source_id":"10000000-0000-4000-8000-000000000059","reference":"issue:59","author":"Synthetic evidence author","source_date":"2026-01-01T00:00:00Z","body":"Original wiki evidence","is_excerpt":true,"excerpt_scope":"paragraph 1"}';
 result:=public.knowledge_source_read(a,'owner-session',s,src);
 hash:=encode(sha256(convert_to('Original wiki evidence','UTF8')),'hex');
 if result->>'content_sha256'<>hash or result->>'excerpt_scope'<>'paragraph 1' or result->>'capturer_user'<>u::text or result->>'source_date'=result->>'captured_at' then raise exception 'source_provenance'; end if;
 if public.knowledge_source_write(a,'owner-session',s,source_p,'wiki-source')->>'content_sha256'<>hash then raise exception 'source_replay'; end if;
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p||'{"author":"changed"}','change-author'),'knowledge_source_immutable');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p||'{"body":"changed"}','wiki-source'),'knowledge_idempotency_conflict');
 perform pg_temp.wiki_fail(format('update quorum_private.knowledge_sources set author=%L where source_id=%L','changed',src),'knowledge_source_immutable');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p||jsonb_build_object('source_id',gen_random_uuid(),'body',repeat('界',2800)),'unicode-oversize'));
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p||jsonb_build_object('source_id',gen_random_uuid(),'is_excerpt','true'),'wrong-boolean'),'knowledge_invalid_payload');
 perform public.knowledge_source_write(a,'owner-session',s,source_p||jsonb_build_object('source_id',src2,'body','Replacement retained evidence'),'wiki-source-2');
 other:=(public.knowledge_admin(a,'owner-session','create','{"name":"other wiki scope"}','wiki-other')->>'space_id')::uuid;
 perform public.knowledge_admin(a,'owner-session','attach',jsonb_build_object('space_id',other,'quorum_id',q,'project_ref','same'),'wiki-other-attach');
 perform public.knowledge_source_write(a,'owner-session',other,source_p||jsonb_build_object('source_id',other_src),'wiki-other-source');
 perform public.knowledge_write(a,'owner-session',s,'propose',jsonb_build_object('entry_id',rel,'type','fact','title','Related wiki page','body','Navigation target'),'wiki-related');
 p:=jsonb_build_object('entry_id',e,'entry_kind','wiki_page','type','fact','title','WIKI_NEEDLE','body','Synthesized wiki answer','source_ids',jsonb_build_array(src),'related_pages',jsonb_build_array(rel),'source_refs','[]'::jsonb,'conflicts_with',jsonb_build_array(rel));
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'propose',p-'source_ids','wiki-no-source'),'knowledge_wiki_evidence_required');
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'propose',p||jsonb_build_object('source_ids',jsonb_build_array(other_src)),'wiki-cross-source'),'knowledge_invalid_link');
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'propose',p||jsonb_build_object('source_ids',jsonb_build_array(src,src)),'wiki-dup-source'),'knowledge_invalid_link');
 result:=public.knowledge_write(a,'owner-session',s,'propose',p,'wiki-page'); original_hash:=result->>'content_sha256';
 result:=public.knowledge_read(a,'owner-session',s,e);
 if result->>'entry_kind'<>'wiki_page' or result->'source_ids'<>jsonb_build_array(src) or result#>>'{sources,0,author}'<>'Synthetic evidence author' then raise exception 'wiki_projection'; end if;
 if public.knowledge_search(a,'owner-session',s,'WIKI_NEEDLE',10,6000)::text like '%Original wiki evidence%' then raise exception 'full_source_in_search'; end if;
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'revise',(p-'entry_kind'-'source_ids')||'{"expected_revision":1}','wiki-erase-source'),'knowledge_wiki_evidence_required');
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'revise',p||'{"expected_revision":1,"entry_kind":"legacy"}','wiki-change-kind'),'knowledge_kind_immutable');
 perform pg_temp.wiki_fail(format('update quorum_private.knowledge_entries set entry_kind=%L where id=%L','legacy',e),'knowledge_kind_immutable');
 perform pg_temp.wiki_fail(format('update quorum_private.knowledge_revisions set source_ids=%L where entry_id=%L','{}',e),'knowledge_revision_immutable');
 -- Evidence is retained through all lifecycle revisions; complete revision data
 -- changes the content hash even when title/body remain identical.
 result:=public.knowledge_write(a,'owner-session',s,'verify',jsonb_build_object('entry_id',e,'expected_revision',1,'owner_assertion',true,'evidence','synthetic owner assertion'),'wiki-verify');
 if result->>'content_sha256'<>original_hash then raise exception 'lifecycle_hash_changed'; end if;
 result:=public.knowledge_write(a,'owner-session',s,'revise',(p-'entry_kind')||jsonb_build_object('expected_revision',2,'source_ids',jsonb_build_array(src2)),'wiki-revise');
 if result->>'content_sha256'=original_hash or result->>'status'<>'proposed' then raise exception 'evidence_hash_not_included'; end if;
 if public.knowledge_read(a,'owner-session',s,e,1,0,4096,true)->'source_ids'<>jsonb_build_array(src) then raise exception 'revision_history_lost'; end if;
 perform public.knowledge_write(a,'owner-session',s,'dispute',jsonb_build_object('entry_id',e,'expected_revision',3,'evidence','synthetic conflict','conflicts_with',jsonb_build_array(rel)),'wiki-dispute');
 if public.knowledge_read(a,'owner-session',s,e)->'source_ids'<>jsonb_build_array(src2) then raise exception 'dispute_lost_sources'; end if;
 perform public.knowledge_write(a,'owner-session',s,'supersede',jsonb_build_object('entry_id',e,'expected_revision',4,'replacement_id',rel),'wiki-supersede');
 perform public.knowledge_admin(a,'owner-session','grant',jsonb_build_object('space_id',s,'user_id',v,'permission','reader'),'wiki-reader');
 perform set_config('request.jwt.claim.sub',v::text,true);
 result:=public.knowledge_read(b,'member-session',s,e,null,0,4096,false,true);
 if result->>'status'<>'superseded' or result->>'replacement_id'<>rel::text or result->'source_ids'<>jsonb_build_array(src2) then raise exception 'superseded_navigation'; end if;
 perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L,null,0,4096,false,false)',b,'member-session',s,e),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L,1,0,4096,false,true)',b,'member-session',s,e),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L,1,0,4096,true,true)',b,'member-session',s,e),'knowledge_denied');
 perform set_config('request.jwt.claim.sub',u::text,true);
 -- A second active page enables withdrawal -> suppression -> explicit recovery.
 p:=p||jsonb_build_object('entry_id','50000000-0000-4000-8000-000000000059');
 perform public.knowledge_write(a,'owner-session',s,'propose',p,'wiki-recovery');
 perform public.knowledge_index(a,'owner-session',s,(p->>'entry_id')::uuid,1,public.knowledge_read(a,'owner-session',s,(p->>'entry_id')::uuid)->>'content_sha256','synthetic-v1',3,'[1,0,0]','wiki-index');
 perform public.knowledge_admin(a,'owner-session','withdraw_source',jsonb_build_object('space_id',s,'source_id',src,'expected_content_sha256',hash),'wiki-withdraw-source');
 if exists(select 1 from quorum_private.knowledge_vectors where entry_id=(p->>'entry_id')::uuid) then raise exception 'source_vectors_survived'; end if;
 perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L)',a,'owner-session',s,p->>'entry_id'),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_read(%L,%L,%L,%L)',a,'owner-session',s,src),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p,'wiki-source'),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',a,'owner-session',s,'propose',p,'wiki-recovery'),'knowledge_denied');
 if public.knowledge_search(a,'owner-session',s,'WIKI_NEEDLE',10,6000)::text like '%50000000-0000-4000-8000-000000000059%' or public.knowledge_catalog(a,'owner-session',s,null,10,6000)::text like '%50000000-0000-4000-8000-000000000059%' then raise exception 'dependent_page_leak'; end if;
 if public.knowledge_source_read(a,'owner-session',s,src,0,4096,true)->>'body'<>'Original wiki evidence' then raise exception 'source_audit_lost'; end if;
 perform public.knowledge_write(a,'owner-session',s,'revise',(p-'entry_kind')||jsonb_build_object('expected_revision',1,'source_ids',jsonb_build_array(src2)),'wiki-recovered');
 perform public.knowledge_read(a,'owner-session',s,(p->>'entry_id')::uuid);
 perform public.knowledge_admin(a,'owner-session','purge_source',jsonb_build_object('space_id',s,'source_id',src,'expected_content_sha256',hash),'wiki-purge-source');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_read(%L,%L,%L,%L,0,4096,true)',a,'owner-session',s,src),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',a,'owner-session',s,source_p,'wiki-reuse-purged'),'knowledge_source_immutable');
 -- Withdrawing/purging a linked page removes its UUID from every projection,
 -- including owner audit of an old page revision.
 perform public.knowledge_write(a,'owner-session',s,'withdraw',jsonb_build_object('entry_id',rel,'expected_revision',1),'wiki-withdraw-related');
 result:=public.knowledge_read(a,'owner-session',s,e,null,0,4096,false,true);
 if result->'related_pages'<>'[]' or result->'conflicts_with'<>'[]' or result->>'replacement_id' is not null then raise exception 'hidden_relation_leak'; end if;
 perform public.knowledge_admin(a,'owner-session','purge',jsonb_build_object('space_id',s,'entry_id',rel,'expected_revision',2),'wiki-purge-related');
 result:=public.knowledge_read(a,'owner-session',s,e,1,0,4096,true);
 if result::text like '%'||rel::text||'%' or result::text like '%'||src::text||'%' then raise exception 'audit_hidden_relation_leak'; end if;
 -- Cursor has stable keyset ordering and a budget failure is explicit.
 loop
  cat:=public.knowledge_catalog(a,'owner-session',s,cursor_id,1,6000);
  if jsonb_array_length(cat->'results')>0 then
   if (cat#>>'{results,0,entry_id}')::uuid=any(seen) then raise exception 'catalog_duplicate'; end if;
   seen:=array_append(seen,(cat#>>'{results,0,entry_id}')::uuid);
  end if;
  exit when not (cat->>'truncated')::boolean;
  cursor_id:=(cat->>'next_cursor')::uuid;
 end loop;
 cat:=public.knowledge_catalog(a,'owner-session',s,null,10,512);
 if length(cat::text)>512 then raise exception 'catalog_budget'; end if;
 -- All new reads require the current scope, including previously valid receipts.
 perform public.knowledge_admin(a,'owner-session','revoke',jsonb_build_object('space_id',s,'user_id',v),'wiki-revoke');
 perform set_config('request.jwt.claim.sub',v::text,true);
 perform pg_temp.wiki_fail(format('select public.knowledge_catalog(%L,%L,%L)',b,'member-session',s),'knowledge_denied');
 perform pg_temp.wiki_fail(format('select public.knowledge_source_read(%L,%L,%L,%L)',b,'member-session',s,src2),'knowledge_denied');
 perform set_config('request.jwt.claim.sub',u::text,true);
 -- Exercise all current-binding gates on each projection, after cached source
 -- success. Direct table changes are fixture-only, representing independent
 -- revocation administration without requiring a new native session.
 perform public.knowledge_admin(a,'owner-session','grant',jsonb_build_object('space_id',s,'user_id',v,'permission','writer'),'wiki-writer-gates');
 perform set_config('request.jwt.claim.sub',v::text,true);
 source_p:=source_p||jsonb_build_object('source_id',gen_random_uuid());
 perform public.knowledge_source_write(b,'member-session',s,source_p,'wiki-member-receipt');
 foreach boundary in array array['grant','detach','device','membership','agent'] loop
  if boundary='grant' then delete from quorum_private.knowledge_grants where space_id=s and user_id=v;
  elsif boundary='detach' then delete from quorum_private.knowledge_attachments where space_id=s and quorum_id=q;
  elsif boundary='device' then update quorum_private.devices set revoked=true where id=b;
  elsif boundary='membership' then update quorum_private.memberships set revoked=true where quorum_id=q and user_id=v;
  else update quorum_private.agents set revoked=true where id=b; end if;
  foreach statement in array array[
   format('select public.knowledge_catalog(%L,%L,%L)',b,'member-session',s),
   format('select public.knowledge_search(%L,%L,%L,%L)',b,'member-session',s,'WIKI_NEEDLE'),
   format('select public.knowledge_read(%L,%L,%L,%L)',b,'member-session',s,p->>'entry_id'),
   format('select public.knowledge_source_read(%L,%L,%L,%L)',b,'member-session',s,src2),
   format('select public.knowledge_source_write(%L,%L,%L,%L,%L)',b,'member-session',s,source_p,'wiki-member-receipt')
  ] loop perform pg_temp.wiki_fail(statement); end loop;
  if boundary='grant' then insert into quorum_private.knowledge_grants values(s,v,'writer');
  elsif boundary='detach' then insert into quorum_private.knowledge_attachments values(s,q,'same');
  elsif boundary='device' then update quorum_private.devices set revoked=false where id=b;
  elsif boundary='membership' then update quorum_private.memberships set revoked=false where quorum_id=q and user_id=v;
  else update quorum_private.agents set revoked=false where id=b; end if;
 end loop;
 perform set_config('request.jwt.claim.sub',u::text,true);
 perform public.knowledge_write(a,'owner-session',s,'propose',jsonb_build_object('entry_id','00000000-0000-4000-8000-000000000001','type','fact','title',repeat('x',200),'body','budget fixture'),'wiki-catalog-budget');
 cat:=public.knowledge_catalog(a,'owner-session',s,null,10,512);
 if length(cat::text)>512 or cat->'results'<>'[]' or not (cat->>'truncated')::boolean or not (cat->>'budget_exhausted')::boolean or cat->>'next_cursor' is not null then raise exception 'catalog_first_item_budget'; end if;
 cat:=public.knowledge_catalog(a,'owner-session',s,'ffffffff-ffff-ffff-ffff-ffffffffffff',10,512);
 if cat->'results'<>'[]' or (cat->>'truncated')::boolean then raise exception 'catalog_exhaustion'; end if;
 if has_function_privilege('anon','public.knowledge_source_write(uuid,text,uuid,jsonb,text)','EXECUTE') or has_table_privilege('authenticated','quorum_private.knowledge_sources','SELECT') then raise exception 'wiki_role_leak'; end if;
end $test$;
-- Independent-review regressions: receipt revision identity and bounded fan-in.
do $review$
declare
 a uuid:='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59'; u uuid:='11111111-1111-4111-8111-111111111159';
 b uuid:='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb59'; v uuid:='22222222-2222-4222-8222-222222222259';
 s uuid:='55555555-5555-4555-8555-555555555559';
 source_a uuid:=gen_random_uuid(); source_b uuid:=gen_random_uuid(); source_c uuid:=gen_random_uuid();
 entry uuid:=gen_random_uuid(); child uuid; body jsonb; evidence jsonb; result jsonb; receipt jsonb;
 hash_a text; hash_c text; failures text[]:='{}'; ids jsonb:='[]'; expected jsonb:='[]'; i integer;
begin
 perform set_config('request.jwt.claim.sub',v::text,true);
 evidence:=jsonb_build_object('source_id',source_a,'reference','review:source','author','Synthetic review','source_date','2026-01-01T00:00:00Z','body','Review source A','is_excerpt',false,'excerpt_scope','');
 hash_a:=public.knowledge_source_write(b,'member-session',s,evidence,'review-source-a')->>'content_sha256';
 perform public.knowledge_source_write(b,'member-session',s,evidence||jsonb_build_object('source_id',source_b,'body','Review source B'),'review-source-b');
 hash_c:=public.knowledge_source_write(b,'member-session',s,evidence||jsonb_build_object('source_id',source_c,'body','Review source C'),'review-source-c')->>'content_sha256';
 body:=jsonb_build_object('entry_id',entry,'entry_kind','wiki_page','type','fact','title','REVIEW_FANIN_TARGET','body','Review page','source_ids',jsonb_build_array(source_a),'related_pages','[]'::jsonb,'source_refs','[]'::jsonb,'conflicts_with','[]'::jsonb);
 receipt:=public.knowledge_write(b,'member-session',s,'propose',body,'review-propose');
 perform set_config('request.jwt.claim.sub',u::text,true);
 perform public.knowledge_admin(a,'owner-session','withdraw_source',jsonb_build_object('space_id',s,'source_id',source_a,'expected_content_sha256',hash_a),'review-withdraw-a');
 perform set_config('request.jwt.claim.sub',v::text,true);
 perform public.knowledge_write(b,'member-session',s,'revise',(body-'entry_kind')||jsonb_build_object('expected_revision',1,'source_ids',jsonb_build_array(source_b)),'review-recover');
 if not quorum_private.knowledge_visible(s,entry) or quorum_private.knowledge_visible(s,entry,1) then raise exception 'review_repro_invalid'; end if;
 begin
  perform public.knowledge_write(b,'member-session',s,'propose',body,'review-propose');
  failures:=array_append(failures,'hidden_revision_receipt_after_recovery');
 exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claim.sub',u::text,true);
 perform public.knowledge_admin(a,'owner-session','purge_source',jsonb_build_object('space_id',s,'source_id',source_a,'expected_content_sha256',hash_a),'review-purge-a');
 perform set_config('request.jwt.claim.sub',v::text,true);
 begin
  perform public.knowledge_write(b,'member-session',s,'propose',body,'review-propose');
  failures:=array_append(failures,'purged_revision_receipt_after_recovery');
 exception when insufficient_privilege then null; end;
 -- Build deterministic fan-in. The two lowest IDs have independently
 -- withdrawable evidence, so filtering must happen before limit/truncation.
 for i in 1..10 loop
  child:=('90000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid;
  perform public.knowledge_write(b,'member-session',s,'propose',body||jsonb_build_object('entry_id',child,'title','Review child','source_ids',jsonb_build_array(case when i<=2 then source_c else source_b end)),'review-child-'||i);
  perform public.knowledge_write(b,'member-session',s,'supersede',jsonb_build_object('entry_id',child,'expected_revision',1,'replacement_id',entry),'review-supersede-'||i);
  if i<=8 then ids:=ids||to_jsonb(child); end if;
  if i>=3 then expected:=expected||to_jsonb(child); end if;
 end loop;
 result:=public.knowledge_read(b,'member-session',s,entry,null,0,1);
 if result->'supersedes' is distinct from ids or result->>'supersedes_truncated' is distinct from 'true' then failures:=array_append(failures,'unbounded_inverse_projection'); end if;
 result:=public.knowledge_search(b,'member-session',s,'REVIEW_FANIN_TARGET',10,6000);
 if result#>'{results,0,supersedes}' is distinct from ids or result#>>'{results,0,supersedes_truncated}' is distinct from 'true' then failures:=array_append(failures,'unbounded_search_inverse_projection'); end if;
 result:=public.knowledge_catalog(b,'member-session',s,null,10,6000);
 if result::text like '%supersedes%' or length(result::text)>6000 then raise exception 'catalog_graph_or_budget'; end if;
 perform set_config('request.jwt.claim.sub',u::text,true);
 perform public.knowledge_admin(a,'owner-session','withdraw_source',jsonb_build_object('space_id',s,'source_id',source_c,'expected_content_sha256',hash_c),'review-withdraw-c');
 perform set_config('request.jwt.claim.sub',v::text,true);
 result:=public.knowledge_read(b,'member-session',s,entry,null,0,1);
 if result->'supersedes' is distinct from expected or result->>'supersedes_truncated' is distinct from 'false' then failures:=array_append(failures,'inverse_visibility_before_limit'); end if;
 -- A visible historical receipt remains replayable while both its revision
 -- and current page are visible; withdrawing the page still blocks it.
 perform public.knowledge_write(b,'member-session',s,'revise',(body-'entry_kind')||jsonb_build_object('expected_revision',2,'source_ids',jsonb_build_array(source_b)),'review-third');
 perform public.knowledge_write(b,'member-session',s,'revise',(body-'entry_kind')||jsonb_build_object('expected_revision',1,'source_ids',jsonb_build_array(source_b)),'review-recover');
 perform public.knowledge_write(b,'member-session',s,'withdraw',jsonb_build_object('entry_id',entry,'expected_revision',3),'review-withdraw-page');
 perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',b,'member-session',s,'revise',(body-'entry_kind')||jsonb_build_object('expected_revision',1,'source_ids',jsonb_build_array(source_b)),'review-recover'),'knowledge_denied');
 if cardinality(failures)>0 then raise exception 'review_regressions: %',array_to_string(failures,', '); end if;
end $review$;
-- Permanent page identity: deletion must never rebind historical graph edges,
-- including direct calls to the legacy write RPC by a non-owner writer.
do $page_identity$
declare
 a uuid:='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59'; u uuid:='11111111-1111-4111-8111-111111111159';
 b uuid:='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb59'; v uuid:='22222222-2222-4222-8222-222222222259';
 s uuid:='55555555-5555-4555-8555-555555555559';
 src uuid:='20000000-0000-4000-8000-000000000059';
 target uuid; referring uuid; predecessor uuid; fresh uuid; original_kind text; new_kind text; role_name text;
 target_body jsonb; referring_body jsonb; predecessor_body jsonb; result jsonb; saved_receipts jsonb;
begin
 perform set_config('request.jwt.claim.sub',u::text,true);
 perform public.knowledge_admin(a,'owner-session','grant',jsonb_build_object('space_id',s,'user_id',v,'permission','writer'),'page-identity-writer');
 foreach original_kind in array array['wiki_page','legacy'] loop
  target:=gen_random_uuid(); referring:=gen_random_uuid(); predecessor:=gen_random_uuid(); fresh:=gen_random_uuid();
  target_body:=jsonb_build_object('entry_id',target,'type','fact','title','PAGE_IDENTITY_TARGET','body','Erased original page');
  if original_kind='wiki_page' then target_body:=target_body||jsonb_build_object('entry_kind','wiki_page','source_ids',jsonb_build_array(src)); end if;
  perform set_config('request.jwt.claim.sub',v::text,true);
  perform public.knowledge_write(b,'member-session',s,'propose',target_body,'identity-target-'||original_kind);
  referring_body:=jsonb_build_object('entry_id',referring,'entry_kind','wiki_page','type','fact','title','PAGE_IDENTITY_REFERRER','body','Referring page',
   'source_ids',jsonb_build_array(src),'related_pages',jsonb_build_array(target),'conflicts_with',jsonb_build_array(target));
  perform public.knowledge_write(b,'member-session',s,'propose',referring_body,'identity-referrer-'||original_kind);
  predecessor_body:=referring_body||jsonb_build_object('entry_id',predecessor,'title','PAGE_IDENTITY_PREDECESSOR');
  perform public.knowledge_write(b,'member-session',s,'propose',predecessor_body,'identity-predecessor-'||original_kind);
  perform public.knowledge_write(b,'member-session',s,'supersede',jsonb_build_object('entry_id',predecessor,'expected_revision',1,'replacement_id',target),'identity-supersede-'||original_kind);
  result:=public.knowledge_read(b,'member-session',s,target);
  if result->'supersedes'<>jsonb_build_array(predecessor) then raise exception 'identity_repro_invalid'; end if;
  select jsonb_agg(to_jsonb(kr) order by request_id) into saved_receipts from quorum_private.knowledge_requests kr
   where actor=v and request_id in ('identity-target-'||original_kind,'identity-referrer-'||original_kind,'identity-predecessor-'||original_kind,'identity-supersede-'||original_kind);
  perform set_config('request.jwt.claim.sub',u::text,true);
  perform public.knowledge_admin(a,'owner-session','purge',jsonb_build_object('space_id',s,'entry_id',target,'expected_revision',1),'identity-purge-'||original_kind);
  perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L,null,0,4096,true)',a,'owner-session',s,target),'knowledge_denied');
  perform set_config('request.jwt.claim.sub',v::text,true);
  -- This first denial is the regression RED on the vulnerable baseline.
  foreach new_kind in array array['legacy','wiki_page'] loop
   result:=jsonb_build_object('entry_id',target,'type','fact','title','PAGE_IDENTITY_TARGET','body','Unrelated replacement content');
   if new_kind='wiki_page' then result:=result||jsonb_build_object('entry_kind','wiki_page','source_ids',jsonb_build_array(src)); end if;
   perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',b,'member-session',s,'propose',result,'identity-reuse-'||original_kind||'-'||new_kind),'knowledge_page_immutable');
  end loop;
  perform pg_temp.wiki_fail(format('select public.knowledge_write(%L,%L,%L,%L,%L,%L)',b,'member-session',s,'propose',target_body,'identity-target-'||original_kind),'knowledge_denied');
  perform pg_temp.wiki_fail(format('select public.knowledge_read(%L,%L,%L,%L)',b,'member-session',s,target),'knowledge_denied');
  -- An unaffected referring receipt stays valid; rereading its unchanged
  -- revision must keep all hidden targets out of related/conflict/replacement.
  perform public.knowledge_write(b,'member-session',s,'propose',referring_body,'identity-referrer-'||original_kind);
  result:=public.knowledge_read(b,'member-session',s,referring);
  if result->>'revision'<>'1' or result->'related_pages'<>'[]' or result->'conflicts_with'<>'[]' then raise exception 'identity_graph_rebound'; end if;
  result:=public.knowledge_read(b,'member-session',s,predecessor,null,0,4096,false,true);
  if result->>'revision'<>'2' or result->'related_pages'<>'[]' or result->'conflicts_with'<>'[]' or result->>'replacement_id' is not null then raise exception 'identity_replacement_rebound'; end if;
  if public.knowledge_search(b,'member-session',s,'PAGE_IDENTITY',10,6000)::text like '%'||target::text||'%'
   or public.knowledge_catalog(b,'member-session',s,null,10,6000)::text like '%'||target::text||'%' then raise exception 'identity_hidden_projection'; end if;
  if saved_receipts is distinct from (select jsonb_agg(to_jsonb(kr) order by request_id) from quorum_private.knowledge_requests kr
   where actor=v and request_id in ('identity-target-'||original_kind,'identity-referrer-'||original_kind,'identity-predecessor-'||original_kind,'identity-supersede-'||original_kind)) then raise exception 'identity_receipts_changed'; end if;
  if exists(select 1 from quorum_private.knowledge_entries where id=target)
   or exists(select 1 from quorum_private.knowledge_revisions where entry_id=target)
   or exists(select 1 from quorum_private.knowledge_vectors where entry_id=target) then raise exception 'identity_purge_content_retained'; end if;
  -- A distinct UUID with the original content remains legitimate.
  perform public.knowledge_write(b,'member-session',s,'propose',target_body||jsonb_build_object('entry_id',fresh),'identity-distinct-'||original_kind);
  perform public.knowledge_read(b,'member-session',s,fresh);
 end loop;
 -- Tombstones contain identity only, with RLS and no client table privileges.
 if (select array_agg(column_name::text order by ordinal_position) from information_schema.columns
  where table_schema='quorum_private' and table_name='knowledge_page_tombstones') is distinct from array['entry_id','space_id'] then raise exception 'identity_tombstone_content'; end if;
 if not (select relrowsecurity from pg_class where oid='quorum_private.knowledge_page_tombstones'::regclass) then raise exception 'identity_tombstone_rls'; end if;
 foreach role_name in array array['anon','authenticated','service_role'] loop
  if has_table_privilege(role_name,'quorum_private.knowledge_page_tombstones','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then raise exception 'identity_tombstone_grant'; end if;
 end loop;
end $page_identity$;
select 'wiki_contract_green';
