"""Scoped MCP tools; human credentials remain in the owner-side API process."""
import json
import sys

TOOLS = {
    'quorum_send': ('message_send', {'quorum_id', 'conversation_id', 'recipient_agent_id', 'body', 'reply_to', 'request_id'}),
    'quorum_context': ('context_read', {'quorum_id', 'project_ref', 'after_version'}),
    'quorum_status': ('agent_status', set()),
    'quorum_thread': ('conversation_read', {'quorum_id', 'conversation_id', 'after_sequence'}),
}


class McpTools:
    def __init__(self, authorize, call, scope=None):
        self.authorize, self.rpc, self.scope = authorize, call, scope

    def call(self, agent_capability, name, arguments):
        agent = self.authorize(agent_capability)
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ValueError('tool_denied')
        operation, allowed = TOOLS[name]
        if set(arguments) - allowed:
            raise ValueError('argument_denied')
        data = dict(arguments)
        if self.scope is None:
            raise ValueError('agent_scope_required')
        scope = self.scope(agent)
        if ('quorum_id' in data and data['quorum_id'] != scope['quorum_id']
                or 'project_ref' in data and data['project_ref'] != scope['project_ref']):
            raise ValueError('scope_denied')
        if name == 'quorum_send':
            if not isinstance(data.get('body'), str) or len(data['body'].encode()) > 16384:
                raise ValueError('invalid_message')
            data['sender_agent_id'] = agent
        if name in ('quorum_status', 'quorum_thread'):
            data['agent_id'] = agent
        return self.rpc(operation, data)


def serve(tools, capability, input_stream=sys.stdin, output_stream=sys.stdout):
    initialized = False
    for line in input_stream:
        if len(line.encode()) > 65536:
            raise ValueError('mcp_request_too_large')
        request = json.loads(line)
        if 'id' not in request:
            continue
        try:
            method = request['method']
            if method == 'initialize':
                version = request.get('params', {}).get('protocolVersion')
                if version not in ('2024-11-05', '2025-03-26', '2025-06-18'):
                    raise ValueError('unsupported_protocol')
                initialized = True
                result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'quorum', 'version': '0.1.0'}}
            elif not initialized:
                raise ValueError('not_initialized')
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': name.replace('_', ' '),
                                    'inputSchema': {'type': 'object', 'properties': {
                                        key: {'type': 'integer' if key in ('after_version', 'after_sequence') else 'string'}
                                        for key in allowed}, 'additionalProperties': False}}
                                   for name, (_, allowed) in TOOLS.items()]}
            elif method == 'tools/call':
                params = request['params']
                value = tools.call(capability, params['name'], params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}]}
            else:
                raise ValueError('method_not_found')
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except (ValueError, KeyError):
            response = {'jsonrpc': '2.0', 'id': request['id'],
                        'error': {'code': -32602, 'message': 'request_denied'}}
        output_stream.write(json.dumps(response) + '\n')
        output_stream.flush()
