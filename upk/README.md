# Luna Sync UPK Packaging

This directory contains the UGOS Pro UPK packaging project for Luna Sync.

The package is a Docker app. It embeds a Docker image archive under
`luna-sync/rootfs_amd64/images/` and uses `luna-sync/rootfs_common/docker-compose.yaml`
to start two services:

- `luna-sync`: the main app on host networking, required for Wi-Fi control.
- `luna-sync-open`: a bridge-network gateway on port `8767`, used by the UGOS
  app launcher to avoid 404s from the system gateway.

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
python3 scripts/make_icon.py luna-sync/rootfs_common/icon.png
python3 scripts/docker_archive_from_registry.py \
  --image jvsheng/luna-sync:latest \
  --arch <amd64|arm64> \
  --output luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar
python3 scripts/append_image_layer.py \
  --input luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar \
  --output luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar.tmp \
  --tag jvsheng/luna-sync:upk-v122-ucd2-autostop-20260728
mv luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar.tmp \
  luna-sync/rootfs_<amd64|arm64>/images/luna-sync-<amd64|arm64>.tar
ugcli check --path luna-sync
cd luna-sync
ugcli pack --arch <amd64|arm64> --build 24
```

The generated `.upk` will be written to `luna-sync/build_dir/pkgs/upk/`.
