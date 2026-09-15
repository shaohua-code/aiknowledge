import { describe, expect, it } from 'vitest'
import { buildApplicationActions } from './applicationActions'

describe('buildApplicationActions', () => {
  it('应优先暴露空知识、失败任务和未配置能力这些真实接入缺口', () => {
    expect(
      buildApplicationActions({
        collectionCount: 0,
        failedRunCount: 2,
        answerProfileCount: 0,
        totalRequestCount: 0
      }).map((action) => action.key)
    ).toEqual(['knowledge', 'failed-runs', 'capability'])
  })

  it('能力接口仍在加载时，不应错误提示用户重新创建能力', () => {
    expect(
      buildApplicationActions({
        collectionCount: 1,
        failedRunCount: 0,
        answerProfileCount: undefined,
        totalRequestCount: 0
      })
    ).toEqual([])
  })

  it('已有能力且尚无调用记录时，应引导先进行调试验证', () => {
    expect(
      buildApplicationActions({
        collectionCount: 1,
        failedRunCount: 0,
        answerProfileCount: 1,
        totalRequestCount: 0
      }).map((action) => action.key)
    ).toEqual(['test'])
  })
})
