"""Explicit project knowledge commands. No embedding generation or background capture."""
import hashlib
import json
import math
from pathlib import Path
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
ENTRY_FIELDS={'entry_id','type','title','body','source_refs','versions','review_after','conflicts_with'}
WRITE_FIELDS={
    'propose':ENTRY_FIELDS,
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
    'configure_vector':{'space_id','model','dimensions'},
}
FIELDS={
    'admin':{'operation','payload'},'write':{'space_id','operation','payload'},
    'search':{'space_id','query','limit','context_budget','model','query_vector'},
    'read':{'space_id','entry_id','revision','offset','length','audit'},
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


def validate(action,data,*,request_id=None,owner_action=False):
    if action not in FIELDS: raise KnowledgeError('knowledge_invalid_operation')
    fields(data,FIELDS[action], {'operation','payload'} if action=='admin' else
           {'space_id','operation','payload'} if action=='write' else
           {'space_id','query'} if action=='search' else
           {'space_id','entry_id'} if action=='read' else FIELDS['index'])
    for key in ('space_id','entry_id'):
        if key in data: identifier(data[key])
    if action in ('admin','write'):
        op=data['operation']
        if not isinstance(op,str): raise KnowledgeError()
        allowed=(ADMIN_FIELDS if action=='admin' else WRITE_FIELDS).get(op)
        if allowed is None: raise KnowledgeError('knowledge_invalid_operation')
        fields(data['payload'],allowed)
        if (action=='admin' or op=='verify') and owner_action is not True:
            raise KnowledgeError('knowledge_owner_action_required')
        if action=='write' and op=='verify' and data['payload'].get('owner_assertion') is not True:
            raise KnowledgeError('knowledge_owner_action_required')
    if action in ('admin','write','index'):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:
            raise KnowledgeError('knowledge_request_id_required')
    if action=='read' and data.get('audit') and owner_action is not True:
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
    try:
        if before_request is None:
            return api.call('knowledge_'+action,payload,request_id if action in ('admin','write','index') else None)
        from quorum_api import guarded_call
        return guarded_call(api,'knowledge_'+action,payload,request_id,before_request=before_request,on_dispatch=lambda:None)
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
    fields(entry,ENTRY_FIELDS,{'entry_id','type','title','body'})
    return invoke(api,record,'write',{'space_id':space_id,'operation':'propose','payload':entry},
                  request_id=request_id,before_request=authorize)


def add_commands(commands):
    command=commands.add_parser('knowledge',help='Explicit scoped project knowledge; no provider calls')
    command.add_argument('action',choices=tuple(FIELDS)+('capture-grant','capture'))
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
    if args.action not in ('capture','capture-grant'):
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
        if args.action=='capture':
            fields(data,{'space_id','entry'},{'space_id','entry'})
            if args.material_result is not True: raise KnowledgeError('knowledge_material_result_required')
            fields(data['entry'],ENTRY_FIELDS,{'entry_id','type','title','body'})
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
        if args.action=='capture':
            return capture(api,ledger,record,data['space_id'],data['entry'],args.request_id,
                           material_result=args.material_result)
        return invoke(api,record,args.action,data,request_id=args.request_id,owner_action=args.owner_action)
    finally: ledger.db.close()
