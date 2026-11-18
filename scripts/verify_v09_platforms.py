"""Install local 0.9 wheel into independent application prefixes on Windows/WSL.

Reuses installed dependencies. This is not an empty-environment offline dependency
installation. No package index, proxy modification or network is used.
"""
from pathlib import Path
import json
import subprocess
import sys
from scientific_figure.project import json_text,digest
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'outputs/implementation-v09-20261118'
WHEEL=ROOT/'deliverables/scientific_figure_skill-0.9.0-py3-none-any.whl'


def command(args,label):
    result=subprocess.run([str(a) for a in args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (RUN/f'{label}.stdout.txt').write_text(result.stdout,encoding='utf-8')
    (RUN/f'{label}.stderr.txt').write_text(result.stderr,encoding='utf-8')
    print(json.dumps({'step':label,'exit_code':result.returncode}),flush=True)
    if result.returncode:raise RuntimeError(f'{label} failed; inspect saved stdout/stderr')


def main():
    assert sys.platform=='win32' and WHEEL.is_file()
    if '--linux-only' not in sys.argv:
        env=ROOT/'.venv-wheel09';assert not env.exists()
        command([sys.executable,'-I','-m','venv','--system-site-packages',env],'windows-environment')
        python=env/'Scripts/python.exe'
        command([python,'-I','-m','pip','install','--no-index','--no-deps','--ignore-installed',WHEEL],'windows-install')
        command([python,'-I',ROOT/'scripts/verify_v09_workflow.py','--require-wheel','--output',RUN/'wheel-windows'],'windows-workflow')
    root_linux='/mnt/e/codex_WORKSPACES/Scientific Figure Reconstruction & Drawing'
    # Native Python uses argument arrays; no shell expands paths or variables.
    command(['wsl.exe','-d','Ubuntu-24.04','--',root_linux+'/.venv-linux07/bin/python','-I',root_linux+'/scripts/verify_v09_linux.py'],'linux-python-workflow')
    windows=json.loads((RUN/'wheel-windows/verification.json').read_text(encoding='utf-8'))
    linux=json.loads((RUN/'wheel-linux/verification.json').read_text(encoding='utf-8'))
    assert windows['status']==linux['status']=='passed'
    assert windows['installation']['wheel_sha256']==linux['installation']['wheel_sha256']==digest(WHEEL)
    proof={'status':'passed','wheel_sha256':digest(WHEEL),'windows':windows['installation'],'linux':linux['installation'],
        'dependency_installation':'Existing dependencies reused; application installed from local wheel with --no-index --no-deps.',
        'linux_filesystem':'Workflow executed under native /tmp, then evidence copied to workspace; no DrvFS atomic-rename claim.',
        'cross_platform_font_pixel_identity':'not_asserted','network_used':False}
    (RUN/'platform-verification.json').write_text(json_text(proof),encoding='utf-8')
    print(json.dumps(proof,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
