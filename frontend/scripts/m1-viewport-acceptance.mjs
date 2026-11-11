/**
 * M1 多视口截图验收脚本。
 *
 * 按 1440、1280、1024、768 宽度截取控制台页面。
 * 优先使用 Playwright 驱动本机 Chrome；未安装 Playwright 时改用 Chrome headless，
 * 仍然保存同一组登录页截图。
 *
 * 登录后的应用概览、知识中心和能力调试需要可用 API 与管理员密码。
 * 缺少这些条件时只验收公开登录页，并在报告中标明未覆盖页面。
 *
 * 用法：
 *   node frontend/scripts/m1-viewport-acceptance.mjs
 * 环境变量：
 *   CONSOLE_BASE_URL  控制台地址，默认 http://127.0.0.1:5173
 *   M1_OUTPUT_DIR     截图目录，默认 docs/acceptance/m1-viewports
 */
import { execFile } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '../..')
const baseUrl = (process.env.CONSOLE_BASE_URL || 'http://127.0.0.1:5173').replace(/\/$/, '')
const outputDir = path.resolve(repoRoot, process.env.M1_OUTPUT_DIR || 'docs/acceptance/m1-viewports')

/** 执行书要求覆盖的四个宽度。高度取该宽度下的常见窗口，避免只截一条横条。 */
const VIEWPORTS = [
  { name: '1440', width: 1440, height: 900 },
  { name: '1280', width: 1280, height: 800 },
  { name: '1024', width: 1024, height: 768 },
  { name: '768', width: 768, height: 1024 }
]

const AUTHENTICATED_PAGES = [
  { name: 'overview', path: '/applications/{applicationId}/{environmentId}/overview' },
  { name: 'knowledge', path: '/applications/{applicationId}/{environmentId}/knowledge' },
  { name: 'intelligence', path: '/applications/{applicationId}/{environmentId}/intelligence' }
]

/**
 * 确认控制台地址可访问。Vite 未启动时给出明确失败，而不是保存空白图。
 */
async function assertConsoleReachable() {
  const response = await fetch(`${baseUrl}/login`, { redirect: 'follow' })
  if (!response.ok) {
    throw new Error(`控制台返回 HTTP ${response.status}：${baseUrl}/login`)
  }
}

/**
 * 用 Playwright 截图。本机已有 Chrome 时不额外下载浏览器。
 * @returns {Promise<boolean>} 是否成功使用 Playwright
 */
async function captureWithPlaywright(pagePath) {
  let playwright
  try {
    playwright = await import('playwright')
  } catch {
    try {
      // 允许只用 playwright-core 搭配本机 Chrome，不必再下载浏览器。
      playwright = await import('playwright-core')
    } catch {
      return false
    }
  }
  const browser = await playwright.chromium.launch({
    channel: 'chrome',
    headless: true
  })
  try {
    for (const viewport of VIEWPORTS) {
      const page = await browser.newPage({ viewport })
      await page.goto(`${baseUrl}${pagePath}`, { waitUntil: 'networkidle' })
      const file = path.join(outputDir, `login-${viewport.name}.png`)
      await page.screenshot({ path: file, fullPage: true })
      await page.close()
    }
  } finally {
    await browser.close()
  }
  return true
}

/**
 * Playwright 不可用时，用系统 Chrome 的 headless 截图参数完成同一组视口。
 */
async function captureWithChrome(pagePath) {
  const chrome = process.env.CHROME_PATH || 'google-chrome'
  for (const viewport of VIEWPORTS) {
    const file = path.join(outputDir, `login-${viewport.name}.png`)
    await execFileAsync(chrome, [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      '--no-sandbox',
      `--window-size=${viewport.width},${viewport.height}`,
      `--screenshot=${file}`,
      `${baseUrl}${pagePath}`
    ], { timeout: 30000 })
  }
}

async function main() {
  await mkdir(outputDir, { recursive: true })
  await assertConsoleReachable()
  const usedPlaywright = await captureWithPlaywright('/login')
  if (!usedPlaywright) {
    await captureWithChrome('/login')
  }
  const engine = usedPlaywright ? 'playwright' : 'chrome-headless'
  const report = [
    '# M1 多视口截图记录',
    '',
    `- 时间：${new Date().toISOString()}`,
    `- 控制台：${baseUrl}/login`,
    `- 引擎：${engine}`,
    '- 已保存：登录页 1440 / 1280 / 1024 / 768',
    '- 未覆盖：应用概览、知识中心、能力调试。这些页面需要已登录的真实管理员会话和可用 API，本次没有伪造登录态。',
    '',
    '待真实环境补截图的路径：',
    ...AUTHENTICATED_PAGES.map((item) => `- ${item.name}: ${item.path}`)
  ].join('\n')
  await writeFile(path.join(outputDir, 'report.md'), `${report}\n`, 'utf8')
  console.log(report)
}

main().catch((error) => {
  console.error(`M1 视口验收未完成：${error.message}`)
  process.exitCode = 1
})
