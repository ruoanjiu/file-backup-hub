<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import appIcon from './assets/app-icon.png'
import {
  ArchiveRestore,
  ArrowRight,
  Bell,
  Boxes,
  Check,
  ChevronsLeft,
  ChevronsRight,
  ChevronRight,
  CircleAlert,
  Clock3,
  Database,
  FolderOpen,
  Gauge,
  HardDrive,
  Inbox,
  Laptop,
  LayoutDashboard,
  Link2,
  Monitor,
  MoreHorizontal,
  PackageCheck,
  RefreshCw,
  Send,
  Server,
  Settings,
  ShieldCheck,
  UploadCloud,
  UserRoundPlus,
  UserRoundX,
  LogOut,
  Trash2,
  X,
} from '@lucide/vue'
import { bridge, waitForBridge } from './bridge'

type AnyRecord = Record<string, any>
const SERVER_RECEIVER_ID = '__server__'

const data = ref<AnyRecord>({ mode: 'client', servers: [], devices: [], tasks: [], inbox: [], backups: [] })
const active = ref('overview')
const busy = ref('')
const toast = ref('')
const modal = ref('')
const selectedPaths = ref<string[]>([])
const selectedDevice = ref('')
const selectedServer = ref('auto')
const receiveTarget = ref('')
const pairingCode = ref('')
const pairingName = ref('')
const pairingServer = ref('')
const pairingQr = ref('')
const navCollapsed = ref(false)
const notificationOpen = ref(false)
const serverSettings = ref({ server_id: '', data_dir: '' })
const newTask = ref({ name: 'daily_backup', sources: [] as string[], scheduled: true, time: '04:00' })
const setup = ref({
  server_url: 'http://192.168.1.100:8000',
  server_id: 'server-a',
  server_name: 'Server A',
  pairing_code: '',
  device_id: 'my-device',
  display_name: '我的设备',
  data_dir: '',
  inbox_dir: '',
})
const addServerForm = ref({
  server_url: '',
  server_id: '',
  server_name: '',
  pairing_code: '',
  display_name: '',
})

const isServer = computed(() => data.value.mode === 'server')
const forceSetup = new URLSearchParams(window.location.search).get('setup') === '1'
const otherDevices = computed(() =>
  (data.value.devices || []).filter(
    (device: AnyRecord) => device.device_id !== data.value.device?.device_id,
  ),
)
const sendTargets = computed(() => [
  ...otherDevices.value,
  { device_id: SERVER_RECEIVER_ID, display_name: '当前 Server' },
])
const clientNav = [
  ['overview', '总览', LayoutDashboard],
  ['backup', '备份', Database],
  ['transfer', '传送', Send],
  ['restore', '恢复', ArchiveRestore],
  ['devices', '设备', Monitor],
  ['settings', '设置', Settings],
] as const
const serverNav = [
  ['overview', '总览', LayoutDashboard],
  ['devices', '配对', Monitor],
  ['receive', '接收', Inbox],
  ['storage', '存储', HardDrive],
  ['settings', '设置', Settings],
] as const
const navItems = computed(() => (isServer.value ? serverNav : clientNav))
const notifications = computed(() => {
  const items: AnyRecord[] = []
  if (isServer.value) {
    if (data.value.server?.status !== 'ok') {
      items.push({ kind: 'warning', title: 'Server 未运行', detail: '备份和文件传送暂时不可用。', target: 'overview' })
    }
    for (const item of data.value.inbox || []) {
      items.push({ kind: 'inbox', title: 'Server 收到待处理文件', detail: `${deviceName(item.sender_device_id)} 发来 ${item.file_count} 个文件。`, target: 'receive' })
    }
  } else {
    for (const item of data.value.inbox || []) {
      if (item.status === 'AVAILABLE') {
        items.push({ kind: 'inbox', title: '收到待接收文件', detail: `${deviceName(item.sender_device_id)} 发来 ${item.file_count} 个文件。`, target: 'transfer' })
      }
    }
    for (const server of data.value.servers || []) {
      if (server.status !== 'ok') {
        items.push({ kind: 'warning', title: `${server.name} 无法连接`, detail: server.url || '请检查 Server 和网络。', target: 'overview' })
      }
    }
  }
  return items
})
const serverSettingsDirty = computed(() =>
  serverSettings.value.server_id.trim() !== String(data.value.server?.id || '')
  || serverSettings.value.data_dir.trim() !== String(data.value.server?.data_dir || ''),
)

const pageMeta = computed(() => {
  const client: Record<string, [string, string]> = {
    overview: ['总览', '双 Server 备份、设备和文件传送集中在一个页面。'],
    backup: ['备份', '创建只读备份任务；同一个备份包自动复制到所有启用的 Server。'],
    transfer: ['文件传送', '选择文件和接收设备；当前版本自动使用可用 Server 中转。'],
    restore: ['恢复', '默认恢复到新目录，并允许自主选择备份来源 Server。'],
    devices: ['设备', '显示名称可以修改，内部 Device ID 始终保持不变。'],
    settings: ['设置', '接收路径只能由本机决定；发送者不能远程指定。'],
  }
  const server: Record<string, [string, string]> = {
    overview: ['总览', '后台服务独立运行，关闭管理窗口不会停止 Server。'],
    devices: ['设备与配对', '生成一次性配对码、查看设备并修改显示名称。'],
    receive: ['Server 文件接收', '接收或拒绝发给当前 Server 的文件；发送方原文件始终保持不变。'],
    storage: ['存储', '备份、Manifest、文件中转和 Trash 使用互相独立的目录。'],
    settings: ['设置', '保存配置后重启 Server 生效。'],
  }
  return (isServer.value ? server : client)[active.value] || ['File Backup', '']
})

