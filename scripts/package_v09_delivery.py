"""Build the local 0.9 delivery with explicit allowlists; never publish it.

Requires the completed workspace evidence, not just an extracted source archive.
Files are built and checked in a temporary staging directory before delivery.
"""
from pathlib import Path
import hashlib
import json
import tempfile
import zipfile

from scientific_figure import __version__
from scientific_figure.project import digest, json_text, load_project
from scientific_figure.reviews import current_project_review

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'outputs/implementation-v09-20261118'
OUT = ROOT / 'deliverables'
VERSION = '0.9.0'
FORBIDDEN = {'.ttf', '.otf', '.woff', '.woff2', '.dll', '.exe', '.pyc', '.pyd'}
NAMES = [
    f'scientific-figure-skill-{VERSION}.zip',
    f'scientific_figure_skill-{VERSION}-py3-none-any.whl',
    f'scientific-figure-examples-{VERSION}.zip',
    f'scientific-figure-private-reference-{VERSION}.zip',
    f'verification-{VERSION}.json',
    f'checksums-{VERSION}.json',
]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json_text(value), encoding='utf-8')


def tree(directory, prefix):
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    result = {}
    for path in directory.rglob('*'):
        if '__pycache__' in path.parts:
            continue
        if path.is_symlink():
            raise ValueError(f'Symlink excluded: {path}')
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN:
            raise ValueError(f'Excluded file type: {path}')
        result[(Path(prefix) / path.relative_to(directory)).as_posix()] = path
    return result


