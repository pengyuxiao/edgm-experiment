"""Restore byte-exact archived evidence without overwriting existing files."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='validate without creating files')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'evidence/archive_manifest.json').read_text())
    for item in manifest['files']:
        original = (root / item['original']).resolve()
        archive = (root / item['archive']).resolve()
        if not all(p.is_relative_to(root / 'evidence') for p in (original, archive)):
            raise ValueError('archive paths must stay within evidence')
        packed = archive.read_bytes()
        if len(packed) != item['archive_bytes'] or sha(packed) != item['archive_sha256']:
            raise ValueError(f'archive hash/size mismatch: {archive}')
        data = gzip.decompress(packed)
        if len(data) != item['original_bytes'] or sha(data) != item['original_sha256']:
            raise ValueError(f'original hash/size mismatch: {original}')
        if original.exists():
            if original.read_bytes() != data:
                raise ValueError(f'existing file differs; refusing overwrite: {original}')
            status = 'verified existing'
        elif args.check:
            status = 'verified archive; original not restored'
        else:
            with original.open('xb') as stream:
                stream.write(data)
            status = 'restored'
        print(f'{status}: {item["original"]}')


if __name__ == '__main__':
    main()
