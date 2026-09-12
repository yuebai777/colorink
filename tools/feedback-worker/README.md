# Colorink 意见反馈 Serverless 云函数部署指南

本方案基于 **Cloudflare Workers**（终身免费，每日 10 万次请求额度，无需绑定信用卡），为 Colorink 文档站提供免 GitHub 账号提交反馈并自动同步至 GitHub Issues 的代理服务。

---

## 核心原理与安全保障

1. **Token 绝不暴露**：前端网页只向 Cloudflare Worker 发送用户填写的表单，GitHub Token 保存在 Cloudflare 后端环境变量中，任何人都无法通过浏览器抓取。
2. **频控防刷**：每个客户端 IP 限制 1 分钟内只能提交 1 次，内容限制在 5~3000 字之间，防止恶意爬虫灌水。
3. **极简体验**：普通画师无需注册任何技术账号，在网页输入后点击即可同步至 GitHub Issues。

---

## 3 步极简部署（耗时约 2 分钟）

### 第一步：获取 GitHub Token（访问密钥）

1. 打开 GitHub 并登录：[https://github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)
2. 点击 **Generate new token**：
   - **Token name**：填写 `colorink-feedback`
   - **Expiration**：建议选择 `90 days` 或 `Custom`（可自定较长有效期）
   - **Repository access**：选择 **Only select repositories**，并在列表中勾选 **`yuebai777/colorink`**
   - **Permissions** -> 展开 **Repository permissions**：
     - 找到 **Issues**，将权限选为 **Read and write**
3. 点击页面最底部 **Generate token**，**复制生成的这一串以 `github_pat_` 开头的 Token**（仅显示一次，注意保存）。

---

### 第二步：创建 Cloudflare Worker

1. 登录 Cloudflare 控制台：[https://dash.cloudflare.com/](https://dash.cloudflare.com/)（如果没有账号直接用邮箱免费注册）。
2. 在左侧菜单点击 **Workers & Pages**（Workers 和 Pages）→ 点击 **Create**（创建应用）→ **Create Worker**。
3. Worker 名称任意（如 `colorink-feedback`），点击 **Deploy**（部署）。
4. 部署后点击 **Edit Code**（编辑代码）：
   - 将在线编辑器里的原有代码清空；
   - 将本目录下的 [`worker.js`](./worker.js) 中的所有代码完整复制并粘贴进去；
   - 点击右上角 **Save and deploy**（保存并部署）。

---

### 第三步：配置环境变量与密钥

1. 返回该 Worker 的管理页面，点击 **Settings**（设置）标签页 → 点击左侧 **Variables and Secrets**（变量与机密）。
2. 在 **Environment Variables** 区域点击 **Add**（添加）：
   - 变量 1：
     - Variable name（名称）：`GITHUB_TOKEN`
     - Value（值）：粘贴在第一步复制的 GitHub PAT 密钥
     - 建议勾选或点击 **Encrypt**（加密存储）
   - 变量 2：
     - Variable name（名称）：`GITHUB_REPO`
     - Value（值）：`yuebai777/colorink`
3. 点击 **Deploy** 或 **Save** 保存。

---

### 第四步：在文档站启用

1. 在 Cloudflare Worker 的概览页面，复制你的 Worker 公共访问地址（例如 `https://colorink-feedback.your-name.workers.dev`）。
2. 打开 `docs/download/index.md`（或全局配置中），将 Worker 地址作为参数传给 `<FeedbackCard />`：
   ```html
   <FeedbackCard worker-endpoint="https://colorink-feedback.your-name.workers.dev" />
   ```
   或者直接在 `docs/.vitepress/theme/components/FeedbackCard.vue` 默认值中填入该地址。
3. 部署文档站后，画师在网页提交的反馈将立即在 GitHub Issues 页面自动生成！
