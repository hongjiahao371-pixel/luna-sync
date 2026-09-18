#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import http.client
import io
import json
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REGISTRY = "https://registry-1.docker.io"
AUTH = "https://auth.docker.io/token"


def request_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res), res.headers


def request_bytes(url, headers=None):
    last = None
    data = bytearray()
    offset = 0
    for attempt in range(12):
        req_headers = dict(headers or {})
        if offset:
            req_headers["Range"] = "bytes=%d-" % offset
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as res:
                status = getattr(res, "status", None) or 200
                length = int(res.headers.get("Content-Length") or 0)
                total = length + (offset if status == 206 else 0)
                next_report = (offset // (64 * 1024 * 1024)) * (64 * 1024 * 1024) + 64 * 1024 * 1024
                while True:
                    chunk = res.read(1024 * 1024)
                    if not chunk:
                        break
                    data.extend(chunk)
                    offset = len(data)
                    if total and offset >= next_report:
                        print(f"downloaded {offset // 1024 // 1024}M / {total // 1024 // 1024}M", file=sys.stderr)
                        next_report += 64 * 1024 * 1024
            if total and len(data) != total:
                raise http.client.IncompleteRead(bytes(data), total - len(data))
            return bytes(data), None
        except (http.client.IncompleteRead, TimeoutError, urllib.error.URLError) as exc:
            last = exc
            if attempt == 11:
                break
            time.sleep(min(2 ** min(attempt, 4), 16))
    raise last


def auth_token(repo):
    query = urllib.parse.urlencode({
        "service": "registry.docker.io",
        "scope": f"repository:{repo}:pull",
    })
    data, _ = request_json(f"{AUTH}?{query}")
    return data["token"]


def split_image(image):
    repo_tag = image
    if "/" not in repo_tag:
        repo_tag = "library/" + repo_tag
    if ":" not in repo_tag.rsplit("/", 1)[-1]:
        repo_tag += ":latest"
    repo, tag = repo_tag.rsplit(":", 1)
    return repo, tag


def get_manifest(repo, reference, token, accept):
    headers = {"Authorization": f"Bearer {token}", "Accept": accept}
    return request_json(f"{REGISTRY}/v2/{repo}/manifests/{reference}", headers)


def blob(repo, digest, token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        data, _ = request_bytes(f"{REGISTRY}/v2/{repo}/blobs/{digest}", headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        # anonymous tokens expire after ~5 minutes; refresh once and retry
        data, _ = request_bytes(f"{REGISTRY}/v2/{repo}/blobs/{digest}",
                                {"Authorization": f"Bearer {auth_token(repo)}"})
    got = "sha256:" + hashlib.sha256(data).hexdigest()
    if got != digest:
        raise RuntimeError(f"blob digest mismatch: expected {digest}, got {got}")
    return data


def media_type(manifest):
    return manifest.get("mediaType", "")


def pick_platform(index, arch):
    for item in index.get("manifests", []):
        platform = item.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") == arch:
            return item["digest"]
    raise RuntimeError(f"linux/{arch} image not found")


def layer_tar(data):
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def add_bytes(tf, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    tf.addfile(info, io.BytesIO(data))


def build_archive(image, arch, output):
    repo, tag = split_image(image)
    token = auth_token(repo)
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])
    manifest, _ = get_manifest(repo, tag, token, accept)
    if "index" in media_type(manifest) or "manifest.list" in media_type(manifest):
        digest = pick_platform(manifest, arch)
        manifest, _ = get_manifest(repo, digest, token, accept)

    config_desc = manifest["config"]
    config = blob(repo, config_desc["digest"], token)
    config_json = json.loads(config)
    if config_json.get("architecture") != arch:
        raise RuntimeError(f"config architecture is {config_json.get('architecture')}, expected {arch}")

    config_name = config_desc["digest"].split(":", 1)[1] + ".json"
    layers = []
    layer_entries = []
    for idx, desc in enumerate(manifest["layers"]):
        compressed = blob(repo, desc["digest"], token)
        data = layer_tar(compressed)
        digest = hashlib.sha256(data).hexdigest()
        layer_name = f"{idx:03d}_{digest}/layer.tar"
        layers.append((layer_name, data))
        layer_entries.append(layer_name)

    manifest_json = [{
        "Config": config_name,
        "RepoTags": [image],
        "Layers": layer_entries,
    }]
    repositories = {repo: {tag: layer_entries[-1].split("/", 1)[0] if layer_entries else ""}}

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w") as tf:
        add_bytes(tf, "manifest.json", json.dumps(manifest_json, separators=(",", ":")).encode())
        add_bytes(tf, "repositories", json.dumps(repositories, separators=(",", ":")).encode())
        add_bytes(tf, config_name, config)
        for name, data in layers:
            prefix = name.rsplit("/", 1)[0]
            add_bytes(tf, f"{prefix}/VERSION", b"1.0")
            add_bytes(tf, f"{prefix}/json", b"{}")
            add_bytes(tf, name, data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--arch", default="amd64", choices=["amd64", "arm64"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build_archive(args.image, args.arch, args.output)


if __name__ == "__main__":
    main()
