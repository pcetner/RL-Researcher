"""Compare completed real-learning runs beyond counters: game frames and all saved learner state."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from rl_researcher import atomic, checkpoint


def compare(a, b, path='root'):
    if isinstance(a, torch.Tensor):
        return [] if isinstance(b, torch.Tensor) and a.dtype==b.dtype and torch.equal(a,b) else [path]
    if isinstance(a, dict):
        if not isinstance(b, dict) or a.keys()!=b.keys():
            return [path]
        return [failure for key in a for failure in compare(a[key], b[key], f'{path}.{key}')]
    if isinstance(a, (list,tuple)):
        if type(a) is not type(b) or len(a)!=len(b):
            return [path]
        return [failure for i in range(len(a)) for failure in compare(a[i],b[i],f'{path}[{i}]')]
    return [] if a==b else [path]


def load(run):
    directory = Path(run['directory'])
    manifest = atomic.read_json(directory/'manifest.json')
    record = next(iter(run['trials'].values()))['checkpoint']
    payload = checkpoint.validate(directory, manifest, record)
    # Never execute pickle supplied by a worker in the trusted comparison process.
    learner = torch.load(payload/'learning.pt', map_location='cpu', weights_only=True)
    with np.load(payload/'trajectory.npz', allow_pickle=False) as data:
        trajectory = {name:data[name].copy() for name in data.files}
    return learner, trajectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    report = atomic.read_json(args.report)
    assert report['kind']=='learning' and all(run.get('passed') for run in report['runs'])
    baseline, trajectory = load(report['runs'][0])
    findings = []
    for run in report['runs'][1:]:
        learner, observed = load(run)
        differences = compare(baseline, learner)
        # Registration fingerprint is shared. JSON metadata may contain transient timings;
        # compare the actual observations and rendered game frames independently.
        arrays = {key: np.array_equal(trajectory[key],observed[key]) for key in ('frames','observations')}
        history_differences = compare(json.loads(str(trajectory['metadata_json'])),
                                      json.loads(str(observed['metadata_json'])), 'trajectory')
        findings.append({'fault':run['fault'],'learner_differences':differences,
                         'trajectory_differences':history_differences,
                         'arrays_equal':arrays,'passed':not differences and not history_differences and all(arrays.values())})
    output = {'same_host_exact_continuation':all(f['passed'] for f in findings),
              'scope':'This engine, dependency set, seed, method, and bounded test only; not cross-platform determinism.',
              'comparisons':findings}
    atomic.write_json(args.report.parent/'continuation-comparison.json', output)
    print(json.dumps(output,indent=2))
    if not output['same_host_exact_continuation']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
