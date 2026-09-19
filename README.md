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

默认 Docker Compose 使用 `network_mode: host` 和 `privileged: true`，应用可在
WebUI 内直接接管宿主机无线网卡（wpa_supplicant 模式），Web 服务直接以宿主机端口
对外。这是自部署形态的能力选择；绿联应用商店分发的 UPK 包受商店安全规范约束，
改用桥接网络与非特权容器（见 `upk/`）。macOS/Windows Docker Desktop 不能管理
宿主机无线网卡，可使用手动连接模式。

## Windows EXE

Windows x64 版会直接使用系统无线网络接口，不依赖 Docker、NetworkManager 或
wpa_supplicant。首次启动会自动打开 `https://127.0.0.1:8765`（自签证书，确认告警即可），默认目录为：

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

`networkmanager` 模式通过挂载宿主机 D-Bus 套接字远程调度宿主机的 NetworkManager
（与 wpa 模式二选一，`auto` 时优先 wpa）：

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

浏览器访问 `https://设备IP:8765`（首次打开会提示自签证书告警，确认继续即可；也可用 `LUNA_TLS_CERT`/`LUNA_TLS_KEY` 挂载自己的证书）。

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
| `tls` | `auto`（默认，自动生成自签证书并启用 HTTPS）或 `off`；也可用环境变量 `LUNA_TLS` |
| `tls_cert` / `tls_key` | 自有证书路径（PEM）；也可用环境变量 `LUNA_TLS_CERT`/`LUNA_TLS_KEY` |
| `wifi_guidance` | 设为 `ugos` 时连接面板替换为「到系统 Wi-Fi 设置连接相机」的指引（UPK 商店包默认启用）；Docker 部署无需设置 |

`config.json`、媒体文件和运行状态已被 Git 忽略。记住 Wi-Fi 功能会将凭据保存在
`state/wifi.json`，文件权限设置为仅容器用户可读写。

## Web 访问密码

首次打开页面会引导设置 Web 访问密码（至少 4 位），设置后所有页面和 API 均需登录；
浏览器登录态以 HttpOnly Cookie 保存 30 天，右上角可退出登录。密码以 PBKDF2 哈希
存储在 `state/settings.json` 的 `web_password` 字段，不会明文保存。

Web 服务默认启用 HTTPS：首次启动自动在 `state/tls/` 生成自签证书（RSA-2048，10 年），
登录密码、Wi-Fi 凭据与 SSID 均经 TLS 加密传输，登录 Cookie 带 `Secure` 标记并下发
HSTS。明文 HTTP 不再提供服务；确需关闭请设置 `LUNA_TLS=off`（不推荐）。

部署时也可以用 `web_auth_token` 配置项或 `LUNA_AUTH_TOKEN` 环境变量直接指定密码，
此时首次引导设置不可用。忘记密码时，删除 `state/settings.json` 中的 `web_password`
字段并重启应用即可重新引导设置。

## 更新日志

### v1.3.0

安全合规（绿联应用商店审核要求，作用于商店 UPK 包形态）：

- 容器不再使用 host 网络与特权模式：商店包以桥接网络运行，主服务 Web 端口仅容器内部可达，唯一对外入口为 8767 透传网关（TLS 端到端加密）
- 8767 入口对明文 HTTP 请求返回 301 跳转 HTTPS，兼容 UGOS 应用中心的 http 打开链接，明文链路不承载任何数据
- 商店包连接面板替换为系统指引卡片：引导用户在 UGOS Pro 的 Wi-Fi 管理界面连接相机热点，应用实时显示相机可达状态

HTTPS 加密传输（两种部署形态均生效）：

- Web 服务默认启用 HTTPS：首次启动自动生成自签证书（`state/tls/`），支持 `LUNA_TLS_CERT`/`LUNA_TLS_KEY` 配置自有证书，`LUNA_TLS=off` 可显式关闭（不推荐）
- 登录密码、Wi-Fi 密码与 SSID 全部经 TLS 加密传输；登录会话 Cookie 追加 `Secure` 标记并下发 HSTS

隐私授权与协议入口：

- 首次隐私授权弹窗新增「不同意并退出」按钮，与同意按钮同等便捷；拒绝后进入说明页，可随时返回重新阅读并同意
- 主界面顶栏新增「隐私与协议」常驻入口，应用内即可随时查看隐私政策与用户协议；登录页同步补充协议链接
- 隐私政策与用户协议补充传输加密说明（2026-09-18），升级后会重新显示一次授权弹窗

部署形态：

- 自部署 Docker Compose（`docker-compose.yml` / `docker-compose.hub.yml`）保持 host 网络 + 特权容器，可在 WebUI 内以 wpa_supplicant 直接接管无线网卡（与 v1.2.x 行为一致）；该形态面向自部署用户，不经过应用商店分发
- NetworkManager 覆盖文件（`docker-compose.networkmanager.yml`）经宿主机 D-Bus 远程调度网络，与 wpa 模式二选一

媒体预览：

- 支持 Live 图预览：`LIV_` 前缀 JPG（及 `.liv` 容器）自动拆分静态照片与动态片段，点开先看静帧、一键切换播放动态（循环静音），网格墙卡片带 Live 徽标
- 支持 360 全景照片环视：等距柱状全景图（`.insp` 及相机直出的 2:1 JPG）进入 WebGL 球面查看器，按住拖动环视、滚轮缩放视角；普通照片自动回退平图显示，不依赖任何外部库
- Live 图动态片段缓存纳入「清除缓存」范围，删除文件时同步清理
- LRV 与视频文件在相机列表和本地列表中显示缩略图：本地文件直接抽帧，未下载的素材经相机 HTTP 流式抽帧并缓存

其他改进：

- 相机文件列表支持点击表头排序（文件名/来源/类型/拍摄日期/大小），默认按拍摄日期从新到旧，再点切换升降序
- 修复自动同步与日志写入之间的并发死锁

### v1.2.5

- 新增 Web 访问密码：首次打开引导设置（PBKDF2 哈希存储），登录后以 Cookie 保持 30 天会话；所有 API 与媒体接口未登录返回 401，首页跳转登录页
- 支持 `web_auth_token` 配置 / `LUNA_AUTH_TOKEN` 环境变量部署级指定密码，覆盖首次引导
- WebUI 右上角新增退出登录；任意请求会话失效时自动跳回登录页
- 登录页与设置引导支持中英文

### v1.2.4

- UPK 安装形态下 8766 端口不再对局域网开放：主服务仅监听本机回环，由 docker 网桥网关上的转发入口供 8767 应用入口反代，8767 成为唯一对外端口
- 新增 `LUNA_GATEWAY_FORWARD` 环境变量控制该模式；Docker Compose 与 Windows 版默认行为不变

### v1.2.3

- 修复“取消下载”只停止当前文件、随后继续执行队列的问题：现在会停止当前任务并清空整批待下载队列
- 覆盖任务刚从队列取出、等待相机就绪但尚未显示进度的取消窗口，避免点击取消后任务重新排队
- 前端会明确显示停止当前任务、移除排队数量和空闲状态；自动同步仍开启时会提示后续可能重新加入未完成素材
- 精确大小已知时会校验本地文件和断点位置，异常文件不再被误判为已完成
- 托管 Wi-Fi 模式会核对当前 SSID，避免其他网络上的同地址设备被误判为 Luna
- 新增 DNG 缩略图与大图预览，并修正 wpa_supplicant 信号强度的 dBm 显示

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
