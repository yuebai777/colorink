/**
 * Colorink 官方意见反馈中转 Cloudflare Worker
 *
 * 作用：接收文档站用户的免登录反馈表单，代理调用 GitHub REST API 创建 Issue。
 * 优势：
 *   1. 保护 GitHub Token：Token 仅存留在 Worker 环境变量中，前端绝不泄露。
 *   2. 防刷机制：基于客户端 IP 进行基础频控（1 分钟最多提交 1 次）。
 *   3. 零维护成本：部署在免费版 Cloudflare Workers，全球高速访问。
 *
 * 环境变量配置（在 Cloudflare Worker 的 Settings -> Variables 中配置）：
 *   - GITHUB_TOKEN: 带有 Issues: Write 权限的 GitHub Personal Access Token (PAT)
 *   - GITHUB_REPO: 目标仓库，例如 "yuebai777/colorink"
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*', // 生产环境也可锁定为 'https://yuebai777.github.io'
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Content-Type': 'application/json; charset=utf-8'
}

const TYPE_MAP = {
  bug: { label: '🐛 遇到问题 / 崩溃', tag: 'bug' },
  feature: { label: '💡 功能建议 / 需求', tag: 'enhancement' },
  sync: { label: '🎨 绘画软件适配', tag: 'sync-integration' },
  other: { label: '💬 用户留言', tag: 'feedback' }
}

export default {
  async fetch(request, env, ctx) {
    // 1. 处理 OPTIONS 跨域预检请求
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: CORS_HEADERS
      })
    }

    // 2. 仅允许 POST 请求
    if (request.method !== 'POST') {
      return new Response(
        JSON.stringify({ success: false, message: 'Only POST method is allowed' }),
        { status: 405, headers: CORS_HEADERS }
      )
    }

    try {
      // 3. 校验环境变量
      const githubToken = env.GITHUB_TOKEN
      const githubRepo = env.GITHUB_REPO || 'yuebai777/colorink'

      if (!githubToken) {
        return new Response(
          JSON.stringify({
            success: false,
            message: 'Worker 未配置 GITHUB_TOKEN，请维护者在 Cloudflare 后台设置环境变量。'
          }),
          { status: 500, headers: CORS_HEADERS }
        )
      }

      // 4. IP 简易限流（利用 Cache API，1 分钟内同一 IP 仅能提交一次）
      const clientIp = request.headers.get('cf-connecting-ip') || 'unknown'
      const cache = caches.default
      const cacheKey = new Request(`https://rate-limit.local/${clientIp}`)
      const cached = await cache.match(cacheKey)

      if (cached) {
        return new Response(
          JSON.stringify({
            success: false,
            message: '提交过于频繁，请稍候 1 分钟后再试。'
          }),
          { status: 429, headers: CORS_HEADERS }
        )
      }

      // 5. 解析并校验请求内容
      const body = await request.json().catch(() => ({}))
      const type = body.type || 'other'
      const content = (body.content || '').trim()
      const contact = (body.contact || '').trim() || '未提供'
      const pageUrl = body.url || '未获取'
      const userAgent = body.ua || request.headers.get('user-agent') || '未知客户端'

      if (!content || content.length < 5) {
        return new Response(
          JSON.stringify({ success: false, message: '反馈内容太短（至少 5 个字）' }),
          { status: 400, headers: CORS_HEADERS }
        )
      }

      if (content.length > 3000) {
        return new Response(
          JSON.stringify({ success: false, message: '反馈内容过长（最多 3000 字）' }),
          { status: 400, headers: CORS_HEADERS }
        )
      }

      // 6. 构造 Issue 标题与 Markdown 正文
      const typeMeta = TYPE_MAP[type] || TYPE_MAP.other
      const titleSummary = content.split('\n')[0].substring(0, 32).trim()
      const issueTitle = `[画师网页反馈] ${typeMeta.label.split(' ')[1] || '意见'}: ${titleSummary}`

      const issueBody = `
### 📌 反馈分类
${typeMeta.label}

### 📝 反馈详情
${content}

---

### 👤 提交者附加信息
- **联系方式**：${contact}
- **来源页面**：\`${pageUrl}\`
- **提交时间**：${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} (北京时间)
- **客户端环境**：\`${userAgent}\`

> 💡 *本 Issue 由 Colorink 文档站反馈组件自动同步投递。*
`.trim()

      const labels = ['feedback']
      if (typeMeta.tag) {
        labels.push(typeMeta.tag)
      }

      // 7. 调用 GitHub REST API 创建 Issue
      const ghRes = await fetch(`https://api.github.com/repos/${githubRepo}/issues`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${githubToken}`,
          Accept: 'application/vnd.github+json',
          'User-Agent': 'Colorink-Feedback-Worker',
          'X-GitHub-Api-Version': '2022-11-28'
        },
        body: JSON.stringify({
          title: issueTitle,
          body: issueBody,
          labels
        })
      })

      const ghData = await ghRes.json()

      if (!ghRes.ok) {
        console.error('GitHub API Error:', ghData)
        return new Response(
          JSON.stringify({
            success: false,
            message: ghData.message || 'GitHub API 响应异常'
          }),
          { status: ghRes.status, headers: CORS_HEADERS }
        )
      }

      // 8. 记录限流缓存（缓存 60 秒）
      const rateLimitResponse = new Response('ok', {
        headers: { 'Cache-Control': 'max-age=60' }
      })
      ctx.waitUntil(cache.put(cacheKey, rateLimitResponse))

      // 9. 成功返回
      return new Response(
        JSON.stringify({
          success: true,
          issue_url: ghData.html_url,
          issue_number: ghData.number
        }),
        { status: 200, headers: CORS_HEADERS }
      )
    } catch (err) {
      return new Response(
        JSON.stringify({ success: false, message: err.message || '内部处理错误' }),
        { status: 500, headers: CORS_HEADERS }
      )
    }
  }
}
