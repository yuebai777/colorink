<template>
  <div class="ck-roadmap-wrap">
    <div class="ck-roadmap-header">
      <div class="ck-feedback-badge">📊 开发进度 & 需求看板</div>
      <h3 class="ck-roadmap-title">我们正在为 Colorink 打造什么？</h3>
      <p class="ck-roadmap-desc">
        每一位画师的建议都会在这里得到认真审视。下面是当前正在全力攻坚的功能、规划中的需求以及近期已落地的更新。
      </p>
    </div>

    <!-- 状态流程示意条 -->
    <div class="ck-flow-bar">
      <div class="ck-flow-step done">
        <span class="step-num">1</span>
        <span class="step-text">📝 提交建议</span>
      </div>
      <div class="ck-flow-arrow">→</div>
      <div class="ck-flow-step active">
        <span class="step-num">2</span>
        <span class="step-text">🔍 需求评审</span>
      </div>
      <div class="ck-flow-arrow">→</div>
      <div class="ck-flow-step active">
        <span class="step-num">3</span>
        <span class="step-text">🛠️ 研发中</span>
      </div>
      <div class="ck-flow-arrow">→</div>
      <div class="ck-flow-step finish">
        <span class="step-num">4</span>
        <span class="step-text">🎉 随新版发布</span>
      </div>
    </div>

    <!-- 看板主体三栏 -->
    <div class="ck-board-grid">
      <!-- 正在开发中 -->
      <div class="ck-board-col">
        <div class="ck-board-col-head in-progress">
          <span class="dot"></span>
          <h4>🛠️ 正在开发中 (In Progress)</h4>
          <span class="count">{{ inProgressList.length }}</span>
        </div>
        <div class="ck-board-cards">
          <div
            v-for="(item, idx) in inProgressList"
            :key="idx"
            class="ck-task-card"
          >
            <div class="ck-task-top">
              <span :class="['ck-tag', item.tagType]">{{ item.tag }}</span>
              <span class="ck-task-ver">{{ item.targetVersion }}</span>
            </div>
            <h5 class="ck-task-name">{{ item.title }}</h5>
            <p class="ck-task-desc">{{ item.desc }}</p>
            <div class="ck-progress-bar-wrap">
              <div
                class="ck-progress-bar-inner"
                :style="{ width: item.progress + '%' }"
              ></div>
            </div>
            <div class="ck-task-foot">
              <span class="ck-progress-text">进度：{{ item.progress }}%</span>
              <span class="ck-task-from">源自画师反馈</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 评估与计划中 -->
      <div class="ck-board-col">
        <div class="ck-board-col-head planned">
          <span class="dot"></span>
          <h4>💡 采纳与规划中 (Planned)</h4>
          <span class="count">{{ plannedList.length }}</span>
        </div>
        <div class="ck-board-cards">
          <div
            v-for="(item, idx) in plannedList"
            :key="idx"
            class="ck-task-card"
          >
            <div class="ck-task-top">
              <span :class="['ck-tag', item.tagType]">{{ item.tag }}</span>
              <span class="ck-status-text">{{ item.statusText }}</span>
            </div>
            <h5 class="ck-task-name">{{ item.title }}</h5>
            <p class="ck-task-desc">{{ item.desc }}</p>
            <div class="ck-task-foot">
              <span class="ck-task-votes">🔥 {{ item.heat }} 画师关注</span>
              <span class="ck-task-from">排期评估中</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 近期已发布 -->
      <div class="ck-board-col">
        <div class="ck-board-col-head shipped">
          <span class="dot"></span>
          <h4>✅ 最近已上线 (Shipped)</h4>
          <span class="count">{{ shippedList.length }}</span>
        </div>
        <div class="ck-board-cards">
          <div
            v-for="(item, idx) in shippedList"
            :key="idx"
            class="ck-task-card is-shipped"
          >
            <div class="ck-task-top">
              <span class="ck-tag tag-done">已落地</span>
              <span class="ck-task-ver-shipped">{{ item.version }}</span>
            </div>
            <h5 class="ck-task-name">{{ item.title }}</h5>
            <p class="ck-task-desc">{{ item.desc }}</p>
            <div class="ck-task-foot">
              <span class="ck-date-text">{{ item.date }}</span>
              <span class="ck-check-icon">✓ 稳定运行中</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- GitHub 看板直达底栏 -->
    <div class="ck-issues-link-bar">
      <div class="ck-issues-info">
        <strong>想查看更多议题讨论或技术细节？</strong>
        <p>所有功能反馈与 Bug 处理过程均在 GitHub 完全公开透明。</p>
      </div>
      <a
        class="ck-btn ck-btn-ghost"
        href="https://github.com/yuebai777/colorink/issues"
        target="_blank"
        rel="noopener"
      >
        🔎 前往 GitHub Issues 议题列表 →
      </a>
    </div>
  </div>
