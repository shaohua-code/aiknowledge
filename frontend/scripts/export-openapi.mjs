/**
 * 从后端 FastAPI 应用导出 OpenAPI 快照。
 *
 * 此脚本由前端工作区调用，按 Windows、POSIX 和 PATH 顺序寻找 Python，
 * 避免把某台开发机的 .venv/Scripts 路径固化为唯一可用方式。
 */
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const frontendRoot = resolve(import.meta.dirname, '..')
const apiRoot = resolve(frontendRoot, '../backend/services/api')
const exportScript = resolve(frontendRoot, '../backend/scripts/export_openapi.py')

// 优先复用仓库的隔离环境；CI 或 Linux 开发机可退回到明确配置或 PATH 中的 Python。
const candidates = [
  resolve(apiRoot, '.venv/Scripts/python.exe'),
  resolve(apiRoot, '.venv/bin/python'),
  process.env.PYTHON,
  'python3',
  'python'
].filter((value) => value && (value === 'python3' || value === 'python' || existsSync(value)))

let lastFailure = null
for (const python of candidates) {
  const result = spawnSync(python, [exportScript], {
    cwd: apiRoot,
    encoding: 'utf8',
    stdio: 'inherit'
  })
  if (result.status === 0) process.exit(0)
  // 某个候选解释器缺少项目依赖时继续尝试下一个，不吞掉最终失败信息。
  lastFailure = result.error ?? new Error(`${python} exited with ${result.status ?? 'unknown status'}`)
}

throw new Error(`无法导出 OpenAPI。请准备后端依赖环境后重试：${lastFailure?.message ?? '未找到 Python'}`)
