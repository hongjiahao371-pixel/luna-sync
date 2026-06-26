# Luna Sync

Luna Sync 是一个部署在 Linux 设备上的相机媒体同步工具。它可以连接相机 Wi-Fi，
浏览、下载和转码相机中的照片与视频，并提供网页管理界面。

## 功能

- 支持 `auto`、NetworkManager、wpa_supplicant 和手动连接模式
- 自动识别宿主机无线网卡，也可手动指定
- 扫描并连接相机 Wi-Fi，或只使用用户已手动连接好的网络
- 连上相机 Wi-Fi 后自动增量同步新文件
- 浏览、下载和删除本地媒体
- 断点续传
- 图片、动图与视频预览，H.265 视频可生成 H.264 兼容预览
- Docker Compose 部署

## 环境要求

- Linux 主机
- Docker 与 Docker Compose
- 如需自动连接相机 Wi-Fi，需要无线网卡及可用驱动

自动管理无线网络时需要 `network_mode: host` 和 `privileged: true`。macOS/Windows
Docker Desktop 不能直接管理宿主机无线网卡，但可以使用手动连接模式。

## Wi-Fi 后端

`wifi_backend` 支持以下值：

| 值 | 适用场景 |
|---|---|
| `auto` | 默认值；优先使用 NetworkManager，其次使用 wpa_supplicant，最后退到手动模式 |
| `networkmanager` | Ubuntu、Debian、树莓派等宿主机已运行 NetworkManager 的环境 |
| `wpa_supplicant` | NAS/精简 Linux，有无线网卡驱动但没有 NetworkManager 的环境 |
| `none` | 程序不管理 Wi-Fi；用户自己让部署设备能访问 `camera_host` |

`networkmanager` 模式需要额外挂载宿主机 D-Bus 与 NetworkManager：

```bash
docker compose -f docker-compose.yml -f docker-compose.networkmanager.yml up -d --build
```

使用 Docker Hub 镜像时：

```bash
docker compose -f docker-compose.hub.yml -f docker-compose.networkmanager.yml up -d
```

`wpa_supplicant` 模式会在容器内启动自己的 `wpa_supplicant` 管理无线网卡，不依赖
宿主机安装 `nmcli`。请确保没有其他服务同时控制同一块无线网卡。

`none` 模式适合路由桥接、宿主机手动连接、或只想浏览/管理本地已下载文件的场景。
只要部署设备可以访问 `camera_host`，扫描、下载和自动增量同步仍可工作。

## 快速部署

```bash
cp config.example.json config.json
```

编辑 `config.json`，填写相机 Wi-Fi 名称和密码。`wifi_backend` 默认是 `auto`。
`wifi_iface` 默认设为 `null`，程序会自动选择无线设备；多块无线网卡时可填写
`wlan0`、`wlp2s0` 等设备名。

```bash
mkdir -p downloads state
docker compose up -d --build
```

浏览器访问 `http://设备IP:8765`。

也可以直接使用 Docker Hub 镜像：

```bash
docker compose -f docker-compose.hub.yml up -d
```

或使用 GitHub Container Registry 镜像：

```bash
docker pull ghcr.io/hongjiahao371-pixel/luna-sync:latest
```

仓库的 `main` 分支和 `v*` 标签更新后，会通过 GitHub Actions 自动发布 amd64
和 arm64 镜像。

## 项目结构

```text
app/
  web_app.py       Web 服务
  wifi.py          无线网卡识别与连接
  luna_client.py   相机通信
  downloader.py    媒体下载
docker-compose.yml
docker-compose.hub.yml
docker-compose.networkmanager.yml
Dockerfile
entrypoint.sh
config.example.json
```

## 配置

| 字段 | 说明 |
|---|---|
| `camera_host` | 相机热点中的相机地址 |
| `camera_ssid` | 相机 Wi-Fi 名称 |
| `camera_password` | 相机 Wi-Fi 密码 |
| `wifi_backend` | `auto`、`networkmanager`、`wpa_supplicant` 或 `none` |
| `wifi_iface` | 无线网卡名；`null` 时自动识别 |
| `wpa_ctrl` | wpa_supplicant 控制 socket 目录 |
| `auto_sync` | 是否自动增量同步 |
| `auto_sync_interval_sec` | 自动同步检查间隔，最低 10 秒 |
| `download_dir` | 容器内下载目录 |
| `state_dir` | 容器内运行状态、缩略图和转码缓存目录 |
| `web_port` | Web 服务端口 |

`config.json`、媒体文件和运行状态已被 Git 忽略。记住 Wi-Fi 功能会将凭据保存在
`state/wifi.json`，文件权限设置为仅容器用户可读写。请仅在可信局域网中使用。
