/**
 * 判断任务运行页是否仍需轮询。
 * queued/running 是服务端入库状态机的非终态；成功和失败都等待用户下一次显式操作。
 */
export function hasActiveIngestionRuns(runs: Array<{ status: string }>): boolean {
  return runs.some((run) => run.status === 'queued' || run.status === 'running')
}
