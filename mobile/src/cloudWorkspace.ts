/** Plain data projections for the optional cloud workspace; never executes history. */
import type { CloudState, CloudEntity } from './cloudSync.ts';

export type CloudRow = { id: string; payload: Record<string, unknown>; pending: boolean };
export function verifiedPhoneDevice(receipt: unknown, expectedId = ''): string {
  const device = (receipt as { device?: { id?: unknown; kind?: unknown } } | null)?.device;
  if (!device || device.kind !== 'phone' || typeof device.id !== 'string' ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(device.id) || (expectedId && device.id !== expectedId)) {
    throw new Error('凭据不是对应手机设备；请使用手机专用凭据，不使用管理员或桌面凭据。');
  }
  return device.id;
}
export function cloudRows(state: CloudState, entity: CloudEntity): CloudRow[] {
  const rows = new Map<string, CloudRow>();
  for (const item of state.records.filter(value => value.entity === entity)) {
    if (item.operation === 'delete') rows.delete(item.entity_id);
    else rows.set(item.entity_id, { id: item.entity_id, payload: item.payload, pending: false });
  }
  for (const item of state.outbox.filter(value => value.entity === entity && !value.conflict)) {
    if (item.operation === 'delete') rows.delete(item.entity_id);
    else rows.set(item.entity_id, { id: item.entity_id, payload: item.payload, pending: true });
  }
  return [...rows.values()].sort((a, b) => String(a.payload.created_at).localeCompare(String(b.payload.created_at)) || a.id.localeCompare(b.id));
}

const labels: Record<string, string> = {
  queued: '云端排队', leased: '电脑已领取，尚未确认启动', running: '电脑执行中', succeeded: '已完成',
  failed: '执行失败', interrupted: '已中断，不自动重放', cancelled: '已取消',
};
export function cloudTaskView(task: CloudState['tasks'][number]): { label: string; result: string; terminal: boolean } {
  if (task.local_state === 'pending') return { label: '待同步，仅保存在本机', result: '', terminal: false };
  if (task.local_state === 'conflict') return { label: '提交冲突，原任务保留', result: '', terminal: true };
  const receipt = task.result as { task?: { id?: unknown; workspace_id?: unknown; session_id?: unknown; prompt?: unknown;
    status?: unknown; result?: unknown } } | undefined;
  const value = receipt?.task;
  if (!value || value.id !== task.id || value.workspace_id !== task.workspace_id || value.session_id !== task.session_id ||
      value.prompt !== task.prompt || typeof value.status !== 'string' || !Object.hasOwn(labels, value.status)) {
    return { label: '云端已接收，当前执行状态尚未核实', result: '', terminal: false };
  }
  return { label: labels[value.status], result: typeof value.result === 'string' ? value.result.slice(0, 64000) : '',
    terminal: ['succeeded', 'failed', 'interrupted', 'cancelled'].includes(value.status) };
}
