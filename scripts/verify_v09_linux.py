"""Native Linux verification launcher, avoiding WSL shell argument expansion."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    assert sys.platform=='linux' and sys.prefix!=sys.base_prefix
    root=Path(__file__).resolve().parents[1]
    wheel=root/'deliverables/scientific_figure_skill-0.9.0-py3-none-any.whl'
    subprocess.run([sys.executable,'-I','-m','pip','install','--no-index','--no-deps','--force-reinstall',str(wheel)],check=True)
    target=root/'outputs/implementation-v09-20261118/wheel-linux'
    assert not target.exists()
    stage=Path(tempfile.mkdtemp(prefix='figure09-native-'))
    # Do not delete failure evidence. Successful evidence is copied into the workspace.
    subprocess.run([sys.executable,'-I',str(root/'scripts/verify_v09_workflow.py'),'--require-wheel','--output',str(stage/'workflow')],check=True)
    shutil.copytree(stage/'workflow',target)
    print('Native Linux workflow verified; evidence copied to '+str(target),flush=True)

if __name__=='__main__':main()
