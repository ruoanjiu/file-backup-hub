declare global {
  interface Window {
    pywebview?: { api?: Record<string, (...args: unknown[]) => Promise<unknown>> }
  }
}

const mockClient = {
  mode: 'client',
  agent: { status: 'RUNNING', running: true, mode: 'embedded', scheduled_tasks: 2 },
  device: { device_id: 'home-mac', display_name: '家里 Mac' },
  servers: [
    { id: 'server-a', name: 'Server A', enabled: true, status: 'ok', usage: '2.35 TB / 4 TB', percent: 58 },
    { id: 'server-b', name: 'Server B', enabled: true, status: 'ok', usage: '1.62 TB / 4 TB', percent: 40 },
    { id: 'server-c', name: 'Server C', enabled: false, status: 'disabled', usage: null, percent: 0 },
  ],
  devices: [
    { device_id: 'home-mac', display_name: '家里 Mac', status: 'online', paired: true },
    { device_id: 'office-pc', display_name: '办公室电脑', status: 'online', paired: true },
    { device_id: 'office-pc-a', display_name: '办公电脑 A', status: 'offline', paired: true, enabled: false },
  ],
  tasks: [
    { name: '日志和日报', schedule: '每天 04:00', sources: 3, last_status: 'SUCCESS' },
  ],
  inbox: [
    { transfer_id: 'transfer_demo_01', sender_device_id: 'office-pc', status: 'AVAILABLE', file_count: 3, total_size: 134217728, server_id: 'server-a' },
  ],
  backups: [
    { backup_id: 'home-mac__logs__20260829_102400__a1b2c3d4', task_name: '日志和日报', created_at: '2026-08-29 10:24', copy_status: 'HEALTHY', file_count: 218 },
  ],
  inbox_dir: '~/Downloads/FileBackup Inbox',
}

const mockServer = {
  mode: 'server',
  server: { id: 'server-a', status: 'ok', endpoint: 'http://127.0.0.1:8000', data_dir: '/Users/Shared/FileBackupServer' },
  devices: mockClient.devices,
  server_inbox: [
    { transfer_id: 'transfer_to_server_01', sender_device_id: 'office-pc', receiver_device_id: '__server__', status: 'AVAILABLE', file_count: 2, total_size: 4096, created_at: '2026-09-01 17:00' },
  ],
  server_inbox_dir: '/Users/Shared/FileBackupServer/transfers/server-inbox',
  storage: [
    { name: '备份包', path: '/Users/Shared/FileBackupServer/storage', size: '2.35 TB' },
    { name: 'Manifest', path: '/Users/Shared/FileBackupServer/manifests', size: '126 MB' },
    { name: '文件中转', path: '/Users/Shared/FileBackupServer/transfers', size: '1.8 GB' },
    { name: 'Trash', path: '/Users/Shared/FileBackupServer/trash', size: '0 B' },
  ],
}

function isServerPreview() {
  return new URLSearchParams(window.location.search).get('mode') === 'server'
}

export async function bridge<T = unknown>(method: string, ...args: unknown[]): Promise<T> {
  const api = window.pywebview?.api?.[method]
  if (api) return (await api(...args)) as T
  if (method === 'bootstrap' && new URLSearchParams(window.location.search).get('setup') === '1') {
    return { mode: 'client', configured: false } as T
  }
  if (method === 'bootstrap' && new URLSearchParams(window.location.search).get('emptyServers') === '1') {
    return { ...mockClient, servers: [], devices: [], inbox: [], backups: [] } as T
  }
  if (method === 'bootstrap') return (isServerPreview() ? mockServer : mockClient) as T
  await new Promise((resolve) => setTimeout(resolve, 450))
  if (method === 'start_agent') return { status: 'STARTING', running: true, mode: 'embedded', scheduled_tasks: 2 } as T
  if (method === 'stop_agent') return { status: 'STOPPED', running: false, mode: 'embedded', scheduled_tasks: 2 } as T
  if (method === 'restart_agent') return { status: 'STARTING', running: true, mode: 'embedded', scheduled_tasks: 2 } as T
  if (method === 'choose_files') return ['/Users/demo/Documents/项目计划书.pdf'] as T
  if (method === 'choose_folder') return ['/Users/demo/Documents/设计素材包'] as T
  if (method === 'choose_server_data_dir') return ['/Volumes/Backup/FileBackupServer'] as T
  if (method === 'create_pairing_code') return { code: '683291', expires_at: '5 分钟后', server_id: 'server-a' } as T
  if (method === 'open_path') return { opened: true, path: args[0], demo: true } as T
  if (method === 'reject_transfer') return { status: 'REJECTED', transfer_id: args[0] } as T
  if (method === 'receive_server_transfer') return { status: 'COMPLETED', transfer_id: args[0], destination_path: '/Users/Shared/FileBackupServer/transfers/server-inbox/demo' } as T
  if (method === 'save_server_settings') return { status: 'RESTARTING', restarted: true, server_id: args[0], data_dir: args[1] } as T
  if (method === 'revoke_device') return { status: 'REVOKED', device_id: args[0], enabled: false } as T
  if (method === 'leave_server') {
    const invalid = new URLSearchParams(window.location.search).get('invalidToken') === '1'
    return { status: invalid ? 'AUTH_INVALID' : 'LEFT', server_id: args[0], enabled: invalid, local_only_available: invalid } as T
  }
  if (method === 'leave_server_local') return { status: 'LEFT_LOCAL_ONLY', server_id: args[0], enabled: false, remote_revoked: false } as T
  if (method === 'delete_server') return { status: 'DELETED_LOCAL', server_id: args[0], remaining_servers: 2 } as T
  if (method === 'add_server') return { status: 'PAIRED', server_id: (args[0] as Record<string, unknown>)?.server_id } as T
  return { status: 'SUCCESS', demo: true } as T
}

export function waitForBridge(): Promise<void> {
  if (window.pywebview?.api) return Promise.resolve()
  if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
    return Promise.resolve()
  }
  return new Promise((resolve) => {
    window.addEventListener('pywebviewready', () => resolve(), { once: true })
    setTimeout(resolve, 5000)
  })
}
