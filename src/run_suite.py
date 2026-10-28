"""Serial isolated workers; never terminates another task to obtain timings."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from allocators import ROOT, METHODS


def active_compute():
    result = subprocess.run(['ps', '-Ao', 'pid=,pcpu=,command='], capture_output=True, text=True, check=True)
    heavy = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 3:
            continue
        pid, cpu, command = int(fields[0]), float(fields[1]), fields[2]
        if pid != os.getpid() and cpu >= 60 and ('python' in command.lower() or cpu >= 95):
            heavy.append({'pid': pid, 'cpu_percent': cpu, 'command': command})
    return heavy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('smoke', 'functional', 'isolated'), default='smoke')
    args = parser.parse_args()
    gates = json.loads((ROOT / 'GATE_RESULT.json').read_text())
    assert gates['pass']
    for name, sha in gates['code_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, f'code changed after gate: {name}'
    repetitions = 3 if args.mode == 'isolated' else 1
    log_root = ROOT / ('smoke' if args.mode == 'smoke' else 'runs')
    preflights, completed = [], []
    for dataset in ('emailEU', 'CollegeMsg'):
        for rep in range(repetitions):
            order = METHODS[rep:] + METHODS[:rep]
            for method in order:
                heavy = active_compute()
                preflights.append({'utc': datetime.now(timezone.utc).isoformat(), 'dataset': dataset,
                                   'method': method, 'rep': rep, 'other_heavy_processes': heavy})
                (ROOT / f'PREFLIGHT_{args.mode}.json').write_text(json.dumps(preflights, indent=2) + '\n')
                if args.mode == 'isolated' and heavy:
                    raise RuntimeError(f'formal timing deferred because another computation is active: {heavy}')
                out = log_root / dataset / method / f'rep{rep}'
                if out.exists():
                    raise RuntimeError(f'refusing to overwrite run: {out}')
                out.mkdir(parents=True)
                command = [sys.executable, str(ROOT / 'run_e2e.py'), '--dataset', dataset, '--method', method,
                           '--rep', str(rep), '--out', str(out), '--timing-class',
                           'isolated_development_repeats' if args.mode == 'isolated' else 'functional_only']
                if args.mode == 'smoke':
                    command.extend(['--limit', '128'])
                started = time.monotonic()
                result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                (out / 'STDOUT.txt').write_text(result.stdout)
                (out / 'STDERR.txt').write_text(result.stderr)
                (out / 'EXIT.json').write_text(json.dumps({'exit_code': result.returncode,
                                                           'worker_total_seconds_including_audit': time.monotonic() - started,
                                                           'command': command}, indent=2) + '\n')
                print(result.stdout.strip(), flush=True)
                if result.returncode:
                    raise RuntimeError(f'worker failed: {out}; {result.stderr}')
                completed.append(json.loads((out / 'RESULT.json').read_text()))
                if method in ('ReferencePaired', 'StandardParityDSU'):
                    matched = [r for r in completed if r['dataset'] == dataset and r['method'] != method
                               and r['method'] in ('ReferencePaired', 'StandardParityDSU')]
                    for other in matched:
                        assert other['audit']['trajectory_sha256'] == completed[-1]['audit']['trajectory_sha256']
    (ROOT / f'SUITE_{args.mode}.json').write_text(json.dumps({'pass': True, 'workers': len(completed),
                                                           'mode': args.mode}, indent=2) + '\n')


if __name__ == '__main__':
    main()
