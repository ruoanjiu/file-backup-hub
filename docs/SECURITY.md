# 安全说明

## 不要提交的内容

以下内容不能提交到 GitHub：

- `.env`
- `config.yaml`
- 真实 admin token
- 真实 client token
- 备份数据和 SQLite 数据库
- `dist/`、`build/`、`.venv-client-build/`
- 本地 zip 包、exe 包、临时代码备份目录

本仓库 `.gitignore` 已默认排除这些内容。

## Token 设计

Server 使用 Bearer Token：

- `SERVER_ADMIN_TOKEN`：管理员 token，可查看、下载、删除所有远端备份
- `CLIENT_TOKENS`：客户端 token，格式为 `machine_id:token`

建议：

- 每台电脑使用独立 token
- token 至少 32 字节随机值
- 不要复用 admin token 和 client token
- token 泄露后立即轮换

## 设备撤销

- 管理员撤销和 Client 自行退出均为软撤销，只更新配对设备的 `enabled` 状态；
- 已撤销设备的旧 Token 会立即鉴权失败；
- 不删除 Client 数据库行，也不删除备份、Manifest、Transfer、Trash 或任何原始文件；
- 已停用的同一 Device ID 可以使用新的六位一次性配对码重新启用，新 Token 会替换旧 Token 哈希；
- Client 只有在 Server 明确返回撤销成功后，才会清空本地对应 Server Token 并禁用该连接。
- 已退出且 Token 已清空的 Server 可以从 Client 本地配置中删除；该操作不会再次调用远端删除，也不会删除 Server 上的停用设备记录或任何历史数据。
- 自撤销返回 401/403 时，Client 可以在明确二次确认后仅执行本地退出；其他网络或 Server 错误不会自动清空 Token。

## 网络暴露

不建议直接将 `8000` 暴露到公网。正式部署建议：

- HTTPS
- 只开放 `443`
- 云安全组限制来源 IP
- Admin 页面限制固定 IP 访问
- Server 容器端口只绑定 `127.0.0.1`

## 数据安全

当前 Server 保存的是备份包和 manifest。Server 管理员或入侵者可以读取远端备份内容。高安全场景建议继续增强：

- Client 上传前加密
- Server 端备份数据盘加密
- 云盘快照
- 对象存储版本控制
- 删除审计和软删除

## 恢复安全

Client 恢复时会：

- 校验下载包 SHA256
- 校验 manifest
- 只允许恢复到 `restore.allowed_roots`
- 覆盖前创建 rollback 快照

不要把 `restore.allowed_roots` 设置为系统根目录。
