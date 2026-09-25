#!/usr/bin/env python3
"""Read-only verification of the pinned offline ZIP, every payload and model references."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BLOCK = 1024 * 1024


def digest_stream(stream):
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(BLOCK):
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def verify(path):
    registry = json.loads((ROOT / 'resources/model-registry.json').read_text(encoding='utf-8'))
    expected = registry['bundle']
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular offline ZIP file')
    with path.open('rb') as stream:
        size, digest = digest_stream(stream)
    if size != expected['size'] or digest != expected['sha256']:
        raise ValueError('Whole ZIP does not match the pinned size and SHA-256')
    prefix = expected['archive_prefix']
    manifest_name = prefix + 'offline_bundle_manifest.json'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP entries')
        manifest_data = archive.read(manifest_name)
        if hashlib.sha256(manifest_data).hexdigest() != expected['content_manifest_sha256']:
            raise ValueError('Resource manifest does not match the pinned SHA-256')
        manifest = json.loads(manifest_data)
        files = manifest['files']
        wanted = {prefix + item['path'] for item in files} | {manifest_name}
        if set(names) != wanted or len(files) != registry['contains']['payload_files']:
            raise ValueError('ZIP file set differs from the resource manifest')
        checked = {}
        for item in files:
            relative = PurePosixPath(item['path'])
            if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative):
                raise ValueError('Unsafe resource path')
            name = prefix + str(relative)
            info = archive.getinfo(name)
            if info.file_size != item['size'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'Invalid size or symlink: {relative}')
            with archive.open(name) as stream:
                size, digest = digest_stream(stream)
            if size != item['size'] or digest != item['sha256']:
                raise ValueError(f'Resource integrity failure: {relative}')
            checked[str(relative)] = item
        for item in registry['model_files']:
            if checked.get(item['path']) != item:
                raise ValueError(f'Model registry mismatch: {item["path"]}')
        model_manifest = json.loads(archive.read(
            prefix + 'resources/models/ollama/manifests/registry.ollama.ai/library/qwen3-vl/2b-instruct'))
        references = [model_manifest['config'], *model_manifest['layers']]
        for ref in references:
            kind, digest = ref['digest'].split(':', 1)
            if kind != 'sha256':
                raise ValueError('Unsupported model content digest')
            blob = 'resources/models/ollama/blobs/sha256-' + digest
            if blob not in checked or checked[blob]['sha256'] != digest or checked[blob]['size'] != ref['size']:
                raise ValueError('Missing or mismatched Qwen model blob')
    return {'gate': 'PASS', 'zip_bytes': path.stat().st_size, 'zip_sha256': expected['sha256'],
            'payload_files': len(files), 'archive_files': len(names),
            'model_files': len(registry['model_files']), 'qwen_references_verified': len(references),
            'field_gate': 'NOT_TESTED', 'installation_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip_path', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.zip_path)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({'gate': 'FAIL', 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
