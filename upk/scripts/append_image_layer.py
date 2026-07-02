#!/usr/bin/env python3
import argparse
import copy
import hashlib
import io
import json
import os
import tarfile
from datetime import datetime, timezone


PATCH_FILES = [
    ('entrypoint.sh', 'entrypoint.sh', 0o755),
    ('app/wifi.py', 'app/wifi.py', 0o644),
    ('app/web_app.py', 'app/web_app.py', 0o644),
    ('app/templates/index.html', 'app/templates/index.html', 0o644),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        dirs = set()
        for _, dest, _ in files:
            parent = os.path.dirname(dest)
            while parent:
                dirs.add(parent)
                parent = os.path.dirname(parent)
        for name in sorted(dirs):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = info.gid = 0
            info.mtime = 0
            tar.addfile(info)
        for src, dest, mode in files:
            data = open(src, 'rb').read()
            info = tarfile.TarInfo(dest)
            info.size = len(data)
            info.mode = mode
            info.uid = info.gid = 0
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def add_bytes(tar, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uid = info.gid = 0
    info.mtime = 0
    tar.addfile(info, io.BytesIO(data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()

    layer = tar_bytes(PATCH_FILES)
    layer_digest = sha256(layer)
    with tarfile.open(args.input, 'r') as src:
        manifest = json.load(src.extractfile('manifest.json'))
        if len(manifest) != 1:
            raise SystemExit('expected one manifest entry')
        entry = copy.deepcopy(manifest[0])
        layer_dir = '%03d_%s' % (len(entry['Layers']), layer_digest)
        layer_name = layer_dir + '/layer.tar'
        config_name = entry['Config']
        config = json.load(src.extractfile(config_name))

        entry['RepoTags'] = [args.tag]
        entry['Layers'].append(layer_name)
        config.setdefault('rootfs', {}).setdefault('diff_ids', []).append('sha256:' + layer_digest)
        config.setdefault('history', []).append({
            'created': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'created_by': 'upk hotplug patch layer',
        })
        config_bytes = json.dumps(config, separators=(',', ':'), sort_keys=True).encode()
        new_config_name = sha256(config_bytes) + '.json'
        entry['Config'] = new_config_name

        repositories = {}
        if ':' in args.tag:
            repo, tag = args.tag.rsplit(':', 1)
            repositories = {repo: {tag: new_config_name[:-5]}}

        with tarfile.open(args.output, 'w') as dst:
            add_bytes(dst, 'manifest.json', json.dumps([entry], separators=(',', ':')).encode())
            add_bytes(dst, 'repositories', json.dumps(repositories, separators=(',', ':')).encode())
            add_bytes(dst, new_config_name, config_bytes)
            for member in src.getmembers():
                if member.name in ('manifest.json', 'repositories', config_name):
                    continue
                fileobj = src.extractfile(member) if member.isfile() else None
                dst.addfile(member, fileobj)
            add_bytes(dst, layer_dir + '/VERSION', b'1.0\n')
            add_bytes(dst, layer_dir + '/json', b'{}')
            add_bytes(dst, layer_name, layer)


if __name__ == '__main__':
    main()
