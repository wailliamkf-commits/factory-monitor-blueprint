#!/usr/bin/env python3
"""Read-only checks of source identity, handoff documents, links and payloads."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    '00-quickstart.md', '01-architecture.md', '02-windows-execution.md',
    '03-macos-execution.md', '04-client-calibration.md',
    '05-validation-and-acceptance.md', '06-operations-and-recovery.md',
    '07-roadmap-and-capacity.md', '08-sources-and-evidence.md', '09-delivery-and-offline.md', '10-client-learning.md',
)


def safe_path(relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or '\\' in relative:
        raise ValueError(f'unsafe path: {relative}')
    target = ROOT / path
    if target.is_symlink() or not target.resolve().is_relative_to(ROOT):
        raise ValueError(f'path leaves repository: {relative}')
    return target


def main():
    errors = []
    manifest = json.loads((ROOT / 'evidence/implementation-manifest.json').read_text(encoding='utf-8'))
    expected = manifest['files']
    for relative, digest in expected.items():
        path = safe_path('implementation/' + relative)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f'source snapshot mismatch: {relative}')

    for name in REQUIRED_DOCS:
        if not (ROOT / 'docs' / name).is_file():
            errors.append(f'missing required guide: docs/{name}')
    docs = [ROOT / 'README.md', ROOT / 'AGENTS.md', *sorted((ROOT / 'docs').glob('*.md')),
            *sorted((ROOT / 'evidence').rglob('README.md')), ROOT / 'resources/README.md']
    links_checked = 0
    for doc in docs:
        if not doc.is_file():
            errors.append(f'missing document: {doc.relative_to(ROOT)}')
            continue
        content = doc.read_text(encoding='utf-8')
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', content):
            target = target.strip().strip('<>')
            parts = urlsplit(target)
            if parts.scheme or target.startswith('#'):
                continue
            target_path = (doc.parent / unquote(parts.path)).resolve()
            links_checked += 1
            if not target_path.is_relative_to(ROOT) or not target_path.exists():
                errors.append(f'broken local link: {doc.relative_to(ROOT)} -> {target}')

    for name in ('field-run.template.json', 'client-capability.template.json', 'software-adaptation.template.json', 'seetong-screen-first.template.json'):
        path = ROOT / 'templates' / name
        if not path.is_file():
            errors.append(f'missing template: {name}')
        else:
            try:
                json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError) as error:
                errors.append(f'invalid template JSON: {name}: {error}')

    # Git checkout: examine only indexed paths, never print secret contents.
    if (ROOT / '.git').exists():
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        tracked = [p for p in tracked if p]
        snapshot_paths = {p.removeprefix('implementation/') for p in tracked if p.startswith('implementation/')}
        if snapshot_paths != set(expected):
            errors.append('indexed implementation file set differs from frozen manifest')
        forbidden_parts = {'.venv', 'node_modules', '__pycache__', 'models', '.tools', 'field-data', 'local-config'}
        forbidden_suffixes = {'.zip', '.exe', '.dll', '.whl', '.pt', '.safetensors', '.mp4', '.pem', '.key'}
        secret_patterns = (
            rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{50,}',
            rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        )
        for relative in tracked:
            path = safe_path(relative)
            if set(path.relative_to(ROOT).parts) & forbidden_parts or path.suffix in forbidden_suffixes:
                errors.append(f'forbidden tracked payload: {relative}')
            if path.name == '.env' or path.stat().st_size > 20 * 1024 * 1024:
                errors.append(f'private/oversize tracked payload: {relative}')
            if path.suffix != '.py' and any(re.search(pattern, path.read_bytes()) for pattern in secret_patterns):
                errors.append(f'credential-shaped content: {relative}')

    # Release extraction: read every declared payload and compare bytes again.
    package_manifest = ROOT / 'PACKAGE_MANIFEST.json'
    if package_manifest.exists():
        package = json.loads(package_manifest.read_text(encoding='utf-8'))
        for relative, item in package['files'].items():
            path = safe_path(relative)
            if (not path.is_file() or path.stat().st_size != item['size']
                    or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
                errors.append(f'release payload mismatch: {relative}')
    result = {'gate': 'FAIL' if errors else 'PASS', 'source_commit': manifest['source_commit'],
              'snapshot_files': len(expected), 'documents': len(docs),
              'local_links_checked': links_checked, 'errors': errors, 'field_gate': 'NOT_TESTED'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
