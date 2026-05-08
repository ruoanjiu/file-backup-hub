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
