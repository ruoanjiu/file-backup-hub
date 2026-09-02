from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["admin-ui"])


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/admin")


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML)


ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>File Backup Server</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d7dce2;
      --text: #1f2933;
      --muted: #667085;
      --accent: #1f6feb;
      --danger: #c7352b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      background: #172033;
      color: #fff;
    }
    header h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      letter-spacing: 0;
    }
    main {
      max-width: 1280px;
      margin: 18px auto;
      padding: 0 16px 24px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 14px;
      margin-bottom: 14px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: 1.6fr 0.8fr 0.8fr auto auto;
      gap: 10px;
      align-items: end;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }
    input {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 6px 8px;
      font: inherit;
      background: #fff;
    }
    button {
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 0 12px;
      font: inherit;
      background: #fff;
      cursor: pointer;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.danger {
      color: var(--danger);
      border-color: #e3a4a0;
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .status {
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
      min-height: 20px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      font-size: 12px;
      color: var(--muted);
      font-weight: 600;
      background: #fafbfc;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .badge {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 12px;
      background: #e9f2ff;
      color: #144d99;
      font-size: 12px;
    }
    .empty {
      padding: 28px;
      text-align: center;
      color: var(--muted);
    }
    @media (max-width: 900px) {
      .toolbar { grid-template-columns: 1fr; }
      table { font-size: 12px; }
      th:nth-child(5), td:nth-child(5) { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>File Backup Server</h1>
    <span id="health">Server OK</span>
  </header>
  <main>
    <section class="panel">
      <div class="toolbar">
        <div>
          <label for="token">Admin Token</label>
          <input id="token" type="password" autocomplete="off" placeholder="输入 SERVER_ADMIN_TOKEN" />
        </div>
        <div>
          <label for="machine">主机 ID 过滤</label>
          <input id="machine" placeholder="可选，例如 office-pc-01" />
        </div>
        <div>
          <label for="task">任务/任务过滤</label>
          <input id="task" placeholder="可选" />
        </div>
        <button class="primary" id="loadBtn">查询备份</button>
        <button id="saveTokenBtn">保存 Token</button>
      </div>
      <div class="status" id="status"></div>
    </section>

    <section class="panel">
      <table>
        <thead>
          <tr>
            <th style="width: 30%">Backup ID</th>
            <th style="width: 12%">主机</th>
            <th style="width: 12%">任务</th>
            <th style="width: 9%">状态</th>
            <th style="width: 14%">时间</th>
            <th style="width: 8%">文件数</th>
            <th style="width: 15%">操作</th>
          </tr>
        </thead>
        <tbody id="rows">
          <tr><td class="empty" colspan="7">输入 admin token 后点击查询备份</td></tr>
        </tbody>
      </table>
    </section>
  </main>

  <script>
    const tokenInput = document.getElementById("token");
    const machineInput = document.getElementById("machine");
    const taskInput = document.getElementById("task");
    const statusEl = document.getElementById("status");
    const rowsEl = document.getElementById("rows");
    const healthEl = document.getElementById("health");

    tokenInput.value = localStorage.getItem("fileBackupAdminToken") || "";

    function token() {
      return tokenInput.value.trim();
    }

    function headers() {
      return { "Authorization": `Bearer ${token()}` };
    }

    function setStatus(message) {
      statusEl.textContent = message || "";
    }

    async function checkHealth() {
      try {
        const response = await fetch("/health");
        const data = await response.json();
        healthEl.textContent = data.status === "ok" ? "Server OK" : "Server 异常";
      } catch {
        healthEl.textContent = "Server 不可用";
      }
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { ...(options.headers || {}), ...headers() },
      });
      if (!response.ok) {
        let detail = await response.text();
        try { detail = JSON.parse(detail).detail || detail; } catch {}
        throw new Error(`${response.status} ${detail}`);
      }
      return response;
    }

    function buildQuery() {
      const params = new URLSearchParams({ limit: "200", offset: "0" });
      if (machineInput.value.trim()) params.set("machine_id", machineInput.value.trim());
      if (taskInput.value.trim()) params.set("task_name", taskInput.value.trim());
      return params.toString();
    }

    async function loadBackups() {
      if (!token()) {
        setStatus("请先输入 Admin Token");
        return;
      }
      setStatus("查询中...");
      rowsEl.innerHTML = "";
      try {
        const response = await api(`/api/v1/backups?${buildQuery()}`);
        const data = await response.json();
        renderRows(data.items || []);
        setStatus(`共 ${data.total ?? data.items.length} 条备份`);
      } catch (error) {
        rowsEl.innerHTML = `<tr><td class="empty" colspan="7">${escapeHtml(error.message)}</td></tr>`;
        setStatus("查询失败");
      }
    }

    function renderRows(items) {
      if (!items.length) {
        rowsEl.innerHTML = '<tr><td class="empty" colspan="7">没有备份数据</td></tr>';
        return;
      }
      rowsEl.innerHTML = items.map(item => `
        <tr>
          <td>${escapeHtml(item.backup_id)}</td>
          <td>${escapeHtml(item.machine_id)}</td>
          <td>${escapeHtml(item.task_name)}</td>
          <td><span class="badge">${escapeHtml(item.status)}</span></td>
          <td>${escapeHtml(item.created_at || "")}</td>
          <td>${item.file_count ?? 0}</td>
          <td>
            <div class="actions">
              <button onclick="downloadBundle('${escapeJs(item.backup_id)}')">下载</button>
              <button onclick="downloadManifest('${escapeJs(item.backup_id)}')">Manifest</button>
              <button class="danger" onclick="deleteBackup('${escapeJs(item.backup_id)}')">删除</button>
            </div>
          </td>
        </tr>
      `).join("");
    }

    async function downloadBundle(backupId) {
      await downloadAuthed(`/api/v1/backups/${encodeURIComponent(backupId)}/bundle`, `${backupId}.tar.gz`);
    }

    async function downloadManifest(backupId) {
      await downloadAuthed(`/api/v1/backups/${encodeURIComponent(backupId)}/manifest`, `${backupId}.manifest.json`);
    }

    async function downloadAuthed(path, filename) {
      setStatus(`下载 ${filename} ...`);
      try {
        const response = await api(path);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setStatus(`已开始下载 ${filename}`);
      } catch (error) {
        setStatus(`下载失败: ${error.message}`);
      }
    }

    async function deleteBackup(backupId) {
      if (!confirm(`确认删除远端备份？\n${backupId}`)) return;
      setStatus(`删除 ${backupId} ...`);
      try {
        await api(`/api/v1/backups/${encodeURIComponent(backupId)}`, { method: "DELETE" });
        setStatus(`已删除 ${backupId}`);
        await loadBackups();
      } catch (error) {
        setStatus(`删除失败: ${error.message}`);
      }
    }

    function saveToken() {
      localStorage.setItem("fileBackupAdminToken", token());
      setStatus("Token 已保存到当前浏览器");
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }[char]));
    }

    function escapeJs(value) {
      return String(value).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
    }

    document.getElementById("loadBtn").addEventListener("click", loadBackups);
    document.getElementById("saveTokenBtn").addEventListener("click", saveToken);
    checkHealth();
    if (token()) {
      loadBackups();
    }
  </script>
</body>
</html>
"""
