"""Local bounded evaluation, public corpus only; no service or provider."""
from pathlib import Path
import json
import argparse
import hashlib
from unittest.mock import patch
import test_quorum_close_race as pg
from test_quorum_knowledge_backend import IMAGE
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/quorum-wiki-evaluation.json'
CORPUS=json.loads(FIXTURE.read_text())
SOURCES=CORPUS['sources']
A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa59'
Q='44444444-4444-4444-8444-444444444459'
OWNER='11111111-1111-4111-8111-111111111159'
RAW='90000000-0000-4000-8000-000000000001'
WIKI='90000000-0000-4000-8000-000000000002'
def quote(value):
    if value is None:return 'null'
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    return "'"+str(value).replace("'","''")+"'"
def ident(prefix,pr):return f'{prefix}0000000-0000-4000-8000-{pr:012d}'
def rpc(cluster,name,*args):
    script='begin; select set_config(\'request.jwt.claim.sub\','+quote(OWNER)+",true); select public."+name+'('+','.join(quote(x) for x in args)+'); commit;'
    return json.loads(cluster.sql('evaluation',script).strip().splitlines()[-1])
def payload(value):return json.dumps(value,ensure_ascii=False)
def evaluate():
    with patch.object(pg,'IMAGE',IMAGE),pg.Cluster() as cluster:
        cluster.sql('postgres','create database evaluation')
        sql=(ROOT/'tests/quorum_backend_fixture.sql').read_text()+(ROOT/'skills/quorum/references/server-bootstrap.sql').read_text()
        for migration in sorted((ROOT/'skills/quorum/supabase/migrations').glob('*.sql')):sql+=migration.read_text()
        cluster.sql('evaluation',sql,single=True)
        cluster.sql('evaluation',(ROOT/'tests/quorum_knowledge_backend.sql').read_text(),single=True)
        for space in (RAW,WIKI):
            rpc(cluster,'knowledge_admin',A,'owner-session','create',payload(dict(space_id=space,name='Public wiki evaluation')),'create-'+space)
            rpc(cluster,'knowledge_admin',A,'owner-session','attach',payload(dict(space_id=space,quorum_id=Q,project_ref='same')),'attach-'+space)
        source_map={x['pr']:x for x in SOURCES}
        for page in CORPUS['pages']:
            pr=page['pr'];source=source_map[pr];body=source['title']+'\n\n'+source['body']
            rpc(cluster,'knowledge_write',A,'owner-session',RAW,'propose',payload(dict(entry_id=ident('a',pr),type='fact',title=source['title'],body=body,source_refs=[source['url']])),'raw-'+str(pr))
            rpc(cluster,'knowledge_source_write',A,'owner-session',WIKI,payload(dict(source_id=ident('b',pr),reference=source['url'],author=source['author'],source_date=source['source_date'],body=body,is_excerpt=True,excerpt_scope='Pull request title and description; excludes diff and discussion.')),'source-'+str(pr))
            rpc(cluster,'knowledge_write',A,'owner-session',WIKI,'propose',payload(dict(entry_id=ident('c',pr),entry_kind='wiki_page',type='fact',title=page['title'],body=page['body'],source_ids=[ident('b',pr)],related_pages=[],source_refs=[source['url']],conflicts_with=[])),'wiki-'+str(pr))
        records=[]
        for question in CORPUS['questions']:
            row=dict(question)
            for label,space,prefix in [('raw',RAW,'a'),('wiki',WIKI,'c')]:
                result=rpc(cluster,'knowledge_search',A,'owner-session',space,question['query'],CORPUS['limit'],CORPUS['context_budget'],None,None)
                found=[x['pr'] for x in SOURCES if ident(prefix,x['pr']) in [r['entry_id'] for r in result['results']]]
                serialized=json.dumps(result,ensure_ascii=False)
                fetched=[]
                source_fetches=0
                for match in result['results']:
                    page_result=rpc(cluster,'knowledge_read',A,'owner-session',space,match['entry_id'],None,0,4096,False)
                    fetched.append(page_result)
                    for source_id in page_result.get('source_ids',[]):
                        fetched.append(rpc(cluster,'knowledge_source_read',A,'owner-session',space,source_id,0,4096,False))
                        source_fetches+=1
                fetched_text=[json.dumps(item,ensure_ascii=False) for item in fetched]
                row[label]=dict(found_prs=found,expected_found=all(pr in found for pr in question['expected_prs']),unexpected_prs=[pr for pr in found if pr not in question['expected_prs']],serialized_chars=len(serialized),utf8_bytes=len(serialized.encode()),truncated=result['truncated'],ranking=result['ranking'],page_fetches=len(result['results']),source_fetches=source_fetches,total_chars=len(serialized)+sum(map(len,fetched_text)),total_utf8_bytes=len(serialized.encode())+sum(len(item.encode()) for item in fetched_text))
            records.append(row)
    if not cluster.removed:raise RuntimeError('disposable cluster cleanup failed')
    return dict(corpus_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),image=IMAGE,questions=records)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-docker',action='store_true',required=True,help='Use the pinned local image with no network or published ports')
    args=parser.parse_args()
    print(json.dumps(evaluate(),indent=2))
