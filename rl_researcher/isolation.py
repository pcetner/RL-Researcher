"""Trusted Linux worker launch policy. Never derive mounts from worker messages."""
import os
from pathlib import Path


def worker_command(directory, manifest):
    if os.name != 'posix':
        raise ValueError('The bubblewrap worker must be supervised inside Linux')
    directory = Path(directory).resolve()
    policy = manifest['isolation']
    if policy.get('kind') != 'bubblewrap-v1':
        raise ValueError('Unsupported worker boundary')
    argv = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--size', '268435456', '--tmpfs', '/tmp',
            '--ro-bind', str(directory), str(directory),
            '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
            '--setenv', 'PYTHONPATH', str(directory/'source/toolkit'),
            '--setenv', 'PYTHONUNBUFFERED', '1', '--setenv', 'LIBGL_ALWAYS_SOFTWARE', '1',
            '--setenv', 'SDL_AUDIODRIVER', 'dummy', '--setenv', 'SM64_RL_PIPE_DIR', '/tmp']
    for source in policy.get('runtime_roots', []):
        path = Path(source).resolve(strict=True)
        if path == Path('/') or path == Path.home() or str(path).startswith('/mnt/'):
            raise ValueError('Runtime mount is too broad or exposes a Windows mount')
        argv += ['--ro-bind', str(path), str(path)]
    for item in manifest['inputs'].values():
        path = Path(item['path']).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Only individual approved input files may be mounted')
        argv += ['--ro-bind', str(path), str(path)]
    for path in [directory/'runtime-work', directory/'finalization', *[directory/'trials'/t['id']/'work' for t in manifest['definition']['trials']]]:
        path.mkdir(parents=True, exist_ok=True)
        source = path
        if policy.get('bounded_resources'):
            from .resources import SCRATCH
            source = SCRATCH/directory.name/path.relative_to(directory)
            source.mkdir(parents=True,exist_ok=True)
        argv += ['--bind', str(source), str(path)]
    # Xvfb/llvmpipe needs the font configuration; it contains no project/user inputs.
    if Path('/etc/fonts').exists():
        argv += ['--ro-bind', '/etc/fonts', '/etc/fonts']
    if policy.get('gpu'):
        argv += ['--dev-bind', '/dev/dxg', '/dev/dxg', '--setenv', 'LD_LIBRARY_PATH', '/usr/lib/wsl/lib']
    argv += ['--chdir', str(directory/'runtime-work'), manifest['python'], '-m', 'rl_researcher.worker', str(directory), 'stdio', '-']
    if policy.get('bounded_resources'):
        return [manifest['python'],'-m','rl_researcher.resource_exec',*argv]
    return argv
