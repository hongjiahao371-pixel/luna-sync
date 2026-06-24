# Luna Sync

Luna Sync 是一个部署在 Linux 设备上的相机媒体同步工具。它通过 NetworkManager
连接相机 Wi-Fi，浏览、下载和转码相机中的照片与视频，并提供网页管理界面。

## 功能

- 自动识别宿主机无线网卡，也可手动指定
- 扫描并连接相机 Wi-Fi
- 浏览、下载和删除本地媒体
- 断点续传
- 图片缩略图与视频转码
- Docker Compose 部署

## 环境要求

- Linux 主机
- Docker 与 Docker Compose
- 无线网卡及可用驱动
- 宿主机使用 NetworkManager

当前方案需要管理宿主机无线网络，因此使用 host 网络、D-Bus 和 privileged 模式。
macOS/Windows Docker Desktop 不能直接管理宿主机无线网卡。

## 快速部署

```bash
cp config.example.json config.json
```

编辑 `config.json`，填写相机 Wi-Fi 名称和密码。`wifi_iface` 默认设为 `null`，
程序会自动选择无线设备；多块无线网卡时可填写 `wlan0`、`wlp2s0` 等设备名。

```bash
mkdir -p downloads state
docker compose up -d --build
```

浏览器访问 `http://设备IP:8765`。

## 项目结构

```text
app/
  web_app.py       Web 服务
  wifi.py          无线网卡识别与连接
  luna_client.py   相机通信
  downloader.py    媒体下载
docker-compose.yml
Dockerfile
config.example.json
```

## 配置

| 字段 | 说明 |
|---|---|
| `camera_host` | 相机热点中的相机地址 |
| `camera_ssid` | 相机 Wi-Fi 名称 |
| `camera_password` | 相机 Wi-Fi 密码 |
| `wifi_iface` | 无线网卡名；`null` 时自动识别 |
| `download_dir` | 容器内下载目录 |
| `web_port` | Web 服务端口 |

`config.json`、媒体文件和运行状态已被 Git 忽略。记住 Wi-Fi 功能会将凭据保存在
`state/wifi.json`，文件权限设置为仅容器用户可读写。请仅在可信局域网中使用。