def record(path):
    return {'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)}


def archive(directory, name, members):
    path = directory / name
    manifest = {key: {'bytes': p.stat().st_size, 'sha256': digest(p)}
                for key, p in sorted(members.items())}
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as z:
        for key, p in sorted(members.items()):
            z.write(p, key)
        z.writestr('archive-manifest.json', json_text(manifest))
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(manifest) + 1
        assert read_manifest(z) == manifest
        for key, proof in manifest.items():
            content = z.read(key)
            assert len(content) == proof['bytes'], key
            assert hashlib.sha256(content).hexdigest() == proof['sha256'], key
    return {**record(path), 'files': len(manifest), 'crc_and_member_sha256': 'passed'}


def read_manifest(z):
    return json.loads(z.read('archive-manifest.json'))


def main():
    assert __version__ == VERSION
    for name in NAMES:
        if not name.endswith('.whl') and (OUT / name).exists():
            raise FileExistsError(OUT / name)
    platforms = read(RUN / 'platform-verification.json')
    assert platforms['status'] == 'passed'
    workflows = {os_name: read(RUN / f'wheel-{os_name}/verification.json')
                 for os_name in ['windows', 'linux']}
    for evidence in workflows.values():
        assert evidence['status'] == 'passed'
        assert evidence['installation']['version'] == VERSION
        assert evidence['installation']['application_files_matched'] == 74
    artwork = read(RUN / 'artwork-mechanical-verification.json')
    assert artwork['status'] == 'passed_for_declared_editable_schematic_scope'
    for task in ['reference-task', 'text-final']:
        project = RUN / task / 'project'
        assert current_project_review(load_project(project), project)['host_record_current']
    wheel = OUT / NAMES[1]
    assert digest(wheel) == platforms['wheel_sha256']
    files = [p for p in (ROOT / 'src/scientific_figure').rglob('*')
             if p.is_file() and '__pycache__' not in p.parts]
    with zipfile.ZipFile(wheel) as z:
        package_files = [key for key in z.namelist()
                         if key.startswith('scientific_figure/') and not key.endswith('/')]
        assert len(files) == len(package_files) == 74
        for p in files:
            key = 'scientific_figure/' + p.relative_to(ROOT / 'src/scientific_figure').as_posix()
            assert z.read(key) == p.read_bytes(), key
    pytest = (RUN / 'full-pytest.txt').read_text(encoding='utf-8')
    assert '969 passed, 5 skipped, 10 subtests passed in 64.55s' in pytest

    source = {}
    for directory in ['src/scientific_figure', 'tests', 'references', 'docs']:
        source.update(tree(ROOT / directory, directory))
    for name in ['README.md', 'SKILL.md', 'pyproject.toml', 'environment.yml',
                 'requirements-win-py312.lock.txt', 'THIRD_PARTY_NOTICES.md']:
        source[name] = ROOT / name
    for name in ['verify_v09_workflow.py', 'verify_v09_platforms.py',
                 'verify_v09_linux.py', 'draw_targeted_release_v09.py',
                 'package_v09_delivery.py']:
        source['scripts/' + name] = ROOT / 'scripts' / name
    assert not any(key.startswith(('outputs/', '.venv', 'research/', 'examples/')) for key in source)
    assert not any(p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'} for p in source.values())

    public = {**tree(RUN / 'text-final', 'text-designed-mechanism'),
              **tree(RUN / 'wheel-windows', 'wheel-windows'),
              **tree(RUN / 'wheel-linux', 'wheel-linux')}
    for name in ['verify_v09_workflow.py', 'draw_targeted_release_v09.py']:
        public['scripts/' + name] = ROOT / 'scripts' / name
    public['README.md'] = RUN / 'PUBLIC-README.md'
    public['THIRD_PARTY_NOTICES.md'] = ROOT / 'THIRD_PARTY_NOTICES.md'
    public['platform-verification.json'] = RUN / 'platform-verification.json'
    public['full-pytest.txt'] = RUN / 'full-pytest.txt'
    assert not any('original.webp' in key or key.startswith(('reference-task/', 'host-program/'))
                   for key in public)
    private = {}
    for name in ['reference-task', 'host-program', 'reference-candidate', 'reference-diff',
                 'artwork-bundle', 'artwork-composed', 'boundaries']:
        private.update(tree(RUN / name, name))
    for name in ['reference-fragments.json', 'reference-corrected-fragments.json',
                 'artwork-mechanical-verification.json', 'artwork-bundle-plan.json',
                 'artwork-composition-plan.json']:
        private[name] = RUN / name
    private['README.md'] = RUN / 'PRIVATE-README.md'
    private['delivery-notes.md'] = ROOT / 'docs/delivery-0.9.0.md'

    # Check actual selected paths and private reference image contents, not just ZIP names.
    private_root = (RUN / 'reference-task').resolve()
    private_image_hashes = {digest(p) for p in private_root.rglob('*')
                            if p.is_file() and p.suffix.lower() in {'.png', '.webp', '.svg'}}
    for key, p in {**source, **public}.items():
        assert not p.resolve().is_relative_to(private_root), key
        if p.suffix.lower() in {'.png', '.webp', '.svg'}:
            assert digest(p) not in private_image_hashes, key
    for members in [source, public, private]:
        for key, p in members.items():
            assert p.is_file() and not p.is_symlink(), key
            assert p.suffix.lower() not in FORBIDDEN, key

    verification = {
        'version': VERSION, 'date': '2026-11-18',
        'status': 'completed_with_declared_schematic_limits',
        'regression': {'passed': 969, 'skipped': 5, 'subtests_passed': 10,
                       'seconds': 64.55, 'skip_reason': 'Windows symlink privileges'},
        'platforms': platforms, 'public_workflows': workflows, 'artwork': artwork,
        'source_wheel_application_files_identical': 74,
        'console_entry_points': {'windows': 'doctor 0.9.0 passed', 'linux': 'doctor 0.9.0 passed'},
        'preserved_failure': 'First WSL shell launcher failed before Linux tests. Native Python launcher passed; original logs retained locally.',
        'scientific_review': 'not_executed', 'external_artwork_used_in_two_drawings': [],
        'private_public_separate': True, 'private_reference_image_hash_scan': 'passed',
        'system_fonts_environments_weights_distributed': False, 'git_publish_deploy': False,
    }
    with tempfile.TemporaryDirectory(prefix='.stage-v09-', dir=OUT) as temporary:
        stage = Path(temporary)
        source_record = archive(stage, NAMES[0], source)
        public_record = archive(stage, NAMES[2], public)
        private_record = archive(stage, NAMES[3], private)
        with zipfile.ZipFile(stage / NAMES[0]) as z:
            assert z.read('SKILL.md') == (ROOT / 'SKILL.md').read_bytes()
            for p in files:
                assert z.read(p.relative_to(ROOT).as_posix()) == p.read_bytes()
        verification['archive_checks'] = [source_record, public_record, private_record]
        save(stage / NAMES[4], verification)
        records = [source_record, record(wheel), public_record, private_record,
                   record(stage / NAMES[4])]
        save(stage / NAMES[5], {'algorithm': 'SHA-256', 'signature': False, 'files': records})
        for name in NAMES:
            if name.endswith('.whl'):
                continue
            (stage / name).rename(OUT / name)
    proof = {'status': 'packaged', 'files': records, 'checksums': str(OUT / NAMES[5])}
    save(RUN / 'delivery.json', proof)
    print(json.dumps(proof, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
