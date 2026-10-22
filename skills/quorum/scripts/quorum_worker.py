"""Single-step worker; preserves launch uncertainty instead of blind replay."""
import json
import uuid
from quorum_release import ReleaseGuard, ReleaseMismatch


class Worker:
    def __init__(self, api, ledger, runner, device_id, policy, binding_resolver=None):
        self.api, self.ledger, self.runner = api, ledger, runner
        self.device_id, self.policy = device_id, policy
        self.binding_resolver = binding_resolver
        self.release = ReleaseGuard(api, ledger)

    def run_once(self):
        self.policy.validate()
        self.release.check()
        with self.ledger.worker_lock():
            pending = self.ledger.db.execute("select id,generation,reply from attempts where state='reply_ready' order by rowid limit 1").fetchone()
            if pending is not None:
                return self._complete(pending[0], pending[1], pending[2])
            claim = self.api.call('inbox_claim', {'device_id': self.device_id}, str(uuid.uuid4()))
            if claim is None:
                return 'idle'
            attempt_id, generation = claim['attempt_id'], claim['generation']
            old = self.ledger.attempt(attempt_id)
            if old:
                if old['generation'] != generation:
                    return 'unknown'
                if old['state'] == 'reply_ready':
                    return self._complete(attempt_id, generation, old['reply'])
                return old['state']
            delivery = claim['delivery']
            binding = (self.binding_resolver(self.ledger, delivery) if self.binding_resolver
                       else self.ledger.binding(delivery['conversation_id']))
            context = self.api.call('context_read', {'quorum_id': binding.quorum_id,
                                                    'project_ref': binding.project_ref, 'after_version': 0})
            prompt = json.dumps({'peer_message': delivery['body'], 'context': context}, ensure_ascii=False)
            if len(prompt.encode()) > 65536 or len(delivery['body'].encode()) > 16384:
                raise ValueError('context_too_large')
            self.release.check()
            data = {'attempt_id': attempt_id, 'generation': generation}
            self.api.call('attempt_update', {**data, 'event': 'launch_intent'}, attempt_id + ':intent')
            self.ledger.set_attempt(attempt_id, generation, 'launch_intent')
            self.ledger.record_event(attempt_id, 'launch_intent', {'generation': generation, 'session_name': binding.name})
            # No automatic retry after this boundary: an exception may follow a spawn.
            handle = None
            try:
                self.release.check()
                handle = self.runner.start(binding, prompt, self.policy)
                result = None
                for event in self.runner.events(handle):
                    if event.get('type') == 'started':
                        self.api.call('attempt_update', {**data, 'event': 'started'}, attempt_id + ':started')
                        self.ledger.set_attempt(attempt_id, generation, 'running')
                    elif event.get('type') == 'heartbeat':
                        self.release.check()
                        # A fresh request ID rechecks revocation and extends the lease.
                        status = self.api.call('attempt_update', {**data, 'event': 'heartbeat'}, str(uuid.uuid4()))
                        if status and status.get('state') == 'cancel_requested':
                            raise ValueError('cancellation_requested')
                    elif event.get('type') == 'completed' and event.get('success') is True:
                        result = event.get('text')
                if not isinstance(result, str) or len(result.encode()) > 16384:
                    raise ValueError('completion_unconfirmed')
            except Exception as error:
                if handle is not None:
                    try: self.runner.cancel(handle)
                    except Exception: pass
                self.ledger.set_attempt(attempt_id, generation, 'unknown')
                try:
                    self.api.call('attempt_update', {**data, 'event': 'unknown'}, attempt_id + ':unknown')
                except Exception:
                    pass
                if isinstance(error, ReleaseMismatch): raise
                return 'unknown'
            self.ledger.set_attempt(attempt_id, generation, 'reply_ready', result)
            return self._complete(attempt_id, generation, result)

    def _complete(self, attempt_id, generation, reply):
        self.release.check()
        self.api.call('delivery_complete', {'attempt_id': attempt_id, 'generation': generation,
                                         'reply_body': reply}, attempt_id + ':reply')
        self.ledger.set_attempt(attempt_id, generation, 'answered')
        return 'answered'

    def reconcile(self):
        self.release.check()
        rows = self.ledger.db.execute("select id,generation from attempts where state in ('launch_intent','running')").fetchall()
        for attempt, generation in rows:
            self.ledger.set_attempt(attempt, generation, 'unknown')
        return [dict(attempt_id=row[0], state='unknown') for row in rows]
