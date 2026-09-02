export type AgentState = {
  status?: string
  running?: boolean
  mode?: string
  scheduled_tasks?: number
}

export function agentStatusLabel(agent?: AgentState): string {
  if (!agent) return '状态未知'
  if (agent.status === 'STARTING') return '正在启动'
  if (agent.status === 'RUNNING') return '自动备份运行中'
  if (agent.status === 'RUNNING_LEGACY') return '旧版 Agent 运行中'
  if (agent.status === 'NO_SERVER') return '等待添加 Server'
  if (agent.status === 'UNCONFIGURED') return '等待首次配置'
  if (agent.status === 'CONFIG_ERROR') return '配置异常'
  if (agent.status === 'STOP_FAILED') return '停止失败'
  return '已停止'
}

export function agentStatusTone(agent?: AgentState): 'success' | 'warning' {
  return agent?.running ? 'success' : 'warning'
}
