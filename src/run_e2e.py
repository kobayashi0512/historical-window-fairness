"""One isolated local SQLite workload. No full-record/network migration claim."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import platform
import resource
import sqlite3
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from allocators import DATASETS, METHODS, REFERENCE, ROOT, load_development, make_allocator
from verification import assignment_digest, audit_database

BATCH = 256
QUERY_EVERY = 8192


class DurableIndex:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            raise ValueError('refusing to overwrite a database')
        self.connection = sqlite3.connect(path, isolation_level=None)
        assert self.connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('PRAGMA wal_autocheckpoint=1000')
        self.connection.executescript('''
            CREATE TABLE records(task_id INTEGER PRIMARY KEY, timestamp INTEGER NOT NULL,
                u INTEGER NOT NULL, v INTEGER NOT NULL, owner INTEGER NOT NULL);
            CREATE INDEX records_time ON records(timestamp,task_id);
            CREATE TABLE updates(update_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        self.logical_owner_writes = 0
        self.journal_bytes = 0

    def write(self, j, event, owner, changes):
        c = self.connection
        if not c.in_transaction:
            c.execute('BEGIN IMMEDIATE')
        if changes:
            result = c.executemany('UPDATE records SET owner=? WHERE task_id=? AND owner=?',
                                  ((new, i, old) for i, old, new in changes))
            if result.rowcount != len(changes):
                raise AssertionError('conditional ownership update missed a record')
        payload = json.dumps({'task_id': j, 'owner': owner, 'changes': changes}, separators=(',', ':'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', (j, *event, owner))
        c.execute('INSERT INTO updates VALUES(?,?)', (j, payload))
        self.logical_owner_writes += 1 + len(changes)
        self.journal_bytes += len(payload.encode())
        return payload

    def commit(self):
        self.connection.commit()

    def query(self, update_count, start_ts, end_ts, anchor_events):
        if self.connection.in_transaction:
            raise AssertionError('query only committed versions')
        rows = self.connection.execute('SELECT u,v,owner FROM records WHERE timestamp>=? AND timestamp<=?',
                                       (start_ts, end_ts)).fetchall()
        delta = {}
        for u, v, owner in rows:
            delta[u] = delta.get(u, 0) - 1
            delta[v] = delta.get(v, 0) - 1
            delta[owner] += 2
        return {'update_count': update_count, 'start_ts': start_ts, 'end_ts': end_ts,
                'anchor_events': anchor_events, 'rows': len(rows),
                'max_error': max(map(abs, delta.values()), default=0) / 2}

    def finish(self):
        assert not self.connection.in_transaction
        result = self.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        assert result[0] == 0, 'checkpoint busy'
        total_sql_changes = self.connection.total_changes
        self.connection.close()
        return total_sql_changes


def distribution(values):
    ordered = sorted(values)
    def at(q):
        return ordered[max(0, math.ceil(q * len(ordered)) - 1)] / 1e6 if ordered else 0
    return {'p50_ms': at(.5), 'p95_ms': at(.95), 'p99_ms': at(.99), 'max_ms': at(1)}


def run(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    protocol = ROOT / 'PROTOCOL.txt'
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'dataset': args.dataset,
                'method': args.method, 'rep': args.rep, 'limit': args.limit,
                'timing_class': args.timing_class, 'python': platform.python_version(),
                'platform': platform.platform(), 'machine': platform.machine(), 'sqlite': sqlite3.sqlite_version,
                'protocol_sha256': hashlib.sha256(protocol.read_bytes()).hexdigest(),
                'source_files': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [protocol, REFERENCE, ROOT / 'allocators.py', ROOT / 'verification.py', Path(__file__)]},
                'transaction_events': BATCH, 'query_every_events': QUERY_EVERY,
                'durability': 'SQLite WAL FULL; batch acknowledgments; local OS sync semantics',
                'not_measured': ['network delivery', 'record payload relocation', 'power-loss testing', 'physical SSD writes']}
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    start = time.perf_counter_ns()
    events, n, data_metadata = load_development(args.dataset)
    if args.limit is not None:
        events = events[:args.limit]
    after_read = time.perf_counter_ns()
    allocator = make_allocator(args.method, n)
    store = DurableIndex(out / 'index.sqlite')
    after_init = time.perf_counter_ns()
    trajectory = hashlib.sha256()
    measurements, queries, commit_times = [], [], []
    wal_sample_max = 0
    wal_path = out / 'index.sqlite-wal'
    for j, (ts, u, v) in enumerate(events):
        step_start = time.perf_counter_ns()
        owner, changes = allocator.add(u, v)
        after_algorithm = time.perf_counter_ns()
        payload = store.write(j, events[j], owner, changes)
        after_write = time.perf_counter_ns()
        commit_ns, query_ns = 0, 0
        is_commit = (j + 1) % BATCH == 0 or j + 1 == len(events)
        if is_commit:
            commit_start = time.perf_counter_ns()
            store.commit()
            commit_ns = time.perf_counter_ns() - commit_start
            commit_times.append(commit_ns)
            wal_sample_max = max(wal_sample_max, wal_path.stat().st_size if wal_path.exists() else 0)
        if (j + 1) % QUERY_EVERY == 0 or j + 1 == len(events):
            assert is_commit
            query_start = time.perf_counter_ns()
            for width in (1000, 10000):
                queries.append(store.query(j + 1, events[max(0, j + 1 - width)][0], ts, width))
            query_ns = time.perf_counter_ns() - query_start
        step_ns = time.perf_counter_ns() - step_start
        measurements.append((j, len(changes), after_algorithm - step_start, after_write - after_algorithm,
                             commit_ns, query_ns, step_ns))
        trajectory.update(payload.encode() + b'\n')
    finish_start = time.perf_counter_ns()
    total_sql_changes = store.finish()
    end = time.perf_counter_ns()
    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = rss_raw if sys.platform == 'darwin' else rss_raw * 1024
    # Final snapshots/digests and independent replay are research verification,
    # outside the product-runtime interval. Timing instrumentation is inside it.
    final_digest = assignment_digest(allocator.snapshot())
    query_seconds = sum(r[5] for r in measurements) / 1e9
    moves = sum(r[1] for r in measurements)
    assert total_sql_changes == 2 * len(events) + moves
    audit_start = time.perf_counter()
    audit = audit_database(out / 'index.sqlite', events, n, args.method != 'DebtGreedy', final_digest,
                          trajectory.hexdigest(), queries)
    audit_seconds = time.perf_counter() - audit_start
    assert audit['old_changes'] == moves
    summary = {'dataset': args.dataset, 'method': args.method, 'rep': args.rep,
               'timing_class': args.timing_class, 'events': len(events), 'data_metadata': data_metadata,
               'end_to_end_seconds': (end - start) / 1e9, 'read_prepare_seconds': (after_read - start) / 1e9,
               'init_seconds': (after_init - after_read) / 1e9,
               'algorithm_event_enumeration_seconds': sum(r[2] for r in measurements) / 1e9,
               'serialize_sql_seconds': sum(r[3] for r in measurements) / 1e9,
               'batch_commit_seconds': sum(commit_times) / 1e9, 'query_seconds': query_seconds,
               'checkpoint_close_seconds': (end - finish_start) / 1e9,
               'update_execution_including_triggered_queries': distribution([r[6] for r in measurements]),
               'update_execution_excluding_periodic_query': distribution([r[6] - r[5] for r in measurements]),
               'batch_commit_latency': distribution(commit_times), 'batch_commit_count': len(commit_times),
               'old_changes': moves, 'old_changes_per_event': moves / len(events),
               'max_update_old_changes': max(r[1] for r in measurements),
               'logical_owner_writes': store.logical_owner_writes,
               'journal_rows': len(events), 'journal_json_bytes': store.journal_bytes,
               'sqlite_total_changed_rows': total_sql_changes, 'database_bytes': (out / 'index.sqlite').stat().st_size,
               'wal_bytes_max_sampled_after_commit': wal_sample_max,
               'runtime_process_peak_rss_bytes': rss_bytes, 'offline_audit_seconds': audit_seconds,
               'fairness': audit['final_metrics'], 'audit': audit,
               'timing_scope': 'read to checkpoint/close; includes instrumentation; offline audit/CSV output excluded',
               'latency_scope': 'execution only; batch waiting/arrival queues/network acknowledgment not measured'}
    with (out / 'UPDATES.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('update_id', 'old_changes', 'algorithm_ns', 'serialize_sql_ns', 'commit_ns', 'query_ns', 'execution_ns'))
        writer.writerows(measurements)
    (out / 'QUERIES.json').write_text(json.dumps(queries, indent=2) + '\n')
    (out / 'RESULT.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'dataset': args.dataset, 'method': args.method, 'rep': args.rep, 'events': len(events),
                      'end_to_end_seconds': summary['end_to_end_seconds'], 'audit_pass': audit['pass'],
                      'old_changes': moves, 'max_window_error': summary['fairness']['max_window_error']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', choices=DATASETS, required=True)
    parser.add_argument('--method', choices=METHODS, required=True)
    parser.add_argument('--rep', type=int, default=0)
    parser.add_argument('--out', required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--timing-class', choices=('functional_only', 'isolated_development_repeats'), default='functional_only')
    run(parser.parse_args())
