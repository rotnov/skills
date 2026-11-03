"""Readiness barriers for one synthetic PostgreSQL statement, never BEGIN alone."""
import json
import time

from test_quorum_close_race import TIMEOUT


def wait_statement(cluster, db, label, statement, predicate, what, *, advisory=False):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        raw = cluster.sql(db, f"""select json_build_object('pid',a.pid,'state',a.state,
          'query',a.query,'wait_event_type',a.wait_event_type,'wait_event',a.wait_event,
          'blocking',pg_blocking_pids(a.pid),'advisory',exists(select 1 from pg_locks l
            where l.pid=a.pid and l.locktype='advisory' and l.granted))
          from pg_stat_activity a where a.application_name='{label}'""").strip()
        if raw:
            row = json.loads(raw)
            if statement in row['query'] and predicate(row) and (not advisory or row['advisory']):
                return row
        time.sleep(0.1)
    raise AssertionError(f'{label} never reached statement barrier: {what}')
