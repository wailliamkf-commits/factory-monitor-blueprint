#!/usr/bin/env python3
"""Package clean committed sources; never models, installers or local field data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('dist'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip():
        raise SystemExit('Refusing a dirty checkout. Review and commit intended files first.')
    subprocess.run([sys.executable, str(root / 'scripts/verify_repository.py')], cwd=root, check=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?', version):
        raise SystemExit('Invalid version label')
    archive_data = subprocess.check_output(['git', 'archive', '--format=tar', 'HEAD'], cwd=root)
    payload = {}
    with tarfile.open(fileobj=io.BytesIO(archive_data)) as archive:
        for entry in archive:
            if entry.isdir():
                continue
            if not entry.isfile():
                raise SystemExit(f'Refusing non-regular payload: {entry.name}')
            payload[entry.name] = archive.extractfile(entry).read()
    manifest = {'schema_version': 1, 'version': version, 'repository_commit': commit,
                'scope': 'architecture, frozen sources, synthetic evidence; field NOT_TESTED',
                'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(payload.items())}}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    payload['PACKAGE_MANIFEST.json'] = manifest_bytes
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / f'FactoryMonitor-Blueprint-v{version}.zip'
    prefix = f'factory-monitor-blueprint-v{version}/'
    with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            item = zipfile.ZipInfo(prefix + name, date_time=(2026, 9, 25, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
            archive.writestr(item, data)
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(payload):
            raise SystemExit('ZIP integrity failure')
        for name, data in payload.items():
            if archive.read(prefix + name) != data:
                raise SystemExit(f'Readback failed: {name}')
    (output / 'PACKAGE_MANIFEST.json').write_bytes(manifest_bytes)
    files = [zip_path, output / 'PACKAGE_MANIFEST.json']
    (output / 'SHA256SUMS.txt').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in files), encoding='utf-8')
    print(json.dumps({'package': str(zip_path), 'bytes': zip_path.stat().st_size,
                      'files': len(payload), 'commit': commit, 'readback': 'PASS'}))


if __name__ == '__main__':
    main()
