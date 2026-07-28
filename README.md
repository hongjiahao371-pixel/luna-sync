# Luna Sync

Luna Sync 是一个相机媒体同步工具。它可以连接相机 Wi-Fi，浏览、下载和转码相机中的
照片与视频，并提供网页管理界面；支持 Linux Docker/NAS 与 Windows 独立程序。

## 功能

- 支持 Windows Native Wi-Fi、NetworkManager、wpa_supplicant 和手动连接模式
- 自动识别宿主机无线网卡，也可手动指定
- 扫描并连接相机 Wi-Fi，或只使用用户已手动连接好的网络
- 连上相机 Wi-Fi 后自动增量同步新文件
- 浏览、下载和删除本地媒体
- 断点续传
- 图片、动图与视频预览，H.265 视频可生成 H.264 兼容预览
- **本地媒体库**：独立的「本地已下载」视图，网格墙直观展示已下载的照片与视频，支持照片/视频分类筛选、关键词搜索、网格/表格两种展示形态
- **视频缩略图**：自动用 ffmpeg 抽取视频首秒画面生成缩略图，网格中直接预览视频内容
- **一键清理预览缓存**：可清空缩略图与转码预览缓存（不影响已下载文件，下次查看自动重新生成）
- Docker Compose 部署

## Linux / NAS 环境要求

- Linux 主机
- Docker 与 Docker Compose
- 如需自动连接相机 Wi-Fi，需要无线网卡及可用驱动

自动管理无线网络时需要 `network_mode: host` 和 `privileged: true`。macOS/Windows
Docker Desktop 不能直接管理宿主机无线网卡，但可以使用手动连接模式。

## Windows EXE

Windows x64 版会直接使用系统无线网络接口，不依赖 Docker、NetworkManager 或
wpa_supplicant。首次启动会自动打开 `http://127.0.0.1:8765`，默认目录为：

- 素材：`%USERPROFILE%\Videos\Luna Sync`
- 配置和缓存：`%LOCALAPPDATA%\LunaSync`

关闭 Luna Sync 的控制台窗口即可退出程序。Windows 可执行文件必须在 Windows 上构建；
仓库的 `Build Windows EXE` GitHub Actions 会生成 `LunaSync-Windows-x64` 构建产物。
也可以在 Windows PowerShell 中本地构建：

```powershell
python -m pip install -r windows/requirements.txt
python windows/prepare_assets.py
python -m PyInstaller --clean --noconfirm windows/luna_sync.spec
```

生成文件位于 `dist\LunaSync.exe`。

## Wi-Fi 后端

`wifi_backend` 支持以下值：

| 值 | 适用场景 |
|---|---|
| `auto` | 默认值；优先使用 NetworkManager，其次使用 wpa_supplicant，最后退到手动模式 |
| `windows` | Windows 独立程序，使用系统 Native Wi-Fi 接口 |
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
宿主机安装 `nmcli`。请确保没有其他服务同时控制同一块无线网卡。连接成功后会给无线网卡
配置 `camera_client_cidr`，默认示例为 `192.168.42.2/24`，用于访问 `camera_host`。

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
windows/
  launcher.py      Windows 启动入口
  luna_sync.spec   PyInstaller 打包配置
```

## 配置

| 字段 | 说明 |
|---|---|
| `camera_host` | 相机热点中的相机地址 |
| `camera_ssid` | 相机 Wi-Fi 名称 |
| `camera_password` | 相机 Wi-Fi 密码 |
| `camera_client_cidr` | wpa_supplicant 模式下为无线网卡配置的相机网段地址 |
| `wifi_backend` | `auto`、`windows`、`networkmanager`、`wpa_supplicant` 或 `none` |
| `wifi_iface` | 无线网卡名；`null` 时自动识别 |
| `wpa_ctrl` | wpa_supplicant 控制 socket 目录 |
| `auto_sync` | 是否自动增量同步 |
| `auto_sync_lrv` | 自动同步是否包含 LRV 文件，默认包含；也可在 WebUI 中切换 |
| `auto_sync_interval_sec` | 自动同步检查间隔，最低 10 秒 |
| `download_dir` | 容器内下载目录 |
| `state_dir` | 容器内运行状态、缩略图和转码缓存目录 |
| `web_port` | Web 服务端口 |

`config.json`、媒体文件和运行状态已被 Git 忽略。记住 Wi-Fi 功能会将凭据保存在
`state/wifi.json`，文件权限设置为仅容器用户可读写。请仅在可信局域网中使用。

## 更新日志

### v1.2.2

- 使用 Luna UCD2 TCP 协议分页读取内置存储与存储卡文件，并复用控制连接持续保活；旧固件仍可回退到 HTTP 目录扫描
- 控制连接短暂中断时会在当前扫描或保活周期内自动重建，减少扫描 401、相机掉线和 Wi-Fi 重连
- 关闭自动同步会停止当前自动下载并移除待执行的自动任务，同时保留手动下载和断点文件
- 下载队列执行期间不再重复扫描或重连相机，避免相机忙于传输时被误判为离线
- 新增首次隐私授权、隐私政策与用户协议页面；未同意前不会读取 Wi-Fi 凭据、扫描相机或启动自动同步
- 完善 UPK 开发者、发布者、安装目录风险提示和应用商店详情资料

### v1.2.1

- 修复取消下载可能误取消下一项队列任务的问题，并确保下载异常时相机连接会被正确关闭
- 自动同步开关现在会持久化；素材扫描会串行执行并缓存已探测的精确文件大小，减少重复请求相机
- 关闭自动同步 LRV 时，也会排除 `.lrv.*` 关联文件，避免把拍摄过程的伴随数据误同步下来
- 关闭兼容视频预览时会停止前端轮询
- 优化 WebUI 工作区布局、素材选中状态和本地媒体库图标：预览、删除、视图切换与关闭预览均使用统一图标按钮
- 移除未使用的旧 `app/main.py` 入口

### v1.2.0

- 新增存储卡素材扫描与同步：同时扫描 Luna Ultra 内置存储和外置存储卡素材，下载时按来源保存到独立目录
- 新增自动同步 LRV 开关：可在 WebUI 中选择自动同步是否包含拍摄过程中生成的 LRV 文件
- 优化素材标识：使用内置存储/存储卡稳定文件 ID，修复同名文件在预览、下载、删除时可能混淆的问题
- 优化日期显示：中文界面下拍摄日期显示为中文日期格式，英文界面显示英文日期格式
- 优化文件大小显示：扫描时优先探测相机 HTTP 真实大小，减少列表大小与下载进度大小不一致的问题
- 修复视频兼容预览误判本地文件：转码预览源文件改存到预览缓存目录，不再让“下载选中”误提示文件已在本地
- 修复相机文件列表空白问题：避免前端表格变量覆盖翻译函数导致渲染中断
- 修复相机离线状态显示：相机断开后顶部状态会正确更新为离线

### v1.1.0

- 新增「本地已下载」视图：网格媒体墙直观展示已下载的照片与视频，支持照片/视频分类筛选、关键词搜索，网格墙与表格两种展示形态自由切换
- 新增视频缩略图：自动用 ffmpeg 抽取视频首秒画面生成缩略图，在网格中直接预览视频内容
- 新增一键清理预览缓存：清空缩略图与转码预览缓存（不影响已下载文件，下次查看自动重新生成）
- 优化媒体库切换 UI：采用胶囊分段控件，配相机/下载图标，风格更统一
- 修复视频卡片在网格中堆叠塌缩的问题

### v1.0.0

- 初始版本：Wi-Fi 自动/手动连接、增量同步、媒体浏览下载、视频转码预览