function formatBytes(value = 0) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Number(value)
  let index = 0
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1 }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
}

function deviceName(id: string) {
  if (id === SERVER_RECEIVER_ID) return '当前 Server'
  return data.value.devices?.find((item: AnyRecord) => item.device_id === id)?.display_name || id
}

async function refresh() {
  busy.value = '正在同步状态…'
  try {
    data.value = await bridge('bootstrap')
    if (data.value.mode === 'server') {
      serverSettings.value = {
        server_id: String(data.value.server?.id || ''),
        data_dir: String(data.value.server?.data_dir || ''),
      }
    }
  }
  catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

function showToast(message: string) {
  toast.value = message
  setTimeout(() => { if (toast.value === message) toast.value = '' }, 3500)
}

function toggleNavigation() {
  navCollapsed.value = !navCollapsed.value
}

function openNotification(item: AnyRecord) {
  active.value = item.target || 'overview'
  notificationOpen.value = false
}

async function openStorage(item: AnyRecord) {
  try {
    await bridge('open_storage_path', item.path)
    showToast(`已打开 ${item.name} 目录`)
  } catch (error) { showToast(String(error)) }
}

async function chooseServerDataDir() {
  try {
    const paths = await bridge<string[]>('choose_server_data_dir', serverSettings.value.data_dir)
    if (paths?.[0]) serverSettings.value.data_dir = paths[0]
  } catch (error) { showToast(String(error)) }
}

async function saveServerSettings() {
  if (!serverSettingsDirty.value) return
  const warnings: string[] = []
  if (serverSettings.value.server_id.trim() !== String(data.value.server?.id || '')) {
    warnings.push('修改 Server ID 后，现有 Client 可能需要更新配置或重新配对。')
  }
  if (serverSettings.value.data_dir.trim() !== String(data.value.server?.data_dir || '')) {
    warnings.push('切换数据目录不会迁移或删除旧备份；新目录为空时会显示为一台新 Server。')
  }
  const message = `${warnings.join('\n\n')}\n\n是否继续保存${data.value.server?.status === 'ok' ? '并重启 Server' : ''}？`
  if (!window.confirm(message)) return
  busy.value = data.value.server?.status === 'ok' ? '正在保存并重启 Server…' : '正在保存 Server 配置…'
  try {
    const result = await bridge<AnyRecord>(
      'save_server_settings',
      serverSettings.value.server_id,
      serverSettings.value.data_dir,
    )
    showToast(result.restarted ? '配置已保存，Server 已重启' : '配置已保存')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function chooseSend(kind: 'files' | 'folder') {
  const method = kind === 'files' ? 'choose_files' : 'choose_folder'
  const paths = await bridge<string[]>(method)
  if (!paths?.length) return
  selectedPaths.value = paths
  selectedDevice.value = sendTargets.value[0]?.device_id || SERVER_RECEIVER_ID
  selectedServer.value = 'auto'
  modal.value = 'send'
}

async function sendNow() {
  if (!selectedPaths.value.length || !selectedDevice.value) return
  busy.value = '正在打包并发送…'
  modal.value = ''
  try {
    const result = await bridge<AnyRecord>('send', selectedPaths.value, selectedDevice.value, selectedServer.value)
    showToast(result.status === 'AVAILABLE' ? '文件已发送，等待对方接收' : JSON.stringify(result))
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function receive(item: AnyRecord) {
  receiveTarget.value = data.value.inbox_dir || ''
  selectedServer.value = item.server_id
  selectedPaths.value = [item.transfer_id]
  modal.value = 'receive'
}

async function receiveNow() {
  busy.value = '正在接收并校验…'
  modal.value = ''
  try {
    const result = await bridge<AnyRecord>('receive', selectedPaths.value[0], selectedServer.value, receiveTarget.value)
    showToast(result.status === 'COMPLETED' ? `已接收 ${result.received_count || ''} 个文件` : JSON.stringify(result))
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function rejectClientTransfer(item: AnyRecord) {
  if (!window.confirm('确定拒绝这个文件传送吗？\n\n拒绝后它不再显示在待接收列表，但不删除发送方原文件。')) return
  busy.value = '正在拒绝文件传送…'
  try {
    await bridge('reject_transfer', item.transfer_id, item.server_id)
    showToast('已拒绝，发送方原文件未删除')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function receiveOnServer(item: AnyRecord) {
  busy.value = '正在校验并保存到 Server 收件箱…'
  try {
    const result = await bridge<AnyRecord>('receive_server_transfer', item.transfer_id)
    showToast(`已接收 ${result.received_count} 个文件：${result.destination}`)
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function rejectServerTransfer(item: AnyRecord) {
  if (!window.confirm('确定拒绝这个 Server 收件吗？\n\n不会删除发送方原文件，中转包也暂时保留。')) return
  busy.value = '正在拒绝 Server 收件…'
  try {
    await bridge('reject_server_transfer', item.transfer_id)
    showToast('已拒绝，不会再显示于待接收列表')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function chooseReceiveTarget() {
  const paths = await bridge<string[]>('choose_receive_folder', receiveTarget.value)
  if (paths?.[0]) receiveTarget.value = paths[0]
}

async function runBackup(task: AnyRecord) {
  busy.value = `正在备份 ${task.name}…`
  try {
    const result = await bridge<AnyRecord>('run_backup', task.name)
    showToast(result.status === 'SUCCESS' ? '双 Server 备份成功' : `备份状态：${result.status}`)
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function addTaskSource(kind: 'files' | 'folder') {
  const paths = await bridge<string[]>(kind === 'files' ? 'choose_files' : 'choose_folder')
  if (paths?.length) newTask.value.sources.push(...paths)
}

async function saveTask() {
  modal.value = ''
  busy.value = '正在保存备份任务…'
  try {
    await bridge('create_task', newTask.value)
    showToast('备份任务已创建')
    newTask.value = { name: 'daily_backup', sources: [], scheduled: true, time: '04:00' }
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function verifyBackup(item: AnyRecord) {
  busy.value = '正在校验备份…'
  try {
    const result = await bridge<AnyRecord>('verify_backup', item.backup_id, selectedServer.value)
    showToast(`校验成功：${result.server_id || selectedServer.value}`)
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function restoreBackup(item: AnyRecord) {
  const folders = await bridge<string[]>('choose_receive_folder', '')
  if (!folders?.[0]) return
  busy.value = '正在恢复到新目录…'
  try {
    const result = await bridge<AnyRecord>('restore_backup', item.backup_id, selectedServer.value, folders[0])
    showToast(result.status === 'SUCCESS' ? `已恢复 ${result.restored_count} 个文件` : JSON.stringify(result))
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function pairDevice() {
  busy.value = '正在配对…'
  modal.value = ''
  try {
    await bridge('pair', pairingServer.value, pairingCode.value, pairingName.value)
    showToast('设备配对成功')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function renameDevice(device: AnyRecord) {
  const name = prompt('新的设备名称', device.display_name)
  if (!name) return
  try { await bridge('rename_device', device.device_id, name); showToast('设备名称已更新'); await refresh() }
  catch (error) { showToast(String(error)) }
}

async function revokeDevice(device: AnyRecord) {
  if (!device?.enabled) return
  if (!window.confirm(`确定解除设备“${device.display_name}”的配对吗？\n\n该设备 Token 会立即失效，但历史备份和原始文件都不会删除。`)) return
  busy.value = `正在解除 ${device.display_name} 的配对…`
  try {
    await bridge('revoke_device', device.device_id)
    showToast('设备已解除配对，旧 Token 已失效')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function removeDevice(device: AnyRecord) {
  if (device?.enabled) return
  const warning = `确定从设备列表移除“${device.display_name}”吗？\n\n只会删除这条已停用的设备登记。历史备份、Manifest、传输文件和 Client 原始文件都不会删除。`
  if (!window.confirm(warning)) return
  busy.value = `正在移除 ${device.display_name} 的设备记录…`
  try {
    const result = await bridge<AnyRecord>('remove_device', device.device_id)
    if (result.backups_deleted) throw new Error('安全检查失败：Server 报告删除了备份')
    showToast('设备记录已移除，历史备份仍保留')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function disconnectServer(server: AnyRecord) {
  if (!server?.enabled) return
  const lastWarning = data.value.servers.filter((item: AnyRecord) => item.enabled).length === 1 ? '\n\n这是最后一台启用的 Server；退出后备份会暂停，直到重新配对或添加 Server。' : ''
  const warning = `确定退出“${server.name}”吗？\n\n这会撤销本设备在该 Server 的 Token，并清除本机对应授权。不会删除原始文件或历史备份，其他 Server 不受影响。${lastWarning}`
  if (!window.confirm(warning)) return
  busy.value = `正在退出 ${server.name}…`
  try {
    const result = await bridge<AnyRecord>('disconnect_server', server.id)
    showToast(`已退出 ${server.name}，剩余 ${result.remaining_servers} 台 Server`)
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function deleteLocalServer(server: AnyRecord) {
  if (server.enabled) return
  if (!window.confirm(`确定删除“${server.name}”的本地配置吗？\n\n远端 Server 数据、本地备份任务、outbox 和历史备份都不会删除。`)) return
  busy.value = `正在删除 ${server.name} 的本地配置…`
  try {
    const result = await bridge<AnyRecord>('delete_server', server.id)
    showToast(`本地 Server 配置已删除，剩余 ${result.remaining_servers} 条`)
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

function openAddServer() {
  addServerForm.value = {
    server_url: '',
    server_id: '',
    server_name: '',
    pairing_code: '',
    display_name: data.value.device?.display_name || '',
  }
  modal.value = 'add-server'
}

async function addServerNow() {
  busy.value = '正在配对并添加 Server…'
  modal.value = ''
  try {
    await bridge('add_server', addServerForm.value)
    showToast('Server 已添加')
    await refresh()
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

async function saveInbox() {
  try { await bridge('save_inbox', data.value.inbox_dir); showToast('接收路径已保存') }
  catch (error) { showToast(String(error)) }
}

async function startServer() { await bridge('start_server'); setTimeout(refresh, 1000) }
async function stopServer() { await bridge('stop_server'); setTimeout(refresh, 800) }
async function createPairingCode() {
  try {
    const result = await bridge<AnyRecord>('create_pairing_code')
    data.value.pairing = result
    pairingQr.value = await QRCode.toDataURL(JSON.stringify({
      server_id: result.server_id,
      server_url: data.value.server?.endpoint,
      pairing_code: result.code,
    }), { width: 180, margin: 1, color: { dark: '#0d2038', light: '#ffffff' } })
  }
  catch (error) { showToast(String(error)) }
}

async function finishSetup() {
  busy.value = '正在配对并创建本机配置…'
  try {
    data.value = await bridge('first_setup', setup.value)
    showToast('设备配对成功')
  } catch (error) { showToast(String(error)) }
  finally { busy.value = '' }
}

onMounted(async () => { await waitForBridge(); await refresh() })
</script>

<template>
  <div v-if="data.configured === false || forceSetup" class="setup-shell">
    <section class="setup-visual">
      <div class="brand setup-brand"><img class="app-logo" :src="appIcon" alt="File Backup" /><span>File Backup</span></div>
      <div class="setup-copy"><span class="setup-kicker">首次设置</span><h1>输入一次性配对码，<br />把这台设备加入备份空间。</h1><p>完成后自动获得独立设备身份和 Server 授权，不需要手动复制 Token。</p><div class="setup-points"><span><ShieldCheck /> 源文件始终只读</span><span><Server /> 支持双 Server 副本</span><span><Send /> 支持设备间文件传送</span></div></div>
    </section>
    <section class="setup-form"><div class="setup-card"><h2>加入已有空间</h2><p>先在 Server Manager 中生成一个六位配对码。</p><label>Server URL</label><input v-model="setup.server_url" /><div class="two-col"><div><label>Server ID</label><input v-model="setup.server_id" /></div><div><label>名称</label><input v-model="setup.server_name" /></div></div><label>六位配对码</label><input v-model="setup.pairing_code" maxlength="6" placeholder="683291" class="pair-input" /><div class="two-col"><div><label>Device ID</label><input v-model="setup.device_id" /></div><div><label>设备名称</label><input v-model="setup.display_name" /></div></div><label>本地数据目录</label><input v-model="setup.data_dir" placeholder="留空时使用系统默认目录" /><label>默认接收箱</label><input v-model="setup.inbox_dir" placeholder="留空时使用 Downloads/FileBackup Inbox" /><button class="primary setup-action" @click="finishSetup"><Link2 :size="18" /> 配对并完成</button></div>
    </section>
    <div v-if="toast" class="toast"><Check :size="18" /> {{ toast }}</div>
    <div v-if="busy" class="busy-overlay"><div class="busy-card"><RefreshCw class="spin" :size="24" /><strong>{{ busy }}</strong></div></div>
  </div>
  <div v-else :class="['app-shell', { 'sidebar-is-collapsed': navCollapsed }]">
    <aside :class="['sidebar', { collapsed: navCollapsed }]">
      <img class="dock-brand" :src="appIcon" :alt="isServer ? 'Backup Server' : 'File Backup'" :title="isServer ? 'Backup Server' : 'File Backup'" />
      <nav>
        <button v-for="[key, label, Icon] in navItems" :key="key" :class="['nav-item', { active: active === key }]" @click="active = key">
          <component :is="Icon" :size="23" /><span>{{ label }}</span>
        </button>
      </nav>
      <div class="sidebar-spacer" />
      <div class="dock-avatar" :title="isServer ? data.server?.id : data.device?.display_name">
        <Laptop :size="24" />
        <i :class="{ warning: isServer && data.server?.status !== 'ok' }" />
      </div>
      <button class="dock-collapse" :title="navCollapsed ? '展开导航' : '收起导航'" @click="toggleNavigation"><ChevronsRight v-if="navCollapsed" :size="18" /><ChevronsLeft v-else :size="18" /></button>
    </aside>

    <main class="main">
      <header class="page-header">
        <div><h1>{{ pageMeta[0] }}</h1><p>{{ pageMeta[1] }}</p></div>
        <div class="header-actions"><div class="notification-wrap"><button class="icon-button notification-button" title="应用内通知" @click="notificationOpen = !notificationOpen"><Bell :size="19" /><i v-if="notifications.length" /></button><div v-if="notificationOpen" class="notification-panel"><div class="notification-heading"><strong>应用内通知</strong><small>{{ notifications.length ? `${notifications.length} 条需要关注` : '当前状态正常' }}</small></div><button v-for="item in notifications" :key="`${item.title}-${item.detail}`" class="notification-item" @click="openNotification(item)"><CircleAlert v-if="item.kind === 'warning'" :size="18" /><Inbox v-else :size="18" /><span><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></span><ChevronRight :size="16" /></button><div v-if="!notifications.length" class="notification-empty"><Check :size="20" /><span>暂无新通知</span></div></div></div><button class="icon-button" @click="refresh" title="刷新"><RefreshCw :size="18" :class="{ spin: !!busy }" /></button></div>
      </header>

      <template v-if="!isServer && active === 'overview'">
        <section class="dashboard-layout">
          <div class="dashboard-main">
            <section class="server-grid">
              <article v-for="server in data.servers" :key="server.id" class="card server-card">
                <div class="server-title-row"><h3>{{ server.name }}</h3><span class="server-health" :class="server.status === 'ok' ? 'success' : 'warning'"><Check v-if="server.status === 'ok'" :size="14" />{{ server.status === 'ok' ? '运行正常' : '无法连接' }}</span></div>
                <div class="server-visual-row"><div class="server-icon"><Server :size="25" /></div><span class="online-dot" :class="server.status === 'ok' ? '' : 'offline'" /></div>
                <div class="usage"><div class="usage-label"><span>{{ server.usage || '等待 Server 返回存储信息' }}</span><span>{{ server.percent || 0 }}%</span></div><div class="progress"><i :style="{ width: `${server.percent || 0}%` }" /></div></div>
                <p class="server-footnote">上次备份：{{ data.backups?.[0]?.created_at || '暂无记录' }}</p>
              </article>
            </section>

            <article class="drop-card concept-drop" @click="chooseSend('files')">
              <div class="drop-icon"><UploadCloud :size="38" /></div><h2>拖拽文件或文件夹到此处</h2><p>支持文件夹同步与设备间安全传送</p><button class="primary select-file-button" @click.stop="chooseSend('files')">选择文件</button><span><ShieldCheck :size="16" /> 只读取源文件，不删除、不移动、不修改</span>
            </article>

            <article class="card table-card transfer-queue-card"><div class="section-heading"><h3>传输队列</h3><button @click="active = 'transfer'">查看全部 <ChevronRight :size="16" /></button></div>
              <div class="table"><div class="tr queue-head"><span>文件 / 方向</span><span>设备</span><span>进度</span><span>大小</span><span></span></div>
                <div v-for="item in data.inbox" :key="item.transfer_id" class="tr queue-row"><span><Inbox :size="17" /> {{ item.file_count }} 个文件</span><strong>{{ deviceName(item.sender_device_id) }}</strong><div class="queue-progress"><i :style="{ width: item.status === 'COMPLETED' ? '100%' : '64%' }" /></div><span>{{ formatBytes(item.total_size) }}</span><div class="compact-actions"><button class="text-button" @click="receive(item)">接收</button><button class="text-button danger-text" @click="rejectClientTransfer(item)">拒绝</button></div></div>
                <div v-if="!data.inbox?.length" class="empty-row">暂时没有传输任务</div>
              </div>
            </article>
          </div>

          <aside class="dashboard-aside">
            <article class="card device-card"><div class="section-heading"><h3>设备</h3><button @click="active = 'devices'">查看全部 <ChevronRight :size="16" /></button></div>
              <div class="device-list"><div v-for="device in data.devices.slice(0, 4)" :key="device.device_id" class="device-row"><div class="device-icon"><Laptop :size="20" /></div><div><strong>{{ device.display_name }}</strong><small>{{ device.device_id }}</small></div><span class="online-dot" :class="device.status === 'offline' ? 'offline' : ''" /></div></div>
              <button class="add-device-link" @click="active = 'devices'">＋ 添加设备</button>
            </article>

            <article class="card timeline-card"><div class="section-heading"><h3>双重备份成功</h3><ShieldCheck :size="20" /></div>
              <div class="timeline"><div v-for="item in data.backups.slice(0, 3)" :key="item.backup_id" class="timeline-item"><i><Check :size="12" /></i><div><small>{{ item.created_at }}</small><strong>{{ item.task_name }}</strong><p>已同时备份到 Server A 和 Server B</p></div></div><div v-if="!data.backups?.length" class="timeline-empty">完成首次双 Server 备份后会显示在这里。</div></div>
              <button class="timeline-link" @click="active = 'restore'">查看备份历史 <ChevronRight :size="15" /></button>
            </article>
          </aside>
        </section>
      </template>

      <template v-else-if="!isServer && active === 'backup'">
        <div class="toolbar"><button class="primary" @click="modal = 'task'"><Database :size="17" /> 新建备份任务</button><span class="safe-note"><ShieldCheck :size="16" /> 备份源始终只读</span></div>
        <section class="task-grid"><article v-for="task in data.tasks" :key="task.name" class="card task-card"><div class="task-icon"><Database :size="22" /></div><div class="task-main"><h3>{{ task.name }}</h3><p>{{ task.schedule }} · {{ task.sources }} 个来源</p><div class="copy-line"><span><Check :size="15" /> Server A</span><span><Check :size="15" /> Server B</span></div></div><button class="primary" @click="runBackup(task)">立即备份</button></article><div v-if="!data.tasks?.length" class="card empty-card"><Database :size="30" /><h3>还没有备份任务</h3><p>创建任务后可以手动或按时自动备份。</p></div></section>
      </template>

      <template v-else-if="!isServer && active === 'transfer'">
        <div class="toolbar"><button class="primary" @click="chooseSend('files')"><Send :size="17" /> 发送文件</button><button @click="chooseSend('folder')"><FolderOpen :size="17" /> 发送文件夹</button></div>
        <article class="card table-card"><div class="section-heading"><h3>接收箱</h3><span class="muted">接收路径由本机决定</span></div><div class="table"><div class="tr transfer-head"><span>发送设备</span><span>文件数</span><span>大小</span><span>状态</span><span>Server</span><span></span></div><div v-for="item in data.inbox" :key="item.transfer_id" class="tr transfer-row"><strong>{{ deviceName(item.sender_device_id) }}</strong><span>{{ item.file_count }}</span><span>{{ formatBytes(item.total_size) }}</span><span class="status-text warning">{{ item.status }}</span><span>{{ item.server_id }}</span><div class="compact-actions"><button class="primary small" @click="receive(item)">接收</button><button class="danger-small" @click="rejectClientTransfer(item)">拒绝</button></div></div><div v-if="!data.inbox?.length" class="empty-row">接收箱为空</div></div></article>
      </template>

      <template v-else-if="!isServer && active === 'restore'">
        <div class="toolbar"><label>恢复来源</label><select v-model="selectedServer"><option value="auto">自动选择</option><option v-for="server in data.servers" :key="server.id" :value="server.id">{{ server.name }}</option></select><span class="safe-note"><ShieldCheck :size="16" /> 默认恢复到新目录</span></div>
        <article class="card table-card"><div class="section-heading"><h3>备份版本</h3></div><div class="table"><div class="tr backup-head"><span>任务</span><span>创建时间</span><span>副本状态</span><span>文件数</span><span></span></div><div v-for="item in data.backups" :key="item.backup_id" class="tr backup-row"><div><strong>{{ item.task_name }}</strong><small>{{ item.backup_id }}</small></div><span>{{ item.created_at }}</span><span class="status-text success">{{ item.copy_status }}</span><span>{{ item.file_count }}</span><div class="row-actions"><button @click="verifyBackup(item)">校验</button><button class="primary small" @click="restoreBackup(item)">恢复</button></div></div></div></article>
      </template>

      <template v-else-if="!isServer && active === 'devices'">
        <div class="toolbar"><button class="primary" @click="pairingName = data.device?.display_name || ''; pairingServer = data.servers?.[0]?.id || ''; modal = 'pair'"><Link2 :size="17" /> 输入配对码</button></div>
        <section class="device-grid"><article v-for="device in data.devices" :key="device.device_id" class="card device-tile"><div class="device-hero"><Monitor :size="28" /></div><div><h3>{{ device.display_name }}</h3><p>{{ device.device_id }}</p><span class="status-text" :class="device.status === 'offline' ? 'warning' : 'success'">● {{ device.status === 'offline' ? '离线' : '在线' }}</span></div><button v-if="device.device_id === data.device?.device_id" class="icon-button" @click="renameDevice(device)"><MoreHorizontal :size="18" /></button></article></section>
      </template>

      <template v-else-if="!isServer && active === 'settings'">
        <article class="card settings-card"><div class="setting-block"><div><h3>默认接收箱</h3><p>所有自动接收内容只会进入这个本机目录。</p></div><div class="path-control"><input v-model="data.inbox_dir" /><button @click="saveInbox">保存</button></div></div></article>
        <article class="card settings-card"><div class="section-heading"><h3>备份 Servers</h3><button class="add-server-button" @click="openAddServer"><UserRoundPlus :size="16" /> 添加 Server</button></div><p class="settings-hint">退出只撤销授权；之后可重新配对，也可删除本地配置。两者都不删除备份数据。</p><div v-for="server in data.servers" :key="server.id" class="server-setting"><Server :size="19" /><div><strong>{{ server.name }}</strong><small>{{ server.id }} · {{ server.url || server.endpoint }}</small></div><span class="copy-badge" :class="server.status === 'ok' ? 'success' : 'warning'">{{ server.status === 'ok' ? '正常' : server.status === 'revoked' ? '已退出' : '离线' }}</span><button v-if="server.enabled" class="danger-link" @click="disconnectServer(server)"><LogOut :size="15" /> 退出此 Server</button><div v-else class="server-local-actions"><button class="text-button" @click="pairingName = data.device?.display_name || ''; pairingServer = server.id; modal = 'pair'"><Link2 :size="15" /> 重新配对</button><button class="danger-link" @click="deleteLocalServer(server)"><Trash2 :size="15" /> 删除 Server</button></div></div><div v-if="!data.servers?.length" class="empty-server-settings"><Server :size="28" /><strong>没有 Server 配置</strong><p>添加 Server 后才能备份、恢复和传送文件。</p><button class="primary" @click="openAddServer">添加 Server</button></div></article>
      </template>

      <template v-else-if="isServer && active === 'overview'">
        <article class="card server-overview"><div class="server-status-icon" :class="data.server?.status === 'ok' ? 'online' : ''"><Server :size="34" /></div><div><p class="eyebrow">运行状态</p><h2>{{ data.server?.status === 'ok' ? 'Server 正常运行' : 'Server 未运行' }}</h2><p>{{ data.server?.endpoint }}</p></div><div class="server-actions"><button class="success-button" @click="startServer">启动 Server</button><button @click="stopServer">停止 Server</button></div></article>
        <section class="metric-grid"><article class="card metric"><ShieldCheck /><span>安全状态</span><strong>删除默认关闭</strong><small>设备独立 Token</small></article><article class="card metric"><HardDrive /><span>数据目录</span><strong>{{ data.server?.data_dir }}</strong><small>App 升级不影响数据</small></article><article class="card metric"><Gauge /><span>健康检查</span><strong>{{ data.server?.status === 'ok' ? '通过' : '等待启动' }}</strong><small>后台进程独立运行</small></article></section>
      </template>

      <template v-else-if="isServer && active === 'devices'">
        <div class="pair-layout"><article class="card pairing-card"><p class="eyebrow">一次性配对码</p><div class="pair-code-row"><img v-if="pairingQr" :src="pairingQr" alt="设备配对二维码" /><div><div class="pair-code">{{ data.pairing?.code ? `${data.pairing.code.slice(0,3)}  ${data.pairing.code.slice(3)}` : '— — —  — — —' }}</div><p>{{ data.pairing?.expires_at || '五分钟有效，只能使用一次' }}</p></div></div><button class="primary" @click="createPairingCode"><UserRoundPlus :size="17" /> 生成配对码和二维码</button></article><article class="card pair-help"><Link2 :size="28" /><h3>新设备如何加入</h3><ol><li>在这里生成一次性配对信息</li><li>桌面端输入 Server 地址和六位码</li><li>移动端后续可直接扫描二维码</li><li>确认设备名称后自动获得独立授权</li></ol></article></div>
        <section class="device-grid"><article v-for="device in data.devices" :key="device.device_id" class="card device-tile"><div class="device-hero"><Monitor :size="28" /></div><div><h3>{{ device.display_name }}</h3><p>{{ device.device_id }}</p><span class="status-text" :class="device.enabled ? 'success' : 'warning'">{{ !device.enabled ? '已停用（可移除）' : device.paired ? '已配对' : '兼容授权' }}</span></div><div class="device-tile-actions"><button class="icon-button" title="修改名称" @click="renameDevice(device)"><MoreHorizontal :size="18" /></button><button v-if="device.paired && device.enabled" class="icon-button revoke-button" title="解除配对" @click="revokeDevice(device)"><UserRoundX :size="17" /></button><button v-else-if="device.paired && !device.enabled" class="icon-button remove-record-button" title="移除设备记录" @click="removeDevice(device)"><Trash2 :size="17" /></button></div></article></section>
      </template>

      <template v-else-if="isServer && active === 'receive'">
        <article class="card table-card"><div class="section-heading"><h3>Server 待接收文件</h3><span class="muted">接收后保存到 transfers/server-inbox</span></div><div class="table"><div class="tr server-inbox-head"><span>发送设备</span><span>文件数</span><span>大小</span><span>状态</span><span>操作</span></div><div v-for="item in data.inbox" :key="item.transfer_id" class="tr server-inbox-row"><strong>{{ deviceName(item.sender_device_id) }}</strong><span>{{ item.file_count }}</span><span>{{ formatBytes(item.total_size) }}</span><span class="status-text warning">{{ item.status }}</span><div class="compact-actions"><button class="primary small" @click="receiveOnServer(item)">接收并校验</button><button class="danger-small" @click="rejectServerTransfer(item)">拒绝</button></div></div><div v-if="!data.inbox?.length" class="empty-row">Server 收件箱为空</div></div></article>
      </template>

      <template v-else-if="isServer && active === 'storage'">
        <section class="storage-grid"><button v-for="item in data.storage" :key="item.name" class="card storage-card" :aria-label="`打开 ${item.name} 目录`" @click="openStorage(item)"><div class="storage-icon"><Boxes :size="23" /></div><div><p>{{ item.name }}</p><h3>{{ item.size }}</h3><small>{{ item.path }}</small></div><ChevronRight :size="19" /></button></section>
      </template>

      <template v-else-if="isServer && active === 'settings'">
        <article class="card settings-card server-settings-card"><div class="section-heading"><h3>Server 配置</h3><span class="copy-badge" :class="data.server?.status === 'ok' ? 'success' : 'warning'">{{ data.server?.status === 'ok' ? '运行中' : '已停止' }}</span></div><div class="setting-block"><div><h3>Server ID</h3><p>用于区分两台独立备份 Server；修改后现有 Client 可能需要重新配对。</p></div><div class="path-control"><input v-model="serverSettings.server_id" maxlength="128" placeholder="server-1" /></div></div><div class="setting-block"><div><h3>数据目录</h3><p>备份、Manifest、数据库、中转与 Trash 均保存在此目录下。</p></div><div class="path-control"><input v-model="serverSettings.data_dir" placeholder="/Users/Shared/FileBackupServer" /><button @click="chooseServerDataDir"><FolderOpen :size="17" /> 选择目录</button></div></div><div class="setting-warning"><CircleAlert :size="19" /><div><strong>切换目录不会自动迁移数据</strong><p>旧数据会保留在原目录，不会删除；如需迁移，应先停止 Server 并完整复制原目录。</p></div></div><div class="server-settings-actions"><button @click="refresh">取消修改</button><button class="primary" :disabled="!serverSettingsDirty" @click="saveServerSettings">{{ data.server?.status === 'ok' ? '保存并重启 Server' : '保存配置' }}</button></div></article>
      </template>
    </main>

    <div v-if="toast" class="toast"><Check :size="18" /> {{ toast }}</div>
    <div v-if="busy" class="busy-overlay"><div class="busy-card"><RefreshCw class="spin" :size="24" /><strong>{{ busy }}</strong></div></div>

    <div v-if="modal" class="modal-backdrop" @click.self="modal = ''">
      <div class="modal">
        <button class="modal-close" @click="modal = ''"><X :size="19" /></button>
        <template v-if="modal === 'send'"><h2>发送文件</h2><p>发送只读取源文件；可发给其他 Client，也可直接保存到当前 Server。</p><div class="selected-files"><div v-for="path in selectedPaths" :key="path"><PackageCheck :size="17" /><span>{{ path }}</span></div></div><label>接收目标</label><select v-model="selectedDevice"><option v-for="device in sendTargets" :key="device.device_id" :value="device.device_id">{{ device.display_name }} · {{ device.device_id }}</option></select><label>{{ selectedDevice === SERVER_RECEIVER_ID ? '目标 Server' : '中转 Server' }}</label><select v-model="selectedServer"><option value="auto">自动选择</option><option v-for="server in data.servers.filter((item: AnyRecord) => item.enabled !== false)" :key="server.id" :value="server.id">{{ server.name }}</option></select><button class="primary modal-action" @click="sendNow"><Send :size="18" /> 发送</button></template>
        <template v-else-if="modal === 'receive'"><h2>接收文件</h2><p>保存位置只能由本机选择；不会覆盖已有同名内容。</p><label>保存到</label><div class="path-control"><input v-model="receiveTarget" /><button @click="chooseReceiveTarget"><FolderOpen :size="17" /> 选择</button></div><button class="primary modal-action" @click="receiveNow"><Inbox :size="18" /> 接收并校验</button></template>
        <template v-else-if="modal === 'task'"><h2>新建备份任务</h2><p>把想备份的文件夹或单文件添加进来，Server内部路径由系统管理。</p><label>任务名称</label><input v-model="newTask.name" /><div class="source-actions"><button @click="addTaskSource('folder')"><FolderOpen :size="17" /> 添加文件夹</button><button @click="addTaskSource('files')"><PackageCheck :size="17" /> 添加文件</button></div><div class="selected-files"><div v-for="(path, index) in newTask.sources" :key="path"><ShieldCheck :size="17" /><span>{{ path }}</span><button @click="newTask.sources.splice(index,1)"><X :size="15" /></button></div></div><label class="check-row"><input v-model="newTask.scheduled" type="checkbox" /> 每天自动备份</label><label v-if="newTask.scheduled">备份时间</label><input v-if="newTask.scheduled" v-model="newTask.time" type="time" /><button class="primary modal-action" @click="saveTask">保存任务</button></template>
        <template v-else-if="modal === 'pair'"><h2>输入配对码</h2><p>配对码由 Server Manager生成，五分钟有效且只能使用一次。</p><label>Server</label><select v-model="pairingServer"><option v-for="server in data.servers" :key="server.id" :value="server.id">{{ server.name }}</option></select><label>六位配对码</label><input v-model="pairingCode" maxlength="6" placeholder="683291" /><label>设备名称</label><input v-model="pairingName" /><button class="primary modal-action" @click="pairDevice"><Link2 :size="18" /> 配对设备</button></template>
        <template v-else-if="modal === 'add-server'"><h2>添加 Server</h2><p>使用对应 Server Manager 生成的六位一次性配对码。</p><label>Server URL</label><input v-model="addServerForm.server_url" placeholder="http://192.168.1.100:8000" /><div class="two-col"><div><label>Server ID</label><input v-model="addServerForm.server_id" placeholder="server-home" /></div><div><label>显示名称</label><input v-model="addServerForm.server_name" placeholder="Home Server" /></div></div><label>六位配对码</label><input v-model="addServerForm.pairing_code" maxlength="6" placeholder="683291" /><button class="primary modal-action" @click="addServerNow"><Link2 :size="18" /> 配对并添加</button></template>
      </div>
    </div>
  </div>
</template>
