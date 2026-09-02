export const SERVER_RECEIVER_ID = '__server__'

export type TransferState = {
  sender_device_id?: string
  receiver_device_id?: string
  file_count?: number
}

export function rejectTransferConfirmation(
  item: TransferState,
  receivingOnServer: boolean,
): string {
  const target = receivingOnServer ? 'Server 收件箱' : '本机接收箱'
  return `确认拒绝这次文件传送吗？\n\n该传送将从${target}待处理列表中移除；不会删除发送方的原始文件，也不会影响历史备份。`
}

export function receiverDisplayName(receiverDeviceId: string): string {
  return receiverDeviceId === SERVER_RECEIVER_ID ? '当前 Server' : receiverDeviceId
}
