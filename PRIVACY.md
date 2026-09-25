# Luna Sync 隐私政策 / Privacy Policy

**生效及更新日期 / Effective and last updated: 2026-09-25（版本 / version 2026-09-25）**

## 0. 运营者信息 / Operator

- 运营者：个人开发者 jvsheng（GitHub: [hongjiahao371-pixel](https://github.com/hongjiahao371-pixel)），无企业主体；真实身份信息已按绿联云开发者平台要求完成实名认证，并由平台在应用详情页按规定披露。
- 注册/办公地址：不适用（个人开发者，无注册企业及固定经营场所；运营地为中国境内）。
- 个人信息保护负责人：jvsheng；联系方式：1981940361@qq.com 或 [GitHub Issues](https://github.com/hongjiahao371-pixel/luna-sync/issues)。

- Operator: individual developer jvsheng (GitHub: [hongjiahao371-pixel](https://github.com/hongjiahao371-pixel)); no corporate entity. The operator's identity has been verified and is disclosed by the UGREEN App Center on the app detail page as required by the platform. No registered business address (individual developer, operating from within mainland China). Personal information protection contact: jvsheng — 1981940361@qq.com or [GitHub Issues](https://github.com/hongjiahao371-pixel/luna-sync/issues).

## 1. 处理的信息 / Information Processed

- Web 访问密码：首次使用时由用户设置，仅以 PBKDF2 加密哈希保存在应用状态目录，用于验证登录，不保存明文。
- 相机 Wi-Fi 名称（SSID）与密码：**仅自部署版本**提供应用内 Wi-Fi 管理并加密保存；**应用商店版本已彻底移除应用内 Wi-Fi 表单与扫描功能，不收集 Wi-Fi 名称与密码**，请在系统 Wi-Fi 设置中连接相机。
- 无线网卡状态和本地连接配置，例如相机网段地址。
- 相机媒体目录，以及用户选择下载的照片、视频、动图和 LRV 文件。
- 下载进度、自动同步设置、同意记录、缩略图和视频兼容预览缓存。

- Web access password: set by you on first use, stored in the app state directory as a PBKDF2 hash for login verification; the plaintext is never saved.
- Camera Wi-Fi SSID and password: **self-hosted deployments only**, stored encrypted. **The store-distributed version has fully removed the in-app Wi-Fi form and scanning feature and does not collect Wi-Fi information** — connect the camera in your system Wi-Fi settings instead.
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
- Web 管理界面默认以 HTTPS（TLS）加密传输，登录密码与 Wi-Fi 凭据仅在加密通道中传输。

- Wi-Fi credentials are used only to connect to the selected Luna camera on the user's local network.
- Media listings are used only for browsing, preview, filtering, and incremental backup.
- Runtime state and cache are used only to resume tasks and improve preview speed.
- This information is processed only on the user's device and local network and is not sent to the developer or third parties.
- The web console is served over HTTPS (TLS) by default; the login password and Wi-Fi credentials are only transmitted through the encrypted channel.

## 3. 已收集个人信息清单 / List of Personal Information Collected

| 信息类型 | 处理场景 | 使用目的 | 处理方式与存放 | 保存期限 |
| --- | --- | --- | --- | --- |
| Web 访问密码 | 首次使用设置访问密码 | 登录验证 | PBKDF2 单向哈希，存本机状态目录，不保存明文 | 直至撤回授权、清除应用数据或卸载 |
| 相机 Wi-Fi 名称与密码 | 仅自部署版：应用内连接相机并“记住” | 自动连接相机 Wi-Fi | 加密后存本机状态目录；商店版不收集 | 直至“清除记住”、撤回授权或卸载 |
| 相机媒体目录与文件列表 | 连接相机后扫描 | 展示、预览与增量同步 | 本机内存与状态文件 | 随应用数据清除 |
| 已下载素材 | 用户主动下载或自动同步 | 本地备份与管理 | 用户指定目录 | 用户自主删除 |
| 缩略图与预览缓存 | 浏览素材时生成 | 加速预览 | 本机缓存目录 | “清除缓存”、撤回授权或卸载 |
| 运行设置与同意记录 | 用户操作与首次授权 | 恢复设置、合规记录 | 本机状态文件 | 清除应用数据或卸载 |

| Type | Scenario | Purpose | Storage | Retention |
| --- | --- | --- | --- | --- |
| Web access password | First-use setup | Login verification | PBKDF2 hash, local state dir, no plaintext | Until withdrawn, data cleared, or uninstall |
| Camera Wi-Fi SSID/password | Self-hosted only: in-app connect + "remember" | Auto-connect to camera | Encrypted, local state dir; not collected by store version | Until cleared, withdrawn, or uninstall |
| Camera media listings | Scan after connect | Browse, preview, sync | In memory + local state files | With app data |
| Downloaded media | Manual download or auto-sync | Local backup | User-selected folder | User-managed |
| Thumbnail/preview cache | Generated while browsing | Faster preview | Local cache dir | Until cleared, withdrawn, or uninstall |
| Settings & consent record | User actions, first-run consent | Restore settings, compliance | Local state files | Until data cleared or uninstall |

## 4. 与第三方共享个人信息清单 / List of Third-Party Sharing

**无 / None.** 本应用不向任何第三方共享、转让或公开披露个人信息；不集成广告、分析、统计或数据上报 SDK，不存在向开发者或云端上传用户数据的通道。

**None.** The app does not share, transfer, or disclose personal information to any third party, and integrates no advertising, analytics, statistics, or telemetry SDK — there is no channel that uploads user data to the developer or any cloud service.

## 5. 保存位置与期限 / Storage and Retention

- Wi-Fi 名称与密码经加密保存在应用状态目录，直至用户点击“清除记住”、撤回授权、清除应用数据或卸载应用。
- 设置、同步状态与同意记录保留至用户清除应用状态数据或卸载应用。
- 缩略图和转码预览保留至用户点击“清除缓存”、撤回授权、清除应用数据或卸载应用。
- 已下载素材保存在安装时指定的目录，直至用户主动删除；卸载应用不会主动删除该外部素材目录。

- Wi-Fi credentials remain encrypted in the app state directory until cleared, withdrawn, or until app data is removed.
- Settings, sync state, and consent records remain until app state is cleared or the app is uninstalled.
- Thumbnail and transcoded preview cache remains until cleared, withdrawn, or app data is removed.
- Downloaded media remains in the chosen folder until the user deletes it. Uninstalling the app does not remove that external media folder.

## 6. 用户权利及行权途径 / Your Rights and How to Exercise Them

- **访问**：应用内即可查看全部被处理的数据（素材库、下载记录、设置与缓存）。
- **更正**：应用内可修改 Wi-Fi 配置（自部署版）与素材目录；访问密码可通过“撤回授权”后重新初始化。
- **删除**：应用内可“清除记住的 Wi-Fi”“清除缓存”、删除素材或选片记录；卸载应用即删除全部应用状态数据（素材目录中的文件由用户自主管理）。
- **撤销同意**：在应用内“隐私与协议”页面点击“撤回授权”，或通过联系方式提出。撤回后应用清除同意记录、Web 访问密码、登录会话、已保存的 Wi-Fi 凭据及缓存，回到首次使用状态并停止一切数据处理。
- **注销账号**：本应用无账号体系，无需注销；卸载应用即删除全部本地数据。
- **响应时限**：通过下方渠道提出的请求，承诺 15 个工作日内答复。

- **Access**: all processed data is visible inside the app.
- **Correction**: Wi-Fi settings (self-hosted) and the media folder can be changed in-app; the access password can be reset by withdrawing consent.
- **Deletion**: clear saved Wi-Fi, cache, media, and curation records in-app; uninstalling removes all app state (files in the media folder are user-managed).
- **Withdrawal of consent**: use "Withdraw consent" on the in-app Privacy page or contact us. Withdrawing clears the consent record, web password, sessions, saved Wi-Fi credentials, and caches, returning the app to its first-run state and stopping all processing.
- **Account deletion**: not applicable — no account system; uninstalling deletes all local data.
- **Response time**: requests via the channels below are answered within 15 business days.

## 7. 用户控制与安全 / User Control and Security

用户可随时清除记住的 Wi-Fi、预览缓存及已下载素材。Wi-Fi 凭据以加密形式存储，文件权限设置为仅应用运行用户可读写；对素材与缓存文件的访问经路径合法性校验，不跟踪符号链接。请仅在可信局域网内使用应用，并为 Luna Sync 选择独立素材目录，不要与个人文件或其他应用数据混存。

Users can clear saved Wi-Fi credentials, preview cache, and downloaded media at any time. Wi-Fi credentials are stored encrypted with permissions restricted to the app runtime user; file access is path-validated and does not follow symbolic links. Use the app only on a trusted local network and select a dedicated Luna Sync media folder instead of mixing it with personal files or other application data.

## 8. 跨境传输说明 / Cross-Border Transfer

本应用所有个人信息仅存储和处理于用户自有设备本地，不存在向境外提供个人信息的行为；应用不提供任何云端服务。如用户自行将素材目录或应用数据迁移至境外服务器，属于用户自主行为，与本应用无关。

All personal information is stored and processed only on the user's own device. The app has no cloud service and does not transfer personal information across borders. Migrating the media folder or app data to an overseas server by yourself is a user action unrelated to the app.

## 9. 儿童信息说明 / Children's Information

本应用为相机素材备份工具，面向普通用户，不面向儿童运营，不主动收集儿童个人信息，亦无账号注册。如监护人发现本应用意外处理了儿童个人信息，可通过第 12 节渠道联系我们，核实后将立即删除。

The app is a camera media backup tool for general users. It is not directed at children, does not knowingly collect children's personal information, and has no account registration. If you believe a child's information was processed unintentionally, contact us through the channels in section 12 and we will delete it promptly.

## 10. 第三方组件 / Third-Party Components

应用使用 Docker、Flask、Pillow、ffmpeg 等开源组件。组件受各自开源许可证约束；本应用不会借助这些组件主动将用户信息发送到外部服务。

The app uses open-source components such as Docker, Flask, Pillow, and ffmpeg. Those components are governed by their respective licenses; the app does not use them to actively transmit user information to external services.

## 11. 政策更新 / Policy Updates

本政策如有实质性变更，将在应用内以弹窗形式重新征求您的同意；您也可以随时在应用内“隐私与协议”查看当前版本。拒绝更新后的政策时，可撤回授权并停止使用。

Material changes will be presented in-app for consent again; the current version is always available under "Privacy & Terms" in the app. You may withdraw consent if you do not accept an updated policy.

## 12. 投诉与举报渠道 / Complaints and Reporting

如您对本应用个人信息处理有任何疑问、意见或投诉，可通过以下渠道联系我们，承诺 15 个工作日内答复：

- 个人信息保护负责人邮箱：1981940361@qq.com
- GitHub Issues：https://github.com/hongjiahao371-pixel/luna-sync/issues

您还可以通过绿联云应用中心的应用详情页或平台客服对应用进行投诉、举报，平台将按规定受理处理。

For questions, comments, or complaints about personal information handling, contact us via 1981940361@qq.com or [GitHub Issues](https://github.com/hongjiahao371-pixel/luna-sync/issues) — we respond within 15 business days. You may also report the app through the UGREEN App Center's app detail page or platform customer service.
