"""Trusted deployment resource boundary; never mounted into research workers."""
import contextlib
import os
import shutil
from pathlib import Path

from . import lock

POOL = Path('/home/peter_cetner/haws-integration/worker-volume')
CGROUP = Path('/sys/fs/cgroup/haws-workers')
SCRATCH = Path('/home/peter_cetner/haws-integration/worker-scratch')


class ResourceUnavailable(ValueError):
    def __init__(self,message,facts):
        super().__init__(message)
        self.facts=facts


def storage_measurement(volume=POOL,required_bytes=0,reserve=128*1024**2):
    measured=shutil.disk_usage(volume)
    return {'resource':'artifact_bytes','volume':str(volume),'physical':True,
            'limit':measured.total,'measured_total':measured.used,
            'remaining_capacity':measured.free,'protected_reserve':reserve,
            'required_bytes':required_bytes}


def preflight(directory, policy):
    if not policy or not policy.get('bounded_resources'):
        return
    directory = Path(directory).resolve()
    if not POOL.is_mount() or not directory.is_relative_to(POOL):
        raise ValueError('Bounded worker volume is not mounted or execution is outside it')
    if not SCRATCH.is_mount() or shutil.disk_usage(SCRATCH).total > 1024**3:
        raise ValueError('Bounded writable worker scratch space is unavailable')
    if shutil.disk_usage(POOL).total > 4*1024**3:
        raise ValueError('Worker volume exceeds its approved capacity')
    if shutil.disk_usage('/mnt/c').free < 10*1024**3:
        raise ResourceUnavailable('Physical host free-space reserve reached',storage_measurement('/mnt/c',reserve=10*1024**3))
    if shutil.disk_usage(POOL).free < 128*1024**2:
        raise ResourceUnavailable('Artifact volume reached its protected metadata reserve',storage_measurement())
    if int((CGROUP/'memory.max').read_text()) > 6*1024**3:
        raise ValueError('Worker memory limit is not established')
    if int((CGROUP/'pids.max').read_text()) > 256:
        raise ValueError('Worker process limit is not established')
    if (CGROUP/'cpu.max').read_text().strip() != '400000 100000':
        raise ValueError('Worker CPU limit is not established')
    if not os.access(CGROUP/'cgroup.procs',os.W_OK) or not os.access(CGROUP.parent/'cgroup.procs',os.W_OK):
        raise ValueError('Trusted launcher cannot enter worker resource cgroup')


@contextlib.contextmanager
def compute_slot(policy):
    if os.name != 'posix' or not policy:
        yield
        return
    with lock.exclusive(Path.home()/'.local/state/haws/compute.lock'):
        if policy.get('bounded_resources') and (CGROUP/'cgroup.procs').read_text().strip():
            raise ValueError('Prior bounded worker processes remain; reconcile before dispatch')
        yield


def retained_bytes(directory):
    return sum(p.stat().st_size for p in Path(directory).rglob('*') if p.is_file() and not p.is_symlink())


def work_source(directory, source):
    from . import atomic
    directory = Path(directory).resolve()
    source = Path(source).resolve()
    manifest = atomic.read_json(directory/'manifest.json')
    if not (manifest.get('isolation') or {}).get('bounded_resources'):
        return source
    for relative in ['runtime-work','finalization',*[f"trials/{t['id']}/work" for t in manifest['definition']['trials']]]:
        prefix = directory/relative
        if source.is_relative_to(prefix):
            allowed = SCRATCH/directory.name/relative
            mapped = (allowed/source.relative_to(prefix)).resolve()
            if not mapped.is_relative_to(allowed):
                raise ValueError('Worker publication escaped its scratch directory')
            return mapped
    raise ValueError('Worker publication must originate in its writable scratch directory')


def publication_space(directory, size):
    from . import atomic
    manifest = atomic.read_json(Path(directory)/'manifest.json')
    if (manifest.get('isolation') or {}).get('bounded_resources'):
        if shutil.disk_usage(POOL).free-size < 128*1024**2:
            raise ResourceUnavailable('Publication would consume protected artifact reserve',storage_measurement(required_bytes=size))


def cleanup_work(directory, policy):
    if not policy or not policy.get('bounded_resources'):
        return
    target = (SCRATCH/Path(directory).name).resolve()
    if target.parent != SCRATCH or (CGROUP/'cgroup.procs').read_text().strip():
        raise ValueError('Cannot release scratch before confirmed worker termination')
    if target.exists():
        shutil.rmtree(target)  # Only disposable working copies; committed evidence is on POOL.
