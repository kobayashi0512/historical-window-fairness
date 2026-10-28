"""Receipt replay and raw-input checks; no access to allocator internals."""
import bisect
import hashlib
import json
import sqlite3


def assignment_digest(owners):
    return hashlib.sha256(json.dumps(owners, separators=(',', ':')).encode()).hexdigest()


def direct_metrics(events, owners, n):
    degree, count = [0] * n, [0] * n
    low, high = [0] * n, [0] * n
    max_prefix, bad_cells, bad_prefixes, active_bad = 0, 0, 0, 0
    for (_, u, v), owner in zip(events, owners, strict=True):
        if owner not in (u, v):
            raise AssertionError('ineligible final owner')
        active_bad -= int(not degree[u] // 2 <= count[u] <= (degree[u] + 1) // 2)
        active_bad -= int(not degree[v] // 2 <= count[v] <= (degree[v] + 1) // 2)
        degree[u] += 1
        degree[v] += 1
        count[owner] += 1
        for p in (u, v):
            delta = 2 * count[p] - degree[p]
            low[p], high[p] = min(low[p], delta), max(high[p], delta)
            max_prefix = max(max_prefix, abs(delta))
            active_bad += int(not degree[p] // 2 <= count[p] <= (degree[p] + 1) // 2)
        bad_cells += active_bad
        bad_prefixes += int(active_bad > 0)
    return {'max_prefix_error': max_prefix / 2, 'max_window_error': max((b - a for a, b in zip(low, high)), default=0) / 2,
            'violating_prefix_cells': bad_cells, 'violating_prefixes': bad_prefixes}


class ReceiptReplay:
    def __init__(self, events, n, exact_required):
        self.events, self.n, self.exact_required = events, n, exact_required
        self.owners = []
        self.pending = [None] * n
        self.links = []
        self.adj = [[] for _ in events]
        self.bad_links = set()
        self.changes, self.maximum, self.direct_checked = 0, 0, 0
        self.trajectory = hashlib.sha256()

    def apply(self, update_id, payload):
        if update_id != len(self.owners) or payload['task_id'] != update_id:
            raise AssertionError('update order or task ID mismatch')
        _, u, v = self.events[update_id]
        owner = payload['owner']
        if owner not in (u, v):
            raise AssertionError('ineligible new owner')
        changed = payload['changes']
        ids = [row[0] for row in changed]
        if ids != sorted(set(ids)):
            raise AssertionError('change IDs must be unique and sorted')
        for i, old_owner, new_owner in changed:
            if not 0 <= i < update_id or self.owners[i] != old_owner:
                raise AssertionError('wrong old owner or task range')
            a, b = self.events[i][1:]
            if new_owner not in (a, b) or new_owner == old_owner:
                raise AssertionError('invalid reassignment')
            self.owners[i] = new_owner
        self.owners.append(owner)
        for vertex in (u, v):
            previous = self.pending[vertex]
            if previous is None:
                self.pending[vertex] = update_id
            else:
                link = len(self.links)
                self.links.append((previous, update_id, vertex))
                self.adj[previous].append(link)
                self.adj[update_id].append(link)
                self.pending[vertex] = None
        touched = {link for i in ids + [update_id] for link in self.adj[i]}
        for link in touched:
            a, b, vertex = self.links[link]
            satisfied = int(self.owners[a] == vertex) + int(self.owners[b] == vertex) == 1
            if satisfied:
                self.bad_links.discard(link)
            else:
                self.bad_links.add(link)
        if self.exact_required and self.bad_links:
            raise AssertionError('a prefix quota pair became invalid')
        if update_id < 128:
            m = direct_metrics(self.events[:update_id + 1], self.owners, self.n)
            if self.exact_required and m['violating_prefix_cells']:
                raise AssertionError('direct prefix check failed')
            self.direct_checked += 1
        self.changes += len(changed)
        self.maximum = max(self.maximum, len(changed))
        self.trajectory.update(json.dumps(payload, separators=(',', ':')).encode() + b'\n')

    def window(self, update_count, start_ts, end_ts):
        times = [e[0] for e in self.events]
        lo = bisect.bisect_left(times, start_ts, 0, update_count)
        hi = bisect.bisect_right(times, end_ts, 0, update_count)
        delta = {}
        for j in range(lo, hi):
            _, u, v = self.events[j]
            delta[u] = delta.get(u, 0) - 1
            delta[v] = delta.get(v, 0) - 1
            owner = self.owners[j]
            delta[owner] += 2
        return {'rows': hi - lo, 'max_error': max(map(abs, delta.values()), default=0) / 2}


def audit_database(path, events, n, exact_required, expected_digest, expected_trajectory, queries):
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        replay = ReceiptReplay(events, n, exact_required)
        by_update = {}
        for query in queries:
            by_update.setdefault(query['update_count'], []).append(query)
        for i, text in connection.execute('SELECT update_id,payload FROM updates ORDER BY update_id'):
            replay.apply(i, json.loads(text))
            for query in by_update.get(i + 1, []):
                observed = replay.window(i + 1, query['start_ts'], query['end_ts'])
                if any(observed[key] != query[key] for key in ('rows', 'max_error')):
                    raise AssertionError('time-window result mismatch')
        if len(replay.owners) != len(events):
            raise AssertionError('missing updates')
        for j, row in enumerate(connection.execute('SELECT task_id,timestamp,u,v,owner FROM records ORDER BY task_id')):
            if tuple(row) != (j, *events[j], replay.owners[j]):
                raise AssertionError('database differs from raw stream/receipt replay')
        if connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] != len(events):
            raise AssertionError('missing records')
        if assignment_digest(replay.owners) != expected_digest:
            raise AssertionError('allocator/database final assignment mismatch')
        if replay.trajectory.hexdigest() != expected_trajectory:
            raise AssertionError('allocator/database change trajectory mismatch')
        final = direct_metrics(events, replay.owners, n)
        if exact_required and final['violating_prefix_cells']:
            raise AssertionError('final direct prefix audit failed')
        return {'pass': True, 'updates_replayed': len(events), 'incremental_constraint_check_every_update': True,
                'direct_all_prefix_updates': replay.direct_checked, 'final_all_prefix_check': True,
                'window_queries_verified': len(queries), 'old_changes': replay.changes,
                'max_update_old_changes': replay.maximum, 'final_metrics': final,
                'trajectory_sha256': replay.trajectory.hexdigest(), 'assignment_sha256': assignment_digest(replay.owners)}
    finally:
        connection.close()
