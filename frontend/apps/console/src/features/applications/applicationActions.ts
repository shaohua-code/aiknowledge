/**
 * 应用工作台的下一步行动计算。
 *
 * 本模块只转换服务端真实状态，不发起网络请求；页面、抽屉或后续通知中心
 * 可共享这一规则，避免出现相同资源在不同入口给出相互矛盾的待办。
 */
export interface ApplicationAction {
  key: 'knowledge' | 'failed-runs' | 'capability' | 'test'
  title: string
  description: string
  path: 'knowledge' | 'automation' | 'intelligence'
}

export interface ApplicationActionInput {
  collectionCount: number
  failedRunCount: number
  // undefined 表示能力接口仍在加载，不能据此生成“创建能力”的错误待办。
  answerProfileCount: number | undefined
  totalRequestCount: number
}

/** 根据知识、任务、能力与调用记录生成按处理优先级排序的行动项。 */
export function buildApplicationActions({
  collectionCount,
  failedRunCount,
  answerProfileCount,
  totalRequestCount
}: ApplicationActionInput): ApplicationAction[] {
  const actions: ApplicationAction[] = []

  if (collectionCount === 0) {
    actions.push({
      key: 'knowledge',
      title: '导入首批知识',
      description: '当前应用还没有知识集合，先让回答具备可追溯的依据。',
      path: 'knowledge'
    })
  }
  if (failedRunCount > 0) {
    actions.push({
      key: 'failed-runs',
      title: `处理 ${failedRunCount} 个失败任务`,
      description: '查看失败阶段与建议，修复后从运行记录重新尝试。',
      path: 'automation'
    })
  }
  if (answerProfileCount === 0) {
    actions.push({
      key: 'capability',
      title: '创建 AI 能力',
      description: '配置检索和回答策略，再使用真实知识进行调试。',
      path: 'intelligence'
    })
  }
  if (answerProfileCount && totalRequestCount === 0) {
    actions.push({
      key: 'test',
      title: '测试已配置能力',
      description: '尚未发现真实调用记录，先通过调试验证输入、答案与引用。',
      path: 'intelligence'
    })
  }
  return actions
}
