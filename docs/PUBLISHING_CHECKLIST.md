# GitHub 发布检查清单

发布前建议逐项确认：

- [ ] 已确认 `.env` 没有进入仓库
- [ ] 已确认真实 token、域名、公网 IP 没有进入仓库
- [ ] 已确认 `dist/`、`build/`、`.venv-client-build/` 没有进入仓库
- [ ] 已确认备份数据、SQLite 数据库、zip 包没有进入仓库
- [x] 已选择并添加 `LICENSE`
- [ ] 已检查 README 中的 Server URL 和 token 都是示例值
- [ ] 已执行测试

```bash
pytest
```

- [ ] 已检查 Docker Compose 配置

```bash
docker compose --env-file .env.example config --quiet
```

- [ ] 已确认生产部署不会使用示例 token

首次推送建议：

```bash
git init
git add .
git status
git commit -m "Initial open source release"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```
