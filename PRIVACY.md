# Luna Sync 隐私政策 / Privacy Policy

**生效及更新日期 / Effective and last updated: 2026-07-22**

Luna Sync（“本应用”）是一款部署在用户自有 NAS 或电脑上的相机素材备份与同步工具。本应用完全在用户自己的设备与局域网内运行，不主动向开发者、云端或第三方上传、出售或共享用户数据。

Luna Sync is a camera media backup and sync tool deployed on the user's own NAS or computer. It runs entirely on the user's device and local network and does not actively upload, sell, or share user data with the developer, cloud services, or third parties.

## 1. 处理的信息 / Information Processed

- 相机 Wi-Fi 名称（SSID）与密码。密码仅在用户开启“记住密码”后保存。
- 无线网卡状态和本地连接配置，例如相机网段地址。
- 相机媒体目录，以及用户选择下载的照片、视频、动图和 LRV 文件。
- 下载进度、自动同步设置、同意记录、缩略图和视频兼容预览缓存。

- Camera Wi-Fi SSID and password. The password is stored only when Remember password is enabled.
- Wireless adapter status and local connection settings, such as the camera subnet address.
- Camera media listings and photos, videos, animated images, or LRV files selected for download.
- Download progress, auto-sync settings, consent record, thumbnails, and compatible video preview cache.

本应用不收集账号、手机号、精确位置、通讯录、支付信息，也不集成广告、分析或数据上报 SDK。

The app does not collect accounts, phone numbers, precise location, contacts, or payment information, and does not integrate advertising, analytics, or telemetry SDKs.

## 2. 处理目的与范围 / Purpose and Scope

- Wi-Fi 凭据仅用于在用户的局域网内连接用户选择的 Luna 相机。
- 媒体目录仅用于展示、预览、筛选与增量备份。
- 运行状态与缓存仅用于恢复任务和提升预览速度。
- 上述信息仅在用户自己的设备和局域网内处理，不会发送给开发者或第三方。

- Wi-Fi credentials are used only to connect to the selected Luna camera on the user's local network.
- Media listings are used only for browsing, preview, filtering, and incremental backup.
- Runtime state and cache are used only to resume tasks and improve preview speed.
- This information is processed only on the user's device and local network and is not sent to the developer or third parties.

## 3. 保存位置与期限 / Storage and Retention

- Wi-Fi 名称与密码保存在应用状态目录，直至用户点击“清除记住”、清除应用数据或卸载应用。
- 设置、同步状态与同意记录保留至用户清除应用状态数据或卸载应用。
- 缩略图和转码预览保留至用户点击“清除缓存”、清除应用数据或卸载应用。
- 已下载素材保存在安装时指定的目录，直至用户主动删除；卸载应用不会主动删除该外部素材目录。

- Wi-Fi credentials remain in the app state directory until cleared by the user or until app data is removed.
- Settings, sync state, and consent records remain until app state is cleared or the app is uninstalled.
- Thumbnail and transcoded preview cache remains until cleared by the user or app data is removed.
- Downloaded media remains in the chosen folder until the user deletes it. Uninstalling the app does not remove that external media folder.

## 4. 用户控制与安全 / User Control and Security

用户可随时清除记住的 Wi-Fi、预览缓存及已下载素材。Wi-Fi 凭据文件权限设置为仅容器运行用户可读写。请仅在可信局域网内使用应用，并为 Luna Sync 选择独立素材目录，不要与个人文件或其他应用数据混存。

Users can clear saved Wi-Fi credentials, preview cache, and downloaded media at any time. Wi-Fi credential files are permission-restricted to the container user. Use the app only on a trusted local network and select a dedicated Luna Sync media folder instead of mixing it with personal files or other application data.

## 5. 第三方组件 / Third-Party Components

应用使用 Docker、Flask、Pillow、ffmpeg 等开源组件。组件受各自开源许可证约束；本应用不会借助这些组件主动将用户信息发送到外部服务。

The app uses open-source components such as Docker, Flask, Pillow, and ffmpeg. Those components are governed by their respective licenses; the app does not use them to actively transmit user information to external services.

## 6. 联系方式 / Contact

隐私问题请通过 [GitHub Issues](https://github.com/hongjiahao371-pixel/luna-sync/issues) 联系开发者。

For privacy questions, contact the developer through [GitHub Issues](https://github.com/hongjiahao371-pixel/luna-sync/issues).
