<template>
  <div class="ck-roadmap-wrap">
    <div class="ck-roadmap-header">
      <div class="ck-feedback-badge">⚡ 实时 GitHub Issues 看板</div>
      <h3 class="ck-roadmap-title">问题反馈与处理进度</h3>
      <p class="ck-roadmap-desc">
        直接调取 GitHub 仓库的真实 Issues 列表。您在上方提交或由画师反馈的每一条建议，其当前状态与解决进度均在此实时公开。
      </p>
    </div>

    <!-- 真实进度统计卡片 -->
    <div class="ck-real-stats-card">
      <div class="ck-stat-item">
        <span class="stat-num">{{ stats.total }}</span>
        <span class="stat-label">总反馈议题</span>
      </div>
      <div class="ck-stat-divider"></div>
      <div class="ck-stat-item open">
        <span class="stat-num">{{ stats.open }}</span>
        <span class="stat-label">🟡 处理 / 跟进中</span>
      </div>
      <div class="ck-stat-divider"></div>
      <div class="ck-stat-item closed">
        <span class="stat-num">{{ stats.closed }}</span>
        <span class="stat-label">🟢 已解决 / 已落地</span>
      </div>
      <div class="ck-stat-divider"></div>
      <div class="ck-stat-item progress-col">
        <div class="stat-progress-top">
          <span class="stat-label">总体解决完成度</span>
          <span class="stat-rate">{{ stats.rate }}%</span>
        </div>
        <div class="ck-stat-bar-track">
          <div
            class="ck-stat-bar-fill"
            :style="{ width: stats.rate + '%' }"
          ></div>
        </div>
      </div>
    </div>

    <!-- 过滤器与刷新操作 -->
    <div class="ck-issues-toolbar">
      <div class="ck-filter-tabs">
        <button
          type="button"
          :class="['ck-filter-btn', { active: currentFilter === 'all' }]"
          @click="currentFilter = 'all'"
        >
          全部 ({{ issues.length }})
        </button>
        <button
          type="button"
          :class="['ck-filter-btn', { active: currentFilter === 'open' }]"
          @click="currentFilter = 'open'"
        >
          🟡 跟进中 ({{ stats.open }})
        </button>
        <button
          type="button"
          :class="['ck-filter-btn', { active: currentFilter === 'closed' }]"
          @click="currentFilter = 'closed'"
        >
          🟢 已完成 ({{ stats.closed }})
        </button>
      </div>

      <button
        type="button"
        class="ck-refresh-btn"
        :disabled="loading"
        @click="fetchIssues"
        title="刷新最新 GitHub Issues"
      >
        <span :class="['refresh-icon', { spinning: loading }]">🔄</span>
        <span>{{ loading ? '同步中...' : '刷新状态' }}</span>
      </button>
    </div>

    <!-- 加载中状态 -->
    <div v-if="loading && issues.length === 0" class="ck-issues-loading">
      <span class="loading-spin">🌀</span>
      <p>正在拉取 GitHub Issues 最新进度数据...</p>
    </div>

    <!-- 错误 / 速率限制提示 -->
    <div v-else-if="error" class="ck-issues-error">
      <p>⚠️ {{ error }}</p>
      <a
        href="https://github.com/yuebai777/colorink/issues"
        target="_blank"
        rel="noopener"
        class="ck-btn ck-btn-ghost"
      >
        直接在 GitHub 上查看完整议题列表 →
      </a>
    </div>

    <!-- 议题列表 -->
    <div v-else-if="filteredIssues.length > 0" class="ck-issues-list">
      <a
        v-for="item in filteredIssues"
        :key="item.id"
        :href="item.html_url"
        target="_blank"
        rel="noopener"
        class="ck-issue-row"
      >
        <!-- 状态指示 -->
        <div class="ck-issue-status">
          <span
            v-if="item.state === 'open'"
            class="badge-status open"
            title="状态：正在跟进 / 开发中"
          >
            🟡 跟进中
          </span>
          <span
            v-else
            class="badge-status closed"
            title="状态：已修复或已实现并合入"
          >
            🟢 已完成
          </span>
        </div>

        <!-- 议题主体 -->
        <div class="ck-issue-main">
          <div class="ck-issue-title-line">
            <span class="ck-issue-num">#{{ item.number }}</span>
            <span class="ck-issue-title">{{ item.title }}</span>
            <!-- 标签 -->
            <span
              v-for="label in item.labels"
              :key="label.id"
              class="ck-issue-label"
              :style="{ backgroundColor: '#' + label.color + '22', color: '#' + label.color }"
            >
              {{ label.name }}
            </span>
          </div>

          <div class="ck-issue-meta">
            <img
              v-if="item.user && item.user.avatar_url"
              :src="item.user.avatar_url"
              class="ck-user-avatar"
              alt="avatar"
            />
            <span class="ck-user-name">@{{ item.user ? item.user.login : '匿名' }}</span>
            <span class="meta-dot">·</span>
            <span>创建于 {{ formatDate(item.created_at) }}</span>
            <span v-if="item.state === 'closed' && item.closed_at" class="closed-date">
              <span class="meta-dot">·</span>
              已于 {{ formatDate(item.closed_at) }} 结案
            </span>
            <span v-if="item.comments > 0" class="ck-comments-count">
              💬 {{ item.comments }} 条回复
            </span>
          </div>
        </div>

        <!-- 外链箭头 -->
        <div class="ck-issue-arrow">
          <span>↗</span>
        </div>
      </a>
    </div>

    <!-- 列表为空 -->
    <div v-else class="ck-issues-empty">
      <p>暂无符合当前筛选条件的反馈议题。</p>
    </div>

    <!-- 底部操作条 -->
    <div class="ck-issues-link-bar">
      <div class="ck-issues-info">
        <strong>想参与技术讨论或补充复现步骤？</strong>
        <p>每一条反馈在 GitHub 均有独立编号与评论区，欢迎交流。</p>
      </div>
      <a
        class="ck-btn ck-btn-ghost"
        href="https://github.com/yuebai777/colorink/issues"
        target="_blank"
        rel="noopener"
      >
        进入 GitHub Issues 讨论区 →
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const issues = ref([])
const loading = ref(false)
const error = ref('')
const currentFilter = ref('all')

const stats = computed(() => {
  const total = issues.value.length
  const open = issues.value.filter((i) => i.state === 'open').length
  const closed = issues.value.filter((i) => i.state === 'closed').length
  const rate = total > 0 ? Math.round((closed / total) * 100) : 100
  return { total, open, closed, rate }
})

const filteredIssues = computed(() => {
  if (currentFilter.value === 'open') {
    return issues.value.filter((i) => i.state === 'open')
  }
  if (currentFilter.value === 'closed') {
    return issues.value.filter((i) => i.state === 'closed')
  }
  return issues.value
})

async function fetchIssues() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(
      'https://api.github.com/repos/yuebai777/colorink/issues?state=all&per_page=30'
    )
    if (!res.ok) {
      if (res.status === 403) {
        throw new Error('访问频率暂时受限（GitHub 匿名 API 限制），请稍候刷新或直接前往 GitHub 仓库查看。')
      }
      throw new Error(`无法获取 Issues 列表 (HTTP ${res.status})`)
    }
    const data = await res.json()
    if (Array.isArray(data)) {
      issues.value = data
    } else {
      issues.value = []
    }
  } catch (err) {
    error.value = err.message || '网络请求失败'
  } finally {
    loading.value = false
  }
}

function formatDate(isoStr) {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return isoStr
  }
}

onMounted(() => {
  fetchIssues()
})
</script>
