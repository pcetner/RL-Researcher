"""Trusted launch attestation and per-campaign deployment, outside worker access."""
import json
import subprocess
from pathlib import Path
from . import atomic


def verify(assessment):
    fingerprints = assessment.get('fingerprints',{})
    if not fingerprints:
        raise ValueError('Runtime source attestation is missing')
    for path, expected in fingerprints.items():
        if not Path(path).is_file() or atomic.digest(Path(path)) != expected:
            raise ValueError('Qualified source or evidence changed: '+path)


def install(broker,campaign):
    path = broker.directory/'research'/campaign/'deployment.json'
    if path.exists():
        return atomic.read_json(path)
    deployment = atomic.read_json(broker.directory/'deployment-template.json')
    if deployment.get('qualification'):
        raise ValueError('Qualification deployment cannot authorize overnight work')
    root = deployment['base']+'/worker-volume/campaigns/'+campaign
    code = '''import pathlib,shutil,sys
from rl_researcher import atomic,resources
root=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2])
resources.preflight(root,{'bounded_resources':True})
root.mkdir(parents=True,exist_ok=True)
target=root/'template'
if not target.exists(): shutil.copytree(source/'template',target)
'''
    subprocess.run(['wsl.exe','-d',deployment['distribution'],'--exec','env',
                    'PYTHONPATH='+deployment['base']+'/src/RL-Researcher',
                    deployment['base']+'/venv/bin/python','-c',code,root,deployment['root']],
                   capture_output=True,check=True,timeout=30)
    deployment['root']=root
    path.parent.mkdir(parents=True,exist_ok=True)
    atomic.write_json(path,deployment)
    return deployment


def host_preflight(baseline):
    if not baseline:
        raise ValueError('Host stability qualification is missing')
    script = '''$ErrorActionPreference='Stop'
$request=[Console]::In.ReadToEnd() | ConvertFrom-Json
Get-WinEvent -LogName System -MaxEvents 1 -ErrorAction Stop | Out-Null
$events=@(Get-WinEvent -FilterHashtable @{LogName='System';StartTime=[datetime]$request.since;Id=1001,4101} -ErrorAction SilentlyContinue | Where-Object {($_.Id -eq 4101 -and $_.ProviderName -eq 'Display') -or ($_.Id -eq 1001 -and $_.ProviderName -eq 'Microsoft-Windows-WER-SystemErrorReporting')} | Select-Object Id,ProviderName,TimeCreated,Message)
[ordered]@{drivers=@(Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceClass -eq 'DISPLAY' | Select-Object DeviceName,DriverVersion);graphics_errors=$events;build=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion' | Select-Object CurrentBuild,UBR)} | ConvertTo-Json -Depth 4
'''
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],
                          input=json.dumps(baseline).encode(),capture_output=True,timeout=30)
    if result.returncode:
        raise ValueError('Cannot verify Windows host stability before launch')
    observed=json.loads(result.stdout)
    actual={d['DeviceName']:d['DriverVersion'] for d in observed['drivers']}
    if actual!=baseline['drivers'] or observed['build']!=baseline['build']:
        raise ValueError('Windows or graphics drivers changed since host qualification: '+json.dumps({'expected':{'drivers':baseline['drivers'],'build':baseline['build']},'observed':observed}))
    if observed['graphics_errors']:
        raise ValueError('New Windows graphics reset or bugcheck requires investigation: '+json.dumps(observed['graphics_errors']))


def preflight(deployment):
    host_preflight(deployment.get('host_baseline'))
    code = '''import json,pathlib,sys
from rl_researcher import resources,atomic
resources.preflight(pathlib.Path(sys.argv[1]),{'bounded_resources':True})
for path,expected in json.loads(sys.argv[2]).items():
 if atomic.digest(pathlib.Path(path))!=expected: raise ValueError('Linux runtime changed: '+path)
with resources.compute_slot({'bounded_resources':True}): pass
'''
    result=subprocess.run(['wsl.exe','-d',deployment['distribution'],'--exec','env',
                    'PYTHONPATH='+deployment['base']+'/src/RL-Researcher',
                    deployment['base']+'/venv/bin/python','-c',code,deployment['root'],
                    json.dumps(deployment.get('remote_fingerprints',{}))],capture_output=True,timeout=30)
    if result.returncode:
        raise ValueError('Launch preflight failed: '+result.stderr.decode(errors='replace')[-2000:])
