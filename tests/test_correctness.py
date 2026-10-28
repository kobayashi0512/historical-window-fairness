import itertools
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from allocators import ReferencePaired, StandardParityDSU, load_development
from run_e2e import DurableIndex
from verification import ReceiptReplay, direct_metrics


def independent_valid(edges, owners, n):
    for k in range(1, len(edges) + 1):
        for p in range(n):
            degree = sum(p in edge for edge in edges[:k])
            allocated = sum(x == p for x in owners[:k])
            if not degree // 2 <= allocated <= (degree + 1) // 2:
                return False
    return True


class Correctness(unittest.TestCase):
    def test_exhaustive_four_party_length_four_against_bruteforce(self):
        pool = list(itertools.combinations(range(4), 2))
        updates = 0
        for edges in itertools.product(pool, repeat=4):
            a, b, previous = ReferencePaired(4), StandardParityDSU(4), []
            for j, (u, v) in enumerate(edges):
                answer_a, answer_b = a.add(u, v), b.add(u, v)
                self.assertEqual(answer_a, answer_b)
                current = b.snapshot()
                self.assertTrue(independent_valid(edges[:j + 1], current, 4))
                exact_changes = [(i, old, new) for i, (old, new) in enumerate(zip(previous, current)) if old != new]
                self.assertEqual(exact_changes, answer_b[1])
                optimum = min(sum(old != new for old, new in zip(previous, assignment))
                              for assignment in itertools.product(*edges[:j + 1])
                              if independent_valid(edges[:j + 1], assignment, 4))
                self.assertEqual(len(exact_changes), optimum)
                previous = current
                updates += 1
        self.assertEqual(updates, 5184)

    def test_longer_random_stream_receipts(self):
        for seed in range(40):
            rng = random.Random(20261016 + seed)
            edges = [tuple(sorted(rng.sample(range(8), 2))) for _ in range(80)]
            events = [(j // 3, *edge) for j, edge in enumerate(edges)]
            a, b = ReferencePaired(8), StandardParityDSU(8)
            replay = ReceiptReplay(events, 8, True)
            for j, edge in enumerate(edges):
                one, two = a.add(*edge), b.add(*edge)
                self.assertEqual(one, two)
                replay.apply(j, {'task_id': j, 'owner': two[0], 'changes': two[1]})
                self.assertEqual(replay.owners, b.snapshot())

    def test_real_prefix_both_sources(self):
        data_root = Path(__import__('os').environ.get('HWF_DATA_DIR', Path(__file__).resolve().parents[1] / 'data'))
        if not all((data_root / 'raw' / name).exists() for name in ('email-Eu-core-temporal.txt.gz', 'CollegeMsg.txt.gz')):
            self.skipTest('public SNAP inputs are not present; see data/DOWNLOAD_MANIFEST.json')
        for dataset in ('emailEU', 'CollegeMsg'):
            events, n, _ = load_development(dataset)
            events = events[:128]
            a, b = ReferencePaired(n), StandardParityDSU(n)
            replay = ReceiptReplay(events, n, True)
            for j, (_, u, v) in enumerate(events):
                one, two = a.add(u, v), b.add(u, v)
                self.assertEqual(one, two)
                replay.apply(j, {'task_id': j, 'owner': two[0], 'changes': two[1]})
                self.assertEqual(replay.owners, b.snapshot())

    def test_corrupt_receipts_rejected(self):
        events = [(0, 0, 1), (0, 2, 3), (1, 0, 2)]
        allocator = StandardParityDSU(4)
        payloads = []
        for j, (_, u, v) in enumerate(events):
            owner, changes = allocator.add(u, v)
            payloads.append({'task_id': j, 'owner': owner, 'changes': changes})
        self.assertTrue(payloads[-1]['changes'])
        for mutation in ('omit', 'old_owner', 'new_owner', 'duplicate'):
            rows = json.loads(json.dumps(payloads))
            if mutation == 'omit':
                rows[-1]['changes'] = []
            elif mutation == 'old_owner':
                rows[-1]['changes'][0][1] = 99
            elif mutation == 'new_owner':
                rows[-1]['changes'][0][2] = 99
            else:
                rows[-1]['changes'] *= 2
            replay = ReceiptReplay(events, 4, True)
            with self.assertRaises(AssertionError):
                for j, payload in enumerate(rows):
                    replay.apply(j, payload)

    def test_storage_conditional_updates_and_time_ties(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DurableIndex(Path(folder) / 'test.sqlite')
            store.write(0, (7, 0, 1), 0, [])
            store.write(1, (7, 2, 3), 2, [])
            store.commit()
            self.assertEqual(store.query(2, 7, 7, 1)['rows'], 2)
            with self.assertRaises(AssertionError):
                store.write(2, (8, 0, 2), 0, [(0, 99, 1)])
            store.connection.rollback()
            store.finish()


if __name__ == '__main__':
    unittest.main(verbosity=2)
