# Luna Sync UPK Packaging

This directory contains the UGOS Pro UPK packaging project for Luna Sync.

The package is a Docker app. It embeds a Docker image archive under
`luna-sync/rootfs_amd64/images/` and uses `luna-sync/rootfs_common/docker-compose.yaml`
to start two services:

- `luna-sync`: the main app on host networking, required for Wi-Fi control.
- `luna-sync-open`: a bridge-network gateway on port `8767`, used by the UGOS
  app launcher to avoid 404s from the system gateway.

The UPK exposes a `DOWNLOAD_DIR` path parameter. Users can choose a normal
shared folder such as `/volume1/LunaSync`; the app mounts `/volume1` and stores
downloaded camera media in that selected directory. The Docker app mounts the
UGOS app config read-only and reads `DOWNLOAD_DIR` from it at startup.

Build prerequisites:

- Linux amd64 environment
- `ugcli` from UGREEN
- Python 3
- Network access to Docker Hub

Build:

```bash
python3 scripts/make_icon.py luna-sync/rootfs_common/icon.png
python3 scripts/docker_archive_from_registry.py \
  --image jvsheng/luna-sync:sha-9ced331 \
  --arch amd64 \
  --output luna-sync/rootfs_amd64/images/luna-sync-amd64.tar
python3 scripts/append_image_layer.py \
  --input luna-sync/rootfs_amd64/images/luna-sync-amd64.tar \
  --output luna-sync/rootfs_amd64/images/luna-sync-amd64.tar.tmp \
  --tag jvsheng/luna-sync:upk-read-ugb-download-dir-20260703
mv luna-sync/rootfs_amd64/images/luna-sync-amd64.tar.tmp \
  luna-sync/rootfs_amd64/images/luna-sync-amd64.tar
ugcli check --path luna-sync
cd luna-sync
ugcli pack --arch amd64 --build 12
```

The generated `.upk` will be written to `luna-sync/build_dir/pkgs/upk/`.
