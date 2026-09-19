"""Explicit real-process acceptance harness; run inside the prepared WSL environment.

These bounded runs qualify plumbing and numerical continuation. They do not make
behavioral-improvement or first-star claims. Every run and failed check is retained.
"""
import argparse
import json
import os
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

from rl_researcher import atomic, checkpoint, experiment, runner, runlog


def wait(directory, predicate, seconds=240, minimum_attempt=1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = runner.state(directory)
        if predicate(state):
            return state
        if state['attempt'] >= minimum_attempt and state['state'] in {'Failed', 'Incomplete', 'Stopped'}:
            raise AssertionError(json.dumps(state))
        time.sleep(.1)
    raise TimeoutError(str(directory))


def alive(pid):
    path = Path(f'/proc/{pid}/stat')
    return path.exists() and path.read_text().split(') ')[1][0] != 'Z'


def descendants(pid):
    found = {pid}
    for _ in range(20):
        previous = set(found)
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                parts = path.read_text().split(') ')[1].split()
                if int(parts[1]) in found:
                    found.add(int(path.parent.name))
            except FileNotFoundError:
                pass
        if found == previous:
            break
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['deterministic','boot','learning'], required=True)
    parser.add_argument('--start-hash')
    args = parser.parse_args()
    base = Path.home()/'haws-integration'
    root = base/'acceptance'/(time.strftime('%Y%m%d-%H%M%S')+'-'+args.kind)
    root.mkdir(parents=True)
    if args.kind == 'deterministic':
        for source in (base/'src/RL-Researcher/examples/deterministic').iterdir():
            if source.is_file():
                shutil.copy2(source, root/source.name)
        d = atomic.read_json(root/'definition.json')
        d['trials'] = [dict(d['trials'][0], decisions=60, delay=.06, seconds=120)]
        identifier = 'deterministic'
        atomic.write_json(root/'definition.json', d)
    else:
        shutil.copytree(base/'src/Auto-SM64/python', root/'python')
        shutil.copytree(base/'src/Auto-SM64/configs', root/'configs')
        d = atomic.read_json(base/'src/Auto-SM64/experiments/from-scratch-check.json')
        d.update(name='WSL real learning recovery acceptance',
                 question='Does interrupted online learning continue from the same game and learning state?',
                 setup='16 decisions, chunks of 8, two gradient updates. User-authorized menu/intro-only castle initialization.',
                 castle_start_sha256=args.start_hash)
        d['trials'] = [dict(d['trials'][0], decisions=16, chunk=8, updates=2, seconds=240, deterministic=True)]
        if args.kind == 'boot':
            d.update(executor='python/autosm64/research/runtime_probe.py:execute',
                     resolver='python/autosm64/research/runtime_probe.py:resolve', finalizer=None)
        for key, filename in [('environment', root/'configs/castle_k20.toml'), ('engine', base/'build/build/us_pc/sm64.us')]:
            d['inputs'][key] = {'path':str(filename), 'sha256':atomic.digest(filename)}
        identifier = 'learning'
        atomic.write_json(root/'definition.json', d)
        atomic.write_json(root/'research.json', {'experiments':[{'id':identifier,'definition':'definition.json'}], 'history':[]})
    d['limits']['overall_seconds'] = 600
    atomic.write_json(root/'definition.json', d)
    resolved = experiment.validate(root, identifier)
    policy = {'kind':'bubblewrap-v1', 'gpu':args.kind!='deterministic',
              'runtime_roots':[str(base/'venv'), str(Path(sys.base_prefix).parent)]}
    results = {'root':str(root), 'kind':args.kind, 'revision':resolved['revision'], 'runs':[], 'checks':{}}
    atomic.write_json(root/'acceptance.json', results)
    for fault in (['none'] if args.kind=='boot' else ['none','stop','supervisor_kill','worker_kill']):
        identity = runner.start(root, identifier, resolved['revision'], uuid.uuid4().hex, isolation=policy)
        directory = root/'.research/executions'/identity
        entry = {'id':identity,'fault':fault,'directory':str(directory)}
        results['runs'].append(entry)
        atomic.write_json(root/'acceptance.json', results)
        try:
            if fault != 'none':
                checked = {}
                def checkpoint_ready(state):
                    for trial_state in state['trials'].values():
                        point = trial_state.get('checkpoint')
                        if not point:
                            continue
                        if args.kind=='deterministic':
                            return point['decision']>=10
                        # A recovery test must restore learned optimizer state, not just an
                        # initialization checkpoint written before the first gradient step.
                        if point['path'] not in checked:
                            import torch
                            payload = checkpoint.validate(directory, atomic.read_json(directory/'manifest.json'), point)
                            learned = torch.load(payload/'learning.pt',map_location='cpu',weights_only=True)
                            checked[point['path']] = learned['state']['trained_updates']>=2 and bool(learned['optimizer']['state'])
                        if checked[point['path']]:
                            return True
                    return False
                state = wait(directory, checkpoint_ready)
                saved = next(t['checkpoint'] for t in state['trials'].values() if t.get('checkpoint'))
                entry['checkpoint_before_fault'] = saved
                entry['fault_boundary'] = 'committed trained model and nonempty optimizer' if args.kind=='learning' else 'committed sum'
                if fault == 'stop':
                    runner.request_stop(directory)
                    wait(directory, lambda s:s['state']=='Stopped')
                else:
                    pid = atomic.read_json(directory/'supervisor.json')['pid'] if fault=='supervisor_kill' else state['worker_pid']
                    # PID came from this just-launched run; command line must bind it to the directory.
                    assert str(directory).encode() in Path(f'/proc/{pid}/cmdline').read_bytes()
                    owned_tree = descendants(state['worker_pid'])
                    os.kill(pid, signal.SIGKILL)
                    worker = state['worker_pid']
                    end = time.monotonic()+10
                    while alive(worker) and time.monotonic()<end:
                        time.sleep(.1)
                    assert not alive(worker), 'Worker survived supervisor death'
                    end = time.monotonic()+10
                    while any(alive(child) for child in owned_tree) and time.monotonic()<end:
                        time.sleep(.1)
                    assert not any(alive(child) for child in owned_tree), 'Descendant survived worker teardown'
                    entry['confirmed_terminated_processes'] = sorted(owned_tree)
                    if fault=='supervisor_kill':
                        runner.reconcile(directory)
                    else:
                        wait(directory, lambda s:s['state']=='Failed')
                # Construct state afresh, as an application reconnect would, before resuming.
                before = runner.state(directory)
                checkpoint.validate(directory, atomic.read_json(directory/'manifest.json'), saved)
                entry['interrupted_state'] = before['state']
                runner.resume(directory)
            final = wait(directory, lambda s:s['state']=='Completed', minimum_attempt=1 if fault=='none' else 2)
            entry.update(state=final['state'], attempt=final['attempt'], trials=final['trials'], spent_seconds=final['spent_seconds'])
            recovered = [e for e in runlog.events(directory) if e['kind']=='trial' and e['data'].get('recovered')]
            assert bool(recovered) == (fault!='none')
            if args.kind=='deterministic':
                assert all(t['result']['measurements']['sum']==1830 for t in final['trials'].values())
            elif args.kind=='learning':
                assert all(t['result']['decisions']==16 and t['result']['trained_updates']==2 for t in final['trials'].values())
            entry['passed'] = True
        except Exception as error:
            entry.update(passed=False, error=str(error))
            atomic.write_json(root/'acceptance.json', results)
            print(json.dumps({'root':str(root),'failed':entry}),flush=True)
            raise
        atomic.write_json(root/'acceptance.json', results)
        print(json.dumps({'fault':fault,'passed':True,'id':identity}),flush=True)
    results['checks']['all_fault_scenarios'] = True
    atomic.write_json(root/'acceptance.json', results)
    print(json.dumps({'report':str(root/'acceptance.json')}))


if __name__ == '__main__':
    main()
