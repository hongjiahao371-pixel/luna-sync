# Luna Sync 隐私政策 / Privacy Policy

**最后更新 / Last updated: 2026-07-03**

Luna Sync（"本应用"）是一款部署在用户自有 Linux 设备（如 NAS）上的相机素材备份与同步工具。
本应用完全在您的本地设备与局域网内运行，**不主动向任何外部服务器上传、出售或共享您的数据**。

Luna Sync is a camera media backup and sync tool deployed on your own Linux device
(such as a NAS). It runs entirely on your local device and local network, and **does not
actively upload, sell, or share your data with any external server**.

---

## 1. 我们收集的信息 / Information We Collect

### 相机连接凭据 / Camera Connection Credentials
- 相机 Wi-Fi 名称（SSID）与密码 / Camera Wi-Fi SSID and password
- 无线网卡配置（如 `camera_client_cidr`）/ Wireless adapter configuration
- 存储位置：容器内 `config.json` 与 `state/wifi.json`，仅容器运行用户可读写
- Stored in `config.json` and `state/wifi.json` inside the container, readable/writable
  only by the container user.

### 相机媒体文件 / Camera Media Files
- 通过相机 Wi-Fi 从您的相机下载的照片与视频 / Photos and videos downloaded from your
  camera over the camera Wi-Fi
- 存储位置：您在安装时指定的「素材保存目录」（如 `/volume1/LunaSync`）
- Stored in the "Media save folder" you choose at install time (e.g. `/volume1/LunaSync`).

### 运行状态与缓存 / Runtime State and Cache
- 同步进度、缩略图、视频转码预览缓存 / Sync progress, thumbnails, and transcode cache
- 存储位置：容器内 `state/` 目录 / Stored in the `state/` directory inside the container.

---

## 2. 信息如何使用 / How Information Is Used

- 相机凭据仅用于在您的局域网内连接相机并下载您本人的素材。
- Camera credentials are used only to connect to your camera on your local network and
  download your own media.
- 媒体文件仅保存在您指定的本地目录，不会上传到任何云端或第三方。
- Media is saved only to your designated local folder and is never uploaded to any cloud
  or third party.

---

## 3. 信息如何存储与保护 / How Information Is Stored and Protected

- 所有数据均存储在**您自己的设备**上。本应用不运营任何后端服务器来接收您的数据。
- All data is stored on **your own device**. This app does not operate any backend server
  that receives your data.
- Wi-Fi 凭据文件权限已设置为仅容器用户可读写。
- Wi-Fi credential files are permission-restricted to the container user only.
- 请在可信的局域网环境内使用，并妥善保管您设备的访问权限。
- Please use only within a trusted local network and keep your device access secured.

---

## 4. 第三方服务 / Third-Party Services

本应用本身不集成任何第三方分析、广告或数据上报 SDK。
The app itself does not integrate any third-party analytics, advertising, or data-reporting SDK.

> 注：应用以 Docker 容器方式运行，镜像可能包含基础系统组件（如 Debian/Alpine、ffmpeg 等），
> 这些组件的处理方式以其各自的开源许可与隐私条款为准。
> Note: The app runs as a Docker container; the image may include base system components
> (e.g. Debian/Alpine, ffmpeg) whose handling is governed by their own licenses and terms.

---

## 5. 您的权利 / Your Rights

- 您可随时删除 `config.json`、`state/` 及已下载的媒体文件以彻底清除数据。
- You may delete `config.json`, `state/`, and downloaded media at any time to fully clear data.
- 卸载本应用不会自动删除您已下载的素材文件，需您手动清理。
- Uninstalling the app does not automatically delete downloaded media; you must remove them manually.

---

## 6. 联系方式 / Contact

如有隐私相关问题，请通过 GitHub Issues 反馈：
For privacy-related questions, please use GitHub Issues:

https://github.com/hongjiahao371-pixel/luna-sync/issues
