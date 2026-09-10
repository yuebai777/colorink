---
layout: home

hero:
  name: "Colorink"
  text: "专为绘画与设计打造的桌面调色工具"
  tagline: "全局极速取色 · 高精度色空间调色 · 绘画软件无缝同步 · 硬件感知灰度滤镜"
  actions:
    - theme: brand
      text: 快速上手 →
      link: /guide/getting-started
    - theme: alt
      text: 下载最新版 (v1.8.9)
      link: https://github.com/yuebai777/colorink/releases/latest
    - theme: alt
      text: ⚡ 爱发电赞助
      link: https://afdian.com/a/touyimoyuebai

features:
  - icon: 🎯
    title: 全局极速取色 & 瞬时落笔
    details: 随时按快捷键呼出全屏放大镜取色，高分屏像素级精确采样；瞬时唤醒同步通道，下笔第一笔绝对不色偏。
    link: /guide/color-picking
    linkText: 了解取色技巧
  - icon: 🎨
    title: LAB 环形色盘 & 和谐配色
    details: 内置五大调和配色体系（互补、分割互补、类似色、三色、矩形），支持数位板笔锚点拖拽联动与 5px 防抖。
    link: /guide/palette-and-harmony
    linkText: 探索色彩引擎
  - icon: 🔄
    title: 绘画软件深度同步
    details: 支持 CSP（推荐 Companion 手机模式扫码直连）、Photoshop（免提权 CEP 扩展）、SAI2 内存同步与透明色支持。
    link: /sync/overview
    linkText: 查看同步矩阵
  - icon: 🪟
    title: 自由面板与半透明悬浮
    details: 面板支持抓手自由拖拽、页签叠放或独立浮出；背景透明度 0%~100% 自由调节，直接浮在画布上方作画。
    link: /guide/floating-and-layout
    linkText: 了解布局定制
  - icon: 👁️
    title: 硬件加速灰度滤镜
    details: 一键切换全屏或指定显示器黑白视图（OKLCh 感知灰度与 BT.709 Luma），方便画师随时检查素描黑白灰关系。
    link: /guide/grayscale-filter
    linkText: 了解灰度检查
  - icon: 🛡️
    title: 便携免装 & 稳定省心
    details: 单文件版双击即用，无需安装 Python 或环境，免管理员开机自启，内置单实例锁与原子配置保护。
    link: /guide/getting-started
    linkText: 3 分钟上手
---

<div class="ck-section">
  <h2 class="ck-section-title">所见即所得的工作台</h2>
  <p class="ck-section-sub">从取色、调色到同步落笔，每一步都为绘画工作流量身打造。</p>
  <div class="ck-shot-grid">
    <div class="ck-shot">
      <img src="/screenshot.png" alt="Colorink 主界面" />
      <div class="ck-shot-cap">主界面 · 色轮 / LAB 环形色盘工作区</div>
    </div>
    <!-- 占位：后续提供「全屏取色放大镜」截图后，将下方 div.ck-img-placeholder 替换为 <img src="/xxx.png" alt="..." /> -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">🔍</span>
        <span class="ck-ph-label">全屏取色放大镜</span>
        <span class="ck-ph-note">截图待补充</span>
      </div>
      <div class="ck-shot-cap">放大镜取色 · 像素级精确采样</div>
    </div>
    <!-- 占位：后续提供「灰度滤镜效果」截图后替换 -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">👁️</span>
        <span class="ck-ph-label">硬件灰度滤镜</span>
        <span class="ck-ph-note">截图待补充</span>
      </div>
      <div class="ck-shot-cap">灰度滤镜 · 一键检查素描黑白灰</div>
    </div>
    <!-- 占位：后续提供「半透明悬浮面板」截图后替换 -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">🪟</span>
        <span class="ck-ph-label">半透明悬浮面板</span>
        <span class="ck-ph-note">截图待补充</span>
      </div>
      <div class="ck-shot-cap">悬浮布局 · 直接浮在画布上方作画</div>
    </div>
  </div>
</div>

<div class="ck-section">
  <h2 class="ck-section-title">与你热爱的绘画软件无缝协作</h2>
  <p class="ck-section-sub">自动识别前台软件，毫秒级颜色推送，前景 / 背景 / 透明色全通道支持。</p>
  <div class="ck-sync-grid">
    <div class="ck-sync-card">
      <h3>CLIP STUDIO PAINT</h3>
      <span class="ck-sync-mode">Companion 扫码直连</span>
      <p>官方协议终身兼容 1.x ~ 5.x，前景 / 背景 / 透明色完整支持，配对一次永久自动重连。</p>
    </div>
    <div class="ck-sync-card">
      <h3>Adobe Photoshop</h3>
      <span class="ck-sync-mode">免提权 CEP 扩展</span>
      <p>CC 2015 ~ 2026+ 全版本通吃，用户目录一键安装，前景 / 背景色双向同步。</p>
    </div>
    <div class="ck-sync-card">
      <h3>PaintTool SAI 2</h3>
      <span class="ck-sync-mode">内存极速同步</span>
      <p>内存级画笔颜色注入，色轮与滑块轨道渐变实时跟随，智能界面全量刷新。</p>
    </div>
    <div class="ck-sync-card">
      <h3>优动漫 PAINT (UDM)</h3>
      <span class="ck-sync-mode">内存极速同步</span>
      <p>国行 CSP 4.0 Pro / EX 自动识别，开箱即用，无需安装任何额外插件。</p>
    </div>
  </div>
  <p style="text-align:center; margin-top: 1.5rem;">
    <a href="/colorink/sync/overview" style="font-weight:600;">查看完整同步配置指南 →</a>
  </p>
</div>

<div class="ck-section">
  <div class="ck-sponsor">
    <h2>☕ 支持作者，让 Colorink 走得更远</h2>
    <p>
      Colorink 是完全免费的开源软件。如果它为你的创作带来了帮助，
      欢迎通过爱发电赞助，或扫码请我喝杯咖啡 —— 你的支持是持续更新的最大动力。
    </p>
    <div class="ck-sponsor-actions">
      <a class="ck-btn ck-btn-afdian" href="https://afdian.com/a/touyimoyuebai" target="_blank" rel="noopener">
        ⚡ 前往爱发电赞助
      </a>
      <a class="ck-btn ck-btn-ghost" href="https://github.com/yuebai777/colorink" target="_blank" rel="noopener">
        ⭐ 在 GitHub 上 Star
      </a>
    </div>
    <img class="ck-sponsor-img" src="/buy-me-a-coffee.jpg" alt="请我喝杯咖啡 · 赞赏码" />
  </div>
</div>

<div class="ck-section" style="margin-bottom: 3rem;">
  <div class="ck-community">
    <h2>欢迎加入 Colorink 用户交流群</h2>
    <p>
      遇到问题、想要反馈新功能建议，或想与其他画师交流调色技巧？欢迎扫码加入官方 QQ 群：
    </p>
    <img src="/community-qrcode.jpg" alt="Colorink 交流群二维码" />
    <p style="margin: 0;"><strong>QQ 群号：1108560464</strong></p>
  </div>
</div>
