"""Complete timestamp boundaries versus arbitrary event-index boundaries."""
import itertools
import json
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parent


def grouped_window_error(rows):
    delta, low, high = {}, {}, {}
    for _, group in itertools.groupby(rows, key=lambda r: r[0]):
        touched = set()
        for _, u, v, owner in group:
            delta[u] = delta.get(u, 0) - 1
            delta[v] = delta.get(v, 0) - 1
            delta[owner] += 2
            touched.update((u, v))
        for p in touched:
            low[p] = min(low.get(p, 0), delta[p])
            high[p] = max(high.get(p, 0), delta[p])
    return max((high[p] - low[p] for p in delta), default=0) / 2


def verify_formula():
    for seed in range(40):
        rng = random.Random(103 + seed)
        rows = []
        for i in range(12):
            u, v = sorted(rng.sample(range(5), 2))
            rows.append((i // 3, u, v, rng.choice((u, v))))
        exact = 0
        for lo in range(4):
            for hi in range(lo, 4):
                for p in range(5):
                    selected = [r for r in rows if lo <= r[0] <= hi]
                    error = abs(sum(r[3] == p for r in selected) - sum(p in r[1:3] for r in selected) / 2)
                    exact = max(exact, error)
        assert grouped_window_error(rows) == exact


def main():
    verify_formula()
    results = []
    for dataset in ('emailEU', 'CollegeMsg'):
        for method in ('ReferencePaired', 'StandardParityDSU', 'DebtGreedy'):
            folder = ROOT / 'runs' / dataset / method / 'rep0'
            connection = sqlite3.connect(f'file:{folder / "index.sqlite"}?mode=ro', uri=True)
            rows = connection.execute('SELECT timestamp,u,v,owner FROM records ORDER BY timestamp,task_id')
            exact = grouped_window_error(rows)
            connection.close()
            result = json.loads((folder / 'RESULT.json').read_text())
            arbitrary = result['fairness']['max_window_error']
            assert exact <= arbitrary
            results.append({'dataset': dataset, 'method': method, 'max_event_index_interval_error': arbitrary,
                            'max_closed_timestamp_window_error': exact})
    out = {'formula_random_bruteforce_tests': 40, 'tests_pass': True,
           'scope': 'final current ownership, exact all closed timestamp windows; development only',
           'results': results}
    (ROOT / 'TIMESTAMP_WINDOW_AUDIT.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
