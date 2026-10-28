"""Descriptive repeat aggregation; no independence or superiority p-values."""
import csv
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent


def aggregate():
    records = [json.loads(p.read_text()) for p in sorted((ROOT / 'runs').glob('*/*/rep*/RESULT.json'))]
    groups = {}
    for result in records:
        assert result['audit']['pass']
        groups.setdefault((result['dataset'], result['method']), []).append(result)
    summary, table = [], []
    for (dataset, method), results in sorted(groups.items()):
        results.sort(key=lambda r: r['rep'])
        assert len(results) == 3, 'incomplete repetition set'
        assert len({r['audit']['trajectory_sha256'] for r in results}) == 1
        first = results[0]
        group = {'dataset': dataset, 'method': method, 'repetitions': len(results),
                 'events': first['events'], 'old_changes': first['old_changes'],
                 'old_changes_per_event': first['old_changes_per_event'],
                 'max_update_old_changes': first['max_update_old_changes'], 'fairness': first['fairness'],
                 'logical_owner_writes': first['logical_owner_writes'], 'journal_rows': first['journal_rows'],
                 'database_bytes': first['database_bytes'], 'journal_json_bytes': first['journal_json_bytes'],
                 'trajectory_sha256': first['audit']['trajectory_sha256'],
                 'window_queries_verified_per_run': first['audit']['window_queries_verified']}
        for key in ('end_to_end_seconds', 'read_prepare_seconds', 'init_seconds',
                    'algorithm_event_enumeration_seconds', 'serialize_sql_seconds', 'batch_commit_seconds',
                    'query_seconds', 'checkpoint_close_seconds', 'runtime_process_peak_rss_bytes',
                    'offline_audit_seconds'):
            values = [r[key] for r in results]
            group[key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
        for category in ('update_execution_including_triggered_queries', 'update_execution_excluding_periodic_query',
                         'batch_commit_latency'):
            group[category] = {}
            for key in ('p50_ms', 'p95_ms', 'p99_ms', 'max_ms'):
                values = [r[category][key] for r in results]
                group[category][key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
        table.append({'dataset': dataset, 'method': method, 'events': first['events'],
                      'max_window_error_tasks': first['fairness']['max_window_error'],
                      'max_prefix_error_tasks': first['fairness']['max_prefix_error'],
                      'old_changes': first['old_changes'], 'old_changes_per_event': first['old_changes_per_event'],
                      'max_update_old_changes': first['max_update_old_changes'],
                      'logical_owner_writes': first['logical_owner_writes'],
                      'e2e_median_s': group['end_to_end_seconds']['median'],
                      'e2e_min_s': group['end_to_end_seconds']['min'], 'e2e_max_s': group['end_to_end_seconds']['max'],
                      'p99_execution_median_ms': group['update_execution_including_triggered_queries']['p99_ms']['median'],
                      'max_execution_worst_ms': group['update_execution_including_triggered_queries']['max_ms']['max'],
                      'peak_rss_worst_MiB': group['runtime_process_peak_rss_bytes']['max'] / 2 ** 20,
                      'db_MiB': first['database_bytes'] / 2 ** 20, 'all_audits_pass': True})
        summary.append(group)
    assert len(summary) == 6
    comparisons = []
    for dataset in ('emailEU', 'CollegeMsg'):
        data = {g['method']: g for g in summary if g['dataset'] == dataset}
        assert data['ReferencePaired']['trajectory_sha256'] == data['StandardParityDSU']['trajectory_sha256']
        paired, greedy = data['StandardParityDSU'], data['DebtGreedy']
        comparisons.append({'dataset': dataset, 'same_trajectory_as_standard_reference': True,
                            'max_window_error_relative_reduction_vs_greedy': 1 - paired['fairness']['max_window_error'] / greedy['fairness']['max_window_error'],
                            'owner_write_multiplier_vs_greedy': paired['logical_owner_writes'] / greedy['logical_owner_writes'],
                            'e2e_time_multiplier_vs_greedy': paired['end_to_end_seconds']['median'] / greedy['end_to_end_seconds']['median'],
                            'standard_vs_original_e2e_ratio': paired['end_to_end_seconds']['median'] / data['ReferencePaired']['end_to_end_seconds']['median']})
    output = {'scope': 'development only; descriptive three serial isolated repeats; holdout unopened',
              'groups': summary, 'comparisons': comparisons,
              'not_claimed': ['new recoloring algorithm', 'same-contract superiority over greedy',
                              'deployment benefit', 'network/physical record migration cost', 'statistical significance']}
    (ROOT / 'SUMMARY.json').write_text(json.dumps(output, indent=2) + '\n')
    with (ROOT / 'COMPARISON.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print(json.dumps({'table': table, 'comparisons': comparisons}, indent=2), flush=True)


if __name__ == '__main__':
    aggregate()
