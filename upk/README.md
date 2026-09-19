# Luna Sync UPK Packaging

This directory contains the UGOS Pro UPK packaging project for Luna Sync.

The package is a Docker app. It embeds a Docker image archive under
`luna-sync/rootfs_amd64/images/` and uses `luna-sync/rootfs_common/docker-compose.yaml`
to start two services:

- `luna-sync`: the main app on the compose bridge network. The web console
  speaks HTTPS (a self-signed certificate is generated on first start); port
  `8766` stays inside the compose network and is never published to the LAN.
- `luna-sync-open`: the published entry on port `8767`. TLS traffic is passed
  through to the main app untouched (end-to-end encryption), while plaintext
  HTTP requests get a 301 redirect to the same host and port over HTTPS, so
  the UGOS app center `http://` launch URL still lands on the secure UI
  without ever carrying data in plaintext.

The UPK exposes a required `DOWNLOAD_DIR` path parameter. UGOS mounts the chosen
host folder to `/downloads` inside the container, and Luna Sync stores camera
media under `/downloads`. The installer text asks users to select a dedicated
folder and warns against mixing personal files or other application data there.

The package metadata includes developer, publisher, privacy policy, user
agreement, source code, help, and technical support links. Developer and
publisher links both open the publisher's GitHub profile. The application also
requires explicit first-run consent before it reads saved Wi-Fi credentials,
detects adapters, scans camera media, or starts automatic sync.

Store listing screenshots are uploaded separately from the UPK. Follow the
UGOS listing requirements and provide at least two PC/Web images at
`1854x1236` and two mobile images at `1125x2436`.

Build prerequisites:

- Linux amd64 environment
- `ugcli` from UGREEN
- Python 3
- Network access to Docker Hub

Build:

```bash
python3 scripts/docker_archive_from_registry.py \
  --image jvsheng/luna-sync:latest \
  --arch <amd64|arm64> \
  --output luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar
python3 scripts/append_image_layer.py \
  --input luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar \
  --output luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar.tmp \
  --tag jvsheng/luna-sync:upk-v125-auth-20260817
mv luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar.tmp \
  luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar
ugcli check --path luna-sync
cd luna-sync
ugcli pack --arch <amd64|arm64> --build 29
```

The tracked `luna-sync/rootfs_common/icon.png` is the release icon. It must
remain a 256x256 PNG under 100 KB with the required light rounded-square
template border.

The generated `.upk` will be written to `luna-sync/build_dir/pkgs/upk/`.
