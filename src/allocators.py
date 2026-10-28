"""Matched API; standard recoloring is a comparator, not a novelty claim."""
import gzip
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get('HWF_DATA_DIR', ROOT.parent / 'data'))
REFERENCE = ROOT / 'prefix_pairing_recourse.py'
DATASETS = {'emailEU': 'email-Eu-core-temporal.txt.gz', 'CollegeMsg': 'CollegeMsg.txt.gz'}
METHODS = ('ReferencePaired', 'StandardParityDSU', 'DebtGreedy')


def load_development(name):
    filename = DATASETS[name]
    path = DATA_ROOT / 'raw' / filename
    if not path.exists():
        raise FileNotFoundError(
            f'{path} is absent. Download the public source listed in '
            f'{DATA_ROOT / "DOWNLOAD_MANIFEST.json"} and place it in data/raw/.'
        )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = next(
        f['sha256'] for f in json.loads((DATA_ROOT / 'DOWNLOAD_MANIFEST.json').read_text())['files']
        if f['file'] == filename
    )
    if sha != expected:
        raise ValueError('source checksum mismatch')
    events, self_loops = [], 0
    with gzip.open(path, 'rt') as stream:
        for line in stream:
            if not line.strip() or line.startswith('#'):
                continue
            u, v, ts = map(int, line.split())
            if u == v:
                self_loops += 1
            else:
                events.append((ts, u, v))
    events.sort(key=lambda e: e[0])
    full_count = len(events)
    events = events[:full_count * 3 // 5]
    nodes = sorted({p for _, u, v in events for p in (u, v)})
    mapping = {p: i for i, p in enumerate(nodes)}
    records = [(ts, min(mapping[u], mapping[v]), max(mapping[u], mapping[v])) for ts, u, v in events]
    return records, len(nodes), {'source_sha256': sha, 'source_file': filename, 'nonloop_total': full_count,
                                'development_events': len(records), 'reserved_unbenchmarked': full_count - len(records),
                                'removed_self_loops': self_loops, 'normalized_nodes': len(nodes)}


class ReferencePaired:
    def __init__(self, n):
        spec = importlib.util.spec_from_file_location('readonly_prefix_reference', REFERENCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.impl = module.PrefixPairing(n)

    def add(self, u, v):
        before = len(self.impl.flip_components)
        sign = self.impl.add(u, v)
        changed = []
        for component in self.impl.flip_components[before:]:
            for i in component:
                a, b = self.impl.edges[i]
                owner = a if self.impl.z[i] == 1 else b
                changed.append((i, b if owner == a else a, owner))
        return u if sign == 1 else v, sorted(changed)

    def snapshot(self):
        return self.impl.choices()


class StandardParityDSU:
    """Generic XOR components, lazy root colors and eager change enumeration.

    Union-by-size/root-color flips are established recoloring mechanisms. The
    member chain permits listing actual affected records without a full scan.
    """
    def __init__(self, n):
        self.n = n
        self.pending = [None] * n
        self.edges = []
        self.parent, self.parity, self.color, self.size = [], [], [], []
        self.head, self.tail, self.next_member = [], [], []

    def find(self, x):
        root, total = x, 0
        while self.parent[root] != root:
            total ^= self.parity[root]
            root = self.parent[root]
        result = total
        while self.parent[x] != x:
            old_parent, step = self.parent[x], self.parity[x]
            self.parent[x], self.parity[x] = root, total
            total ^= step
            x = old_parent
        return root, result

    def bit(self, i):
        root, parity = self.find(i)
        return self.color[root] ^ parity

    def add(self, u, v):
        if not (0 <= u < v < self.n):
            raise ValueError('normalized distinct endpoints required')
        j = len(self.edges)
        requirements = []
        for new_position, vertex in enumerate((u, v)):
            old = self.pending[vertex]
            if old is not None:
                old_position = int(self.edges[old][1] == vertex)
                root, parity = self.find(old)
                relation = 1 ^ old_position ^ new_position
                requirements.append((root, self.color[root] ^ parity ^ relation))
        changes = []
        if len(requirements) == 2:
            (a, want_a), (b, want_b) = requirements
            if a == b and want_a != want_b:
                raise AssertionError('inconsistent signed cycle')
            if want_a != want_b:
                flip = a if self.size[a] <= self.size[b] else b
                i = self.head[flip]
                while i != -1:
                    old_bit = self.bit(i)
                    changes.append((i, self.edges[i][old_bit], self.edges[i][old_bit ^ 1]))
                    i = self.next_member[i]
                self.color[flip] ^= 1
                if flip == a:
                    want_a ^= 1
                else:
                    want_b ^= 1
            assert want_a == want_b
            new_bit = want_a
            roots = list(dict.fromkeys((a, b)))
        elif requirements:
            root, new_bit = requirements[0]
            roots = [root]
        else:
            new_bit, roots = 0, []

        self.edges.append((u, v))
        self.parent.append(j)
        self.parity.append(0)
        self.color.append(new_bit)
        self.size.append(1)
        self.head.append(j)
        self.tail.append(j)
        self.next_member.append(-1)
        if roots:
            base = min(roots, key=lambda r: (-self.size[r], r))
            for other in roots:
                if other == base:
                    continue
                self.parent[other] = base
                self.parity[other] = self.color[other] ^ self.color[base]
                self.next_member[self.tail[base]] = self.head[other]
                self.tail[base] = self.tail[other]
                self.size[base] += self.size[other]
            self.parent[j] = base
            self.parity[j] = new_bit ^ self.color[base]
            self.next_member[self.tail[base]] = j
            self.tail[base] = j
            self.size[base] += 1
        for vertex in (u, v):
            self.pending[vertex] = j if self.pending[vertex] is None else None
        return (u, v)[new_bit], sorted(changes)

    def snapshot(self):
        return [edge[self.bit(j)] for j, edge in enumerate(self.edges)]


class DebtGreedy:
    def __init__(self, n):
        self.delta = [0] * n
        self.owners = []

    def add(self, u, v):
        a, b = self.delta[u], self.delta[v]
        left = (max(abs(a + 1), abs(b - 1)), (a + 1) ** 2 + (b - 1) ** 2, u)
        right = (max(abs(a - 1), abs(b + 1)), (a - 1) ** 2 + (b + 1) ** 2, v)
        owner = min(left, right)[2]
        self.delta[u] += 1 if owner == u else -1
        self.delta[v] += 1 if owner == v else -1
        self.owners.append(owner)
        return owner, []

    def snapshot(self):
        return self.owners.copy()


def make_allocator(name, n):
    return {'ReferencePaired': ReferencePaired, 'StandardParityDSU': StandardParityDSU, 'DebtGreedy': DebtGreedy}[name](n)
