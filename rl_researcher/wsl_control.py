"""Trusted Windows-to-WSL control transport for the isolated integration deployment.

Not an untrusted worker endpoint. WSL owns processes; losing the Windows caller
does not imply cancellation. Retry identity is checked before any new dispatch.
"""
import hashlib
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import atomic, checkpoint, config, experiment, lock, resources, runner


class WslRunner:
    def __init__(self, distribution='Ubuntu', base='/home/peter_cetner/haws-integration'):
        self.distribution, self.base = distribution, base

    def call(self, root, operation, request_id=None, **parameters):
        envelope = dict(root=root, operation=operation, request_id=request_id, **parameters)
        command = ['wsl.exe','-d',self.distribution,'--exec','env',
                   f'PYTHONPATH={self.base}/src/RL-Researcher',f'{self.base}/venv/bin/python',
                   '-m','rl_researcher.wsl_control']
        reply = subprocess.run(command, input=json.dumps(envelope).encode(), capture_output=True,
                               timeout=30, check=False)
        if reply.returncode:
            raise RuntimeError(reply.stderr.decode(errors='replace'))
        value = json.loads(reply.stdout)
        if not value.get('ok'):
            if value.get('resource_fault'):raise resources.ResourceUnavailable(value['error'],value['resource_fault'])
            raise ValueError(value.get('error','Remote operation failed'))
        return value['result']


def handle(envelope):
    root = Path(envelope['root']).resolve(strict=True)
    allowed = Path.home()/'haws-integration/acceptance'
    production = resources.POOL/'campaigns'
    if not ((root.is_relative_to(allowed) and root != allowed) or
            (root.is_relative_to(production) and root != production)):
        raise ValueError('Root is outside the registered integration deployment')
    operation = envelope['operation']
    directory = None
    if 'execution' in envelope:
        identity = envelope['execution']
        if not re.fullmatch('[a-zA-Z0-9_-]+',identity):
            raise ValueError('Invalid execution identity')
        directory = root/'.research/executions'/identity
    if operation == 'storage_measurement':
        return resources.storage_measurement(required_bytes=envelope.get('required_bytes',0))
    if operation == 'inspect':
        return runner.state(directory)
    if operation == 'find':
        return [p.name for p in config.store(root).iterdir()
                if (p/'manifest.json').exists()
                and atomic.read_json(p/'manifest.json').get('request_id') == envelope['dispatch_request']]
    if operation == 'snapshot':
        # Checkpoints are verified by the trusted Linux broker, never by worker assertions.
        manifest = atomic.read_json(directory/'manifest.json')
        observed = runner.state(directory)
        points = []
        for trial in observed['trials'].values():
            point = trial.get('checkpoint')
            if point:
                checkpoint.validate(directory, manifest, point)
                points.append(point)
        preview = None
        sample = observed.get('sample') or {}
        reference = sample.get('preview')
        if reference:
            path = (directory/reference['path']).resolve()
            if (path.is_relative_to((directory/'artifacts').resolve()) and path.is_file()
                    and path.stat().st_size<=512*1024 and atomic.digest(path)==reference['sha256']):
                data = path.read_bytes()
                if data.startswith(b'\x89PNG\r\n\x1a\n'):
                    preview = 'data:image/png;base64,'+base64.b64encode(data).decode()
        return {'state':observed,'checkpoints':points,'directory':str(directory),
                'retained_bytes':resources.retained_bytes(directory),'preview':preview}
    if operation == 'inspect_definition':
        return experiment.inspect(root,envelope['experiment'])
    request = envelope.get('request_id')
    if not isinstance(request,str) or not request:
        raise ValueError('Mutation requires a stable request identity')
    fingerprint = hashlib.sha256(json.dumps(envelope,sort_keys=True).encode()).hexdigest()
    receipt = root/'.research/remote-requests'/(hashlib.sha256(request.encode()).hexdigest()+'.json')
    with lock.exclusive(root/'.research/remote.lock'):
        if receipt.exists():
            previous = atomic.read_json(receipt)
            if previous['fingerprint'] != fingerprint:
                raise ValueError('Request identity reused with changed inputs')
            if previous['status']=='completed':
                return previous['result']
            if operation!='start':
                raise ValueError('Previous operation requires reconciliation; no automatic repeat')
        else:
            atomic.write_json(receipt, {'fingerprint':fingerprint,'status':'pending'})
        if operation=='validate':
            result = experiment.validate(root,envelope['experiment'],envelope.get('isolation'))
        elif operation=='prepare_package':
            from .packages import prepare
            result = prepare(root,request,envelope['proposal'],envelope['isolation'])
        elif operation=='start':
            result = runner.start(root,envelope['experiment'],envelope['revision'],request,
                                  isolation=envelope['isolation'], authorization=envelope.get('authorization'))
        elif operation=='stop':
            runner.request_stop(directory, envelope.get('force',False))
            result = {'requested':True}
        elif operation=='resume':
            runner.resume(directory, authorization=envelope.get('authorization'))
            result = {'execution':directory.name,'requested':True}
        elif operation=='reconcile':
            supervisor_receipt = directory/'supervisor.json'
            if supervisor_receipt.exists() and time.time()-atomic.read_json(supervisor_receipt)['started'] >= 2:
                try:
                    runner.reconcile(directory)
                except BlockingIOError:
                    pass  # A live supervisor still owns its journal.
            try:checkpoint.recover_committed(directory)
            except lock.LockBusy:pass
            result = runner.state(directory)
        else:
            raise ValueError('Unknown remote operation')
        atomic.write_json(receipt, {'fingerprint':fingerprint,'status':'completed','result':result})
        return result


def main():
    try:
        payload = sys.stdin.buffer.read(1024*1024+1)
        if len(payload)>1024*1024:
            raise ValueError('Control envelope exceeds limit')
        print(json.dumps({'ok':True,'result':handle(json.loads(payload))}))
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error),'resource_fault':error.facts if isinstance(error,resources.ResourceUnavailable) else None}))


if __name__ == '__main__':
    main()
