# UGOS 应用商店 · 版本更新说明 / Release Notes

提交新版本时复制到开发者门户的「版本更新说明」字段（上限 5000 字符）。
粘贴前删除尚未包含在当前构建中的条目。

## v1.1.0 (build 9)

zh-CN:

1. 新增 Live 动态图预览：点开先看静帧，可一键切换播放动态片段
2. 新增 360 全景照片球面环视：按住拖动变换视角，滚轮缩放视野
3. 相机文件列表支持表头排序（文件名/来源/类型/拍摄日期/大小），默认最新拍摄在前
4. LRV 与视频文件在列表中直接显示缩略图
5. Web 界面升级为加密访问（HTTPS），首次打开请按浏览器提示确认证书
6. 首次使用提供「同意/不同意」的明确选择，应用内可随时查看隐私政策与用户协议
7. 修复通过 UGOS 内网穿透远程访问应用失败的问题
8. 首次使用流程调整：先完成隐私授权（可选择同意或不同意），再设置访问密码
9. 新增本地照片挑选模式：逐张全屏审片，入选/落选一键标记（NAS 持久保存），支持筛选查看与一键导出精选、批量删除落选
10. 新增相册与项目：按拍摄日期自动归组、可命名保存项目并随时添加照片
11. 新增「自动选片」：本地智能分析照片质量并按连拍聚类推荐最佳照片，一键应用为入选
12. 导出支持一键画质优化与自定义文字水印
13. 自动选片可检测疑似模糊照片并一键标记为落选
14. 新增「自动剪辑」：本地检测视频高光片段，与入选照片自动组成配乐成片（15/30/60 秒）
12. 删除改为进回收站（保留 7 天可恢复），新增回收站管理面板
13. 挑选模式新增两张对比 (C) 与入选照片幻灯片放映 (P)
14. 照片预览显示拍摄参数（机型/镜头/焦距/光圈/快门/ISO）

en-US:

1. Live Photo preview: open the still and play the motion clip with one tap
2. 360 panorama look-around with a perspective view; drag to rotate, scroll to zoom
3. Sort the camera file list by name, source, type, date, or size; newest first by default
4. LRV and video files now show thumbnails in the lists
5. The web console now uses encrypted HTTPS access; confirm the certificate prompt on first visit
6. First launch offers an explicit agree/decline choice, with the privacy policy and terms available in-app at any time
7. Fixed remote access through the UGOS relay failing to open the app
8. First-run flow now asks for privacy consent (agree or decline) before setting the access password
9. New local photo culling mode: full-screen review with keep/reject marks persisted on the NAS, plus filtering, one-tap export of keepers, and bulk delete of rejects
10. Albums and projects: automatic grouping by shoot date, plus named projects you can keep adding photos to
11. Auto select: fully local photo quality analysis with burst clustering, one tap to apply the recommended keeps
12. Exports support one-tap auto enhance and custom text watermark
13. Auto select detects suspected blurry photos for one-tap rejection
14. Auto cut: detects video highlights locally and assembles a scored montage with your photos (15/30/60 s)
12. Deleted files now go to a trash folder kept for 7 days, with a trash management panel
13. Cull mode adds side-by-side compare (C) and a slideshow of kept photos (P)
14. Photo preview now shows shooting parameters (model/lens/focal/aperture/shutter/ISO)

## v1.1.0 (build 20) · 安全与合规强化 / Security & compliance hardening

zh-CN:

1. 隐私政策全面扩充（2026-09-25 版）：新增运营者信息、投诉与举报渠道、已收集个人信息清单、与第三方共享个人信息清单、用户权利及行权途径、跨境传输说明与儿童信息说明
2. 新增「撤回授权」：在隐私政策页一键撤回同意并清除应用数据（素材文件不受影响），应用回到首次使用状态
3. 商店版彻底移除应用内 Wi-Fi 表单与扫描功能（界面与后端接口均不再提供），隐私描述与实际行为保持一致
4. 自部署版记住的 Wi-Fi 密码改为加密存储，不再以明文形式落盘
5. 相机连接认证数据改为按设备配置（首次运行自动生成独立配置，可按设备覆盖），不再完全依赖内置固定值
6. 修复素材目录中存在符号链接时可能被用于读取目录之外文件的安全问题

en-US:

1. Privacy policy greatly expanded (2026-09-25): operator information, complaint & reporting channels, list of collected personal information, list of third-party sharing, user rights with real exercise paths, cross-border transfer statement, and children's information statement
2. New "Withdraw consent" action: revoke consent and clear app data from the privacy page (media files are untouched), returning the app to its first-run state
3. The store version has fully removed the in-app Wi-Fi form and scanning (hidden in the UI and disabled in the backend), keeping the policy consistent with actual behavior
4. Remembered Wi-Fi passwords in self-hosted deployments are now stored encrypted instead of in plaintext
5. Camera connection authentication data is now per-device (auto-generated on first run, overridable per device) instead of relying solely on a compiled-in constant
6. Fixed a security issue where symbolic links inside the media folder could be used to read files outside the folder
