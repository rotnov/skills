"""Explicit project knowledge commands. No embedding generation or background capture."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid

from quorum_api import QuorumError
from quorum_state import LocalLedger


class KnowledgeError(ValueError):
    """Fixed diagnostics, never server/private text."""
    def __init__(self, code='knowledge_invalid_payload'):
        self.code=code
        super().__init__(code)


BINDING=('agent_id','session_ref','quorum_id','project_ref','cwd','engine')
STABLE_BINDING=BINDING+('device_id','role','quorum','registration','transport','worker_binding')
ENTRY_FIELDS={'entry_id','type','title','body','source_refs','versions','review_after','conflicts_with',
              'source_ids','related_pages'}
PROPOSE_FIELDS=ENTRY_FIELDS|{'entry_kind'}
SOURCE_FIELDS={'source_id','reference','author','source_date','body','is_excerpt','excerpt_scope'}
MUTATIONS={'admin','write','index','source-write'}
WRITE_FIELDS={
    'propose':PROPOSE_FIELDS,
    'revise':ENTRY_FIELDS|{'expected_revision'},
    'verify':{'entry_id','expected_revision','evidence','owner_assertion'},
    'dispute':{'entry_id','expected_revision','evidence','conflicts_with'},
    'supersede':{'entry_id','expected_revision','replacement_id'},
    'withdraw':{'entry_id','expected_revision'},
}
ADMIN_FIELDS={
    'create':{'space_id','name'}, 'grant':{'space_id','user_id','permission'},
    'revoke':{'space_id','user_id'},'attach':{'space_id','quorum_id','project_ref'},
    'detach':{'space_id','quorum_id','project_ref'},
    'purge':{'space_id','entry_id','expected_revision'},
    'withdraw_source':{'space_id','source_id','expected_content_sha256'},
    'purge_source':{'space_id','source_id','expected_content_sha256'},
    'configure_vector':{'space_id','model','dimensions'},
}
FIELDS={
    'admin':{'operation','payload'},'write':{'space_id','operation','payload'},
    'search':{'space_id','query','limit','context_budget','model','query_vector'},
    'read':{'space_id','entry_id','revision','offset','length','audit','include_history'},
    'source-write':{'space_id','payload'},
    'source-read':{'space_id','source_id','offset','length','audit'},
    'catalog':{'space_id','after_id','limit','context_budget'},
    'index':{'space_id','entry_id','revision','content_sha256','model','dimensions','vector'},
}


def fields(value, allowed, required=()):
    if (not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value)
            or any(value[key] is None for key in required)):
        raise KnowledgeError()


def bounded_structure(value):
    """Reject excessive nesting before JSON serialization, without recursive walking."""
    pending=[(value,0)]
    visited=0
    while pending:
        item,depth=pending.pop(); visited+=1
        if depth>16 or visited>8192: raise KnowledgeError()
        children=item.values() if isinstance(item,dict) else item if isinstance(item,(list,tuple)) else ()
        pending.extend((child,depth+1) for child in children)


def identifier(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value: raise ValueError()
    except (ValueError,TypeError,AttributeError): raise KnowledgeError() from None


def vector(value, dimensions=None):
    if (not isinstance(value,list) or not 1<=len(value)<=2000 or
            dimensions is not None and (type(dimensions) is not int or len(value)!=dimensions) or
            any(type(x) not in (float,int) or abs(x)>3.4e38 or not math.isfinite(x) for x in value)
            or not any(x!=0 for x in value)):
        raise KnowledgeError('knowledge_invalid_vector')


def source_payload(value):
    fields(value,SOURCE_FIELDS,SOURCE_FIELDS)
    identifier(value['source_id'])
    for key,minimum,maximum in (('reference',1,512),('author',1,200),
                                ('body',1,8192),('excerpt_scope',0,512)):
        text=value[key]
        if not isinstance(text,str): raise KnowledgeError()
        try: size=len(text.encode('utf-8')) if key=='body' else len(text)
        except UnicodeError: raise KnowledgeError() from None
        if not minimum<=size<=maximum: raise KnowledgeError()
    if type(value['is_excerpt']) is not bool or value['is_excerpt'] and not value['excerpt_scope']:
        raise KnowledgeError()
    date=value['source_date']
    if (not isinstance(date,str) or len(date)>128 or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})',date)):
        raise KnowledgeError()
    # Python normalizes overflowing offset minutes. PostgreSQL accepts numeric
    # displacement hours through 15; check components before either parser.
    if date[-1] not in 'Zz' and (int(date[-5:-3])>15 or int(date[-2:])>59):
        raise KnowledgeError()
    try: datetime.fromisoformat(date.upper())
    except ValueError: raise KnowledgeError() from None


def wiki_fields(payload):
    if 'entry_kind' in payload and payload['entry_kind'] not in ('legacy','wiki_page'):
        raise KnowledgeError()
    for key in ('source_ids','related_pages'):
        if key not in payload: continue
        values=payload[key]
        if not isinstance(values,list) or len(values)>8: raise KnowledgeError()
        for value in values: identifier(value)
        if len(set(values))!=len(values): raise KnowledgeError()
    if payload.get('entry_kind')=='wiki_page' and not payload.get('source_ids'):
        raise KnowledgeError()


def bounded_options(data):
    for key in ('audit','include_history'):
        if key in data and type(data[key]) is not bool: raise KnowledgeError()
    for key,minimum,maximum in (('offset',0,2147483647),('length',1,4096),
                                ('limit',1,10),('context_budget',512,6000)):
        if key in data and (type(data[key]) is not int or not minimum<=data[key]<=maximum):
            raise KnowledgeError()


def validate(action,data,*,request_id=None,owner_action=False):
    if action not in FIELDS: raise KnowledgeError('knowledge_invalid_operation')
    fields(data,FIELDS[action], {'operation','payload'} if action=='admin' else
           {'space_id','operation','payload'} if action=='write' else
           {'space_id','query'} if action=='search' else
           {'space_id','entry_id'} if action=='read' else
           {'space_id','payload'} if action=='source-write' else
           {'space_id','source_id'} if action=='source-read' else
           {'space_id'} if action=='catalog' else FIELDS['index'])
    for key in ('space_id','entry_id','source_id'):
        if key in data: identifier(data[key])
    if action=='source-write': source_payload(data['payload'])
    if action in ('read','source-read','catalog'): bounded_options(data)
    if action=='catalog' and data.get('after_id') is not None: identifier(data['after_id'])
    if action in ('admin','write'):
        op=data['operation']
        if not isinstance(op,str): raise KnowledgeError()
        allowed=(ADMIN_FIELDS if action=='admin' else WRITE_FIELDS).get(op)
        if allowed is None: raise KnowledgeError('knowledge_invalid_operation')
        fields(data['payload'],allowed)
        if action=='write' and op in ('propose','revise'): wiki_fields(data['payload'])
        if (action=='admin' or op=='verify') and owner_action is not True:
            raise KnowledgeError('knowledge_owner_action_required')
        if action=='write' and op=='verify' and data['payload'].get('owner_assertion') is not True:
            raise KnowledgeError('knowledge_owner_action_required')
    if action in MUTATIONS:
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
            raise KnowledgeError('knowledge_request_id_required')
    if action in ('read','source-read') and data.get('audit') and owner_action is not True:
        raise KnowledgeError('knowledge_owner_action_required')
    if action=='index': vector(data['vector'],data['dimensions'])
    if action=='search':
        if (data.get('model') is None)!=(data.get('query_vector') is None):
            raise KnowledgeError('knowledge_vector_incompatible')
        if data.get('query_vector') is not None: vector(data['query_vector'])
    bounded_structure(data)
    try: raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
    except (ValueError,TypeError,UnicodeError,RecursionError): raise KnowledgeError() from None
    if len(raw)>130000: raise KnowledgeError('knowledge_request_too_large')


def invoke(api,record,action,data,*,request_id=None,owner_action=False,before_request=None):
    """Only the connected native binding supplies RPC identity. Never honor peer overrides."""
    validate(action,data,request_id=request_id,owner_action=owner_action)
    if not record or any(key not in record for key in BINDING):
        raise KnowledgeError('knowledge_binding_required')
    payload={'agent_id':record['agent_id'],'session_ref':record['session_ref'],**data}
    operation={'source-write':'knowledge_source_write','source-read':'knowledge_source_read',
               'catalog':'knowledge_catalog'}.get(action,'knowledge_'+action)
    try:
        if before_request is None:
            return api.call(operation,payload,request_id if action in MUTATIONS else None)
        from quorum_api import guarded_call
        return guarded_call(api,operation,payload,request_id if action in MUTATIONS else None,before_request=before_request,on_dispatch=lambda:None)
    except QuorumError as error:
        if str(error)=='service_error':
            raise KnowledgeError('knowledge_backend_unavailable_or_rejected') from None
        raise


def binding_snapshot(record):
    """Knowledge-only deep snapshot, independent of old reception/task grants."""
    if not isinstance(record,dict) or any(key not in record for key in BINDING): raise KnowledgeError('knowledge_binding_required')
    endpoints={key:record.get(key) for key in ('messaging_socket','grok_transport')}
    marker=hashlib.sha256(json.dumps(endpoints,sort_keys=True).encode()).hexdigest()
    value={key:record.get(key) for key in STABLE_BINDING}|{'endpoint_sha256':marker}
    return json.loads(json.dumps(value,sort_keys=True))


def scope(record,space_id):
    identifier(space_id)
    return binding_snapshot(record)|{'space_id':space_id}


def capture_authority(ledger,record,space_id):
    expected=scope(record,space_id)
    def authorize():
        grant=ledger.setting('knowledge-capture:'+space_id)
        current=ledger.setting('session-agent')
        expiry=grant.get('expires_at') if isinstance(grant,dict) else None
        now=ledger.clock()
        finite=(type(expiry) in (int,float) and now<expiry<=now+86400 and math.isfinite(expiry))
        if (not current or scope(current,space_id)!=expected or not isinstance(grant,dict) or grant.get('scope')!=expected
                or grant.get('revoked') is not False or not finite):
            raise KnowledgeError('knowledge_capture_denied')
    return authorize


class BoundApi:
    """Compose provenance with the grant guard at the actual token/HTTP boundary."""
    def __init__(self,api,authorize): self.api,self.authorize=api,authorize

    def call(self,operation,payload,request_id=None):
        return self.call_guarded(operation,payload,request_id,before_request=lambda:None)

    def call_guarded(self,operation,payload,request_id=None,*,before_request,on_dispatch=None):
        from quorum_api import guarded_call
        def check():
            self.authorize()
            before_request()
        check()
        result=guarded_call(self.api,operation,payload,request_id,before_request=check,
                            on_dispatch=on_dispatch or (lambda:None))
        check()
        return result


def capture_grant(ledger,record,space_id,*,ttl=3600,owner_action=False,revoke=False):
    if owner_action is not True: raise KnowledgeError('knowledge_owner_action_required')
    exact=scope(record,space_id)
    if type(revoke) is not bool or type(ttl) is not int or not 1<=ttl<=86400: raise KnowledgeError('knowledge_invalid_grant')
    grant={'scope':exact,'expires_at':ledger.clock()+ttl,'revoked':bool(revoke)}
    ledger.setting('knowledge-capture:'+space_id,grant)
    return {'capture':'revoked' if revoke else 'proposals_only','expires_at':grant['expires_at']}


def capture(api,ledger,record,space_id,entry,request_id,*,material_result=False):
    authorize=capture_authority(ledger,record,space_id)
    authorize()
    if material_result is not True: raise KnowledgeError('knowledge_material_result_required')
    fields(entry,PROPOSE_FIELDS,{'entry_id','type','title','body'})
    return invoke(api,record,'write',{'space_id':space_id,'operation':'propose','payload':entry},
                  request_id=request_id,before_request=authorize)


def add_commands(commands):
    command=commands.add_parser('knowledge',help='Explicit scoped project knowledge; no provider calls')
    command.add_argument('action',choices=tuple(FIELDS)+('capture-grant','capture','capture-source'))
    command.add_argument('--json-stdin',action='store_true',required=True,
                         help='Bounded operation arguments; identity comes from this connected native binding')
    command.add_argument('--request-id',help='Reuse identical arguments and ID after an uncertain mutation')
    command.add_argument('--owner-action',action='store_true',
                         help='Current explicit receiving-owner instruction for administration, verification or audit')
    command.add_argument('--material-result',action='store_true',help='Capture a material task result under its finite grant')


def dispatch(args,auth_ledger):
    try: return dispatch_bounded(args,auth_ledger)
    except RecursionError: raise KnowledgeError() from None


def dispatch_bounded(args,auth_ledger):
    from quorum_session import current_session,current_engine,require_caller,check_native_route
    sid=current_session(); engine=current_engine(); require_caller(sid,engine)
    raw=sys.stdin.buffer.read(131073)
    if len(raw)>131072: raise KnowledgeError('knowledge_request_too_large')
    try: data=json.loads(raw)
    except (ValueError,UnicodeError,RecursionError): raise KnowledgeError() from None
    if args.action not in ('capture','capture-source','capture-grant'):
        validate(args.action,data,request_id=args.request_id,owner_action=args.owner_action)
    ledger=LocalLedger(auth_ledger.root/'sessions'/sid)
    try:
        record=ledger.setting('session-agent')
        if not record or record.get('session_ref')!=sid or record.get('engine')!=engine:
            raise KnowledgeError('knowledge_binding_required')
        saved=binding_snapshot(record)
        capture_check=None
        def check_identity():
            require_caller(sid,engine)
            latest=ledger.setting('session-agent')
            if (binding_snapshot(latest)!=saved or str(Path.cwd().resolve())!=saved['cwd']):
                raise KnowledgeError('knowledge_binding_required')
            return latest
        def authorize():
            latest=check_identity()
            if latest.get('transport')=='tool-inbox':
                from quorum_native_inbox import require_worker
                require_worker(latest)
            elif engine=='claude': check_native_route(latest)
            elif engine=='grok':
                from quorum_grok_leader import local_binding
                if local_binding(sid,saved['cwd'])!=latest.get('grok_transport'):
                    raise KnowledgeError('knowledge_binding_required')
            check_identity()  # Local provenance checks may block too.
            if capture_check is not None: capture_check()
        authorize()
        if args.action=='capture-grant':
            fields(data,{'space_id','ttl','revoke'},{'space_id'})
            return capture_grant(ledger,record,**data,owner_action=args.owner_action)
        if args.action in ('capture','capture-source'):
            source=args.action=='capture-source'
            fields(data,{'space_id','payload' if source else 'entry'},
                   {'space_id','payload' if source else 'entry'})
            if args.material_result is not True: raise KnowledgeError('knowledge_material_result_required')
            if source:
                validate('source-write',data,request_id=args.request_id)
            else:
                fields(data['entry'],PROPOSE_FIELDS,{'entry_id','type','title','body'})
                validate('write',{'space_id':data['space_id'],'operation':'propose','payload':data['entry']},request_id=args.request_id)
            capture_check=capture_authority(ledger,record,data['space_id'])
            authorize()
        from quorum_live import api_for
        from quorum_release import ReleaseGuard
        api,_,_=api_for(auth_ledger)
        api=BoundApi(api,authorize)
        authorize()
        # Knowledge commands do not grant lifecycle control over a resident run.
        ReleaseGuard(api,ledger,stop_on_mismatch=False).check()
        if args.action=='capture-source':
            return invoke(api,record,'source-write',data,request_id=args.request_id,before_request=capture_check)
        if args.action=='capture':
            return capture(api,ledger,record,data['space_id'],data['entry'],args.request_id,
                           material_result=args.material_result)
        return invoke(api,record,args.action,data,request_id=args.request_id,owner_action=args.owner_action)
    finally: ledger.db.close()
