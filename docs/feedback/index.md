---
layout: page
title: 意见反馈与开发进度
description: Colorink 画师意见反馈中心与功能开发进度看板。无需 GitHub 账号即可提交建议，随时查看功能排期与最新落地进度。
---

<div class="ck-feedback-page-wrap">
  <!-- 页面头部 -->
  <div class="ck-dl-hero">
    <div class="ck-dl-badge">🌱 听取每一位创作者的声音</div>
    <h1 class="ck-dl-title">意见反馈 & 进度看板</h1>
    <p class="ck-dl-desc">
      无论是遇到 bug 报错、期望支持的绘画软件，还是调色体验的奇思妙想，都可以在此直接提交。
    </p>
    <div class="ck-dl-tags">
      <span class="ck-dl-tag">⚡ 免 GitHub 账号</span>
      <span class="ck-dl-tag">🛡️ 安全私密提交</span>
      <span class="ck-dl-tag">📊 实时 GitHub 联动</span>
      <span class="ck-dl-tag">💬 官方社群交流</span>
    </div>
  </div>

  <!-- 反馈提交表单卡片 -->
  <FeedbackCard />

  <!-- 真实 GitHub Issues 进度看板 -->
  <RoadmapBoard />
</div>