</template>

<script setup>
const inProgressList = [
  {
    title: 'SAI2 透明色与双向调色联动增强',
    desc: '优化透明色选定状态下的色轮轨道渐变，画笔在下笔首帧时色彩瞬时刷新无延迟。',
    tag: '软件适配',
    tagType: 'tag-sync',
    targetVersion: 'v1.9.0',
    progress: 85
  },
  {
    title: '取色热键区域过滤与双屏多显示器边界优化',
    desc: '防误触规则与边缘像素拾取补偿，全面支持高刷新率与高分屏混插显示环境。',
    tag: '核心引擎',
    tagType: 'tag-core',
    targetVersion: 'v1.9.0',
    progress: 70
  },
  {
    title: '调色盘预设导出与通用格式兼容 (ACO / ASE)',
    desc: '支持将 Colorink 中收藏的和弦配色方案直接一键导出为 Photoshop / CSP 通用色板。',
    tag: '功能拓展',
    tagType: 'tag-feat',
    targetVersion: 'v1.9.1',
    progress: 45
  }
]

const plannedList = [
  {
    title: '调色盘暗色模式多款色彩主题皮肤',
    desc: '提供高对比纯黑、冷灰调、低饱和米白等多套视觉风格，契合不同作画环境。',
    tag: 'UI 定制',
    tagType: 'tag-ui',
    statusText: '已立项 · 设计中',
    heat: '高频诉求'
  },
  {
    title: '吸色管按住 Shift 锁定水平 / 垂直单轴移动',
    desc: '精确采样同一条基准线上的渐变色彩，方便画师做素描明度阶梯对比。',
    tag: '操作体验',
    tagType: 'tag-feat',
    statusText: '技术评估中',
    heat: '画师建议'
  },
  {
    title: 'Krita / openCanvas 绘画软件同步通道调研',
    desc: '为更多开源与小众专业绘画软件提供免插件或内存直连的调色同步。',
    tag: '软件生态',
    tagType: 'tag-sync',
    statusText: '需求采纳',
    heat: '社区需求'
  }
]

const shippedList = [
  {
    title: '专属下载页与独立免装单文件版 (Onefile)',
    desc: '无需安装 Python 或运行时，双击即启，支持直接下载与高速加速镜像。',
    version: 'v1.8.10',
    date: '2026-09'
  },
  {
    title: 'CLIP STUDIO PAINT (CSP) Companion 扫码直连',
    desc: '全面兼容国行优动漫及 CSP 1.x~5.x 全版本，配对一次永久自动重连。',
    version: 'v1.8.9',
    date: '2026-08'
  },
  {
    title: 'OKLCh 感知灰度与 BT.709 硬件明度滤镜',
    desc: '全屏一键切换黑白视界，数位板画师随时快速检验素描明暗五大调子。',
    version: 'v1.8.8',
    date: '2026-07'
  }
]
</script>
