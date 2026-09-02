# 开发状态

## 已完成

- 从原项目的开源整理版建立干净源码基线，未带入 `.env`、构建缓存、EXE 和嵌套 Git。
- 保留原有 `manifest.json + bundle.tar.gz + SHA256 + rollback` 备份格式。
- 配置支持多个独立 Server，并兼容旧版单 Server 配置。
- 一次打包上传多台 Server，每个副本单独记录状态和重试次数。
- 副本不足时保留 outbox，可只补传失败 Server。
- 合并查询多台 Server 的备份版本并识别缺副本或哈希冲突。
- 校验和恢复支持指定 Server 或一致性检查后的自动回退。
- Client 移除远端删除入口；Server 删除默认关闭且只允许管理员。
- 修复旧版软删除覆盖 bundle 的缺陷，bundle 与 manifest 使用独立 trash 路径。
- Server 重复 init 对相同备份幂等，失败上传可以用同一 backup_id 重试。
- 增加 macOS/Windows Client 和 Server 构建入口。
- 增加独立后台 Server 进程及跨平台 Server Manager。
- 增加独立 Client Agent、启动时 outbox 补传和滚动日志，并生成独立可执行文件。
- 增加动态设备身份、六位一次性配对码、二维码、设备列表和设备改名。
- 增加 Server 中转式文件传送、接收确认、独立 Inbox、同名防覆盖和三层 SHA256 校验。
- 完成 Vue 3 + TypeScript + Vite 前端，并通过浏览器页面切换、首次设置、Client/Server模式和控制台检查。
- 按确认概念图重构为悬浮浅色导航轨，并重排 Client 总览页的 Server卡片、拖拽区、传输队列、设备栏和双备份时间线。
- 使用 pywebview 将 Vue 前端与 Python核心桥接，macOS Client.app、Server.app 均已打包启动。
- 完成真实 HTTP 端到端传送：上传、接收箱、接受、下载、逐文件校验和完成状态全部通过。
- macOS Server App 增加菜单栏常驻图标、运行状态、打开/启动/停止操作，关闭窗口后继续后台运行。
- 增加 Server 管理员撤销设备、Client 退出指定 Server、旧 Token 失效、已停用设备重新配对；整个流程不删除原始文件或历史备份。
- 同步 Windows 新功能到 macOS：文件直接发送到 Server、Server 收件箱校验保存、Client/Server 拒绝、退出后删除本地 Server 配置、无 Server 状态下重新添加，以及 macOS Client 菜单栏常驻。

## 下一阶段

- Windows Service、Windows Task Scheduler、macOS LaunchDaemon/LaunchAgent 安装器；当前 Agent 已可独立运行，但尚未自动注册到系统启动项。
- 系统 Keychain/Credential Manager 存储 Token；目前配置文件仍包含 Client Token。
- 持久化文件日志、日志轮转、磁盘空间预警和通知。
- Server 保留规则和 trash 到期清理。
- HTTPS/Tailscale 部署向导和防火墙检查。
- Windows x64 原机构建验证、macOS Developer ID 签名与公证。
- 长时间运行、睡眠唤醒、断网、磁盘满和大文件测试。
- 文件分块、断点续传和中转文件到期清理；当前传送为完整 tar.gz 包。
- 局域网设备直传；当前版本会自动选择可用 Server 中转。
- 桌面端二维码扫描；当前桌面端输入六位码，Server 已能显示标准二维码供后续移动端使用。
