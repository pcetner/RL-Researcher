"""Trusted execution transport; authority always remains in the campaign store."""
import time
import uuid

from . import atomic, checkpoint, config, runner
from .wsl_control import WslRunner


class Execution:
    def __init__(self, root, manifest):
        self.root, self.manifest = root, manifest
        self.remote = manifest.get('execution_backend')
        if self.remote:
            if self.remote.get('kind') != 'wsl':
                raise ValueError('Unsupported campaign execution backend')
            self.client = WslRunner(self.remote['distribution'], self.remote['base'])

    def call(self, operation, request_id=None, **inputs):
        return self.client.call(self.remote['root'], operation, request_id, **inputs)

    def start(self, request, authorization):
        if self.remote:
            return self.call('start',request, experiment=self.manifest['experiment'],
                             revision=self.manifest['revision'], isolation=self.remote['isolation'],
                             authorization=authorization)
        return runner.start(self.root,self.manifest['experiment'],self.manifest['revision'],request,
                            authorization=authorization)

    def find(self, request):
        if self.remote:
            return self.call('find',dispatch_request=request)
        return [p.name for p in config.store(self.root).iterdir()
                if (p/'manifest.json').exists() and atomic.read_json(p/'manifest.json').get('request_id')==request]

    def snapshot(self, identity):
        if self.remote:
            self.call('reconcile',uuid.uuid4().hex,execution=identity)
            return self.call('snapshot',execution=identity)
        directory = config.store(self.root)/identity
        state = runner.state(directory)
        manifest = atomic.read_json(directory/'manifest.json')
        points = []
        for trial in state['trials'].values():
            point = trial.get('checkpoint')
            if point:
                checkpoint.validate(directory,manifest,point)
                points.append(point)
        return {'state':state,'checkpoints':points,'directory':str(directory)}

    def stop(self, identity, request, force=False):
        if self.remote:
            return self.call('stop',request,execution=identity,force=force)
        return runner.request_stop(config.store(self.root)/identity,force)

    def resume(self, identity, request, authorization):
        if self.remote:
            return self.call('resume',request,execution=identity,authorization=authorization)
        runner.resume(config.store(self.root)/identity,authorization=authorization)


def authorization(campaign, job):
    if job['deadline'] <= time.time():
        raise ValueError('Dispatch authorization expired before execution')
    return {'campaign':campaign,'job':job['id'],'attempt':job['attempts'][-1]['id'],
            'deadline':job['deadline'],'session':job['session']}
