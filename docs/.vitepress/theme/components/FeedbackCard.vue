<template>
  <div class="ck-feedback-card">
    <div class="ck-feedback-header">
      <div class="ck-feedback-badge">💬 意见与反馈</div>
      <h3 class="ck-feedback-title">告诉我们你的想法或遇到的问题</h3>
      <p class="ck-feedback-desc">
        无需登录 GitHub 账号，直接在此留言。你的反馈将自动同步至维护者的开发看板，帮助 Colorink 变得更好。
      </p>
    </div>

    <!-- 提交成功状态 -->
    <div v-if="status === 'success'" class="ck-feedback-success">
      <div class="ck-feedback-success-icon">✨</div>
      <h4>提交成功，非常感谢你的反馈！</h4>
      <p v-if="issueUrl">
        已自动同步至 GitHub Issue：
        <a :href="issueUrl" target="_blank" rel="noopener">{{ issueUrl }}</a>
      </p>
      <p v-else>维护者已收到你的建议，会在后续版本中持续优化。</p>
      <button class="ck-fb-btn ck-fb-btn-outline" @click="resetForm">
        再提一条
      </button>
    </div>

    <!-- 表单主体 -->
    <form v-else @submit.prevent="handleSubmit" class="ck-feedback-form">
      <!-- 分类单选 -->
      <div class="ck-fb-field">
        <label class="ck-fb-label">反馈类型</label>
        <div class="ck-fb-types">
          <button
            type="button"
            v-for="item in typeOptions"
            :key="item.value"
            :class="['ck-fb-type-btn', { active: form.type === item.value }]"
            @click="form.type = item.value"
          >
            <span>{{ item.icon }}</span> {{ item.label }}
          </button>
        </div>
      </div>

      <!-- 内容描述 -->
      <div class="ck-fb-field">
        <label class="ck-fb-label">
          问题描述 / 需求建议 <span class="required">*</span>
        </label>
        <textarea
          v-model.trim="form.content"
          rows="4"
          class="ck-fb-textarea"
          :placeholder="placeholderText"
          required
        ></textarea>
      </div>

      <!-- 联系方式（选填） -->
      <div class="ck-fb-field">
        <label class="ck-fb-label">
          联系方式 <span class="optional">（选填，QQ / 邮箱 / 微信，方便问题回访）</span>
        </label>
        <input
          v-model.trim="form.contact"
          type="text"
          class="ck-fb-input"
          placeholder="例如：QQ 12345678 / user@example.com"
        />
      </div>

      <!-- 错误提示 / 兜底 -->
      <div v-if="errorMessage" class="ck-fb-error">
        <span>⚠️ {{ errorMessage }}</span>
        <button
          type="button"
          class="ck-fb-copy-btn"
          @click="copyContent"
        >
          {{ copied ? '已复制到剪贴板！' : '一键复制我的反馈内容' }}
        </button>
      </div>

      <!-- 提交按钮 -->
      <div class="ck-fb-actions">
        <button
          type="submit"
          class="ck-fb-btn ck-fb-btn-primary"
          :disabled="status === 'submitting' || !form.content"
        >
          <span v-if="status === 'submitting'">正在同步到 GitHub...</span>
          <span v-else>🚀 发送反馈</span>
        </button>
        <span class="ck-fb-hint">
          也可以直接加入官方交流 QQ 群：<strong>1108560464</strong>
        </span>
      </div>
    </form>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  // 部署好 Cloudflare Worker 后在此配置，如 'https://colorink-feedback.yourname.workers.dev'
  workerEndpoint: {
    type: String,
    default: ''
  }
})

const typeOptions = [
  { value: 'bug', icon: '🐛', label: '问题或报错' },
  { value: 'feature', icon: '💡', label: '功能建议' },
  { value: 'sync', icon: '🎨', label: '绘画软件适配' },
  { value: 'other', icon: '💬', label: '其他想法' }
]

const form = reactive({
  type: 'feature',
  content: '',
  contact: ''
})

const status = ref('idle') // 'idle' | 'submitting' | 'success' | 'error'
const errorMessage = ref('')
const issueUrl = ref('')
const copied = ref(false)

const placeholderText = computed(() => {
  switch (form.type) {
    case 'bug':
      return '请描述你在哪一步遇到了问题、出现的错误提示，以及你的系统版本与绘画软件版本（如 CSP 3.0 / PS 2024）...'
    case 'feature':
      return '你希望 Colorink 增加什么功能？或者当前哪些操作让你觉得不够顺手？期待你的奇思妙想...'
    case 'sync':
      return '你使用的是哪款绘画软件？同步时是否遇到了颜色不一致或延迟等问题？'
    default:
      return '写下你对 Colorink 的任何使用感受或意见吧...'
  }
})

async function handleSubmit() {
  if (!form.content || form.content.length < 5) {
    errorMessage.value = '反馈内容请至少填写 5 个字哦，以便作者更好理解。'
    return
  }

  status.value = 'submitting'
  errorMessage.value = ''

  // 如果未配置 workerEndpoint，则给出友好提示并允许复制
  const endpoint = props.workerEndpoint || (typeof window !== 'undefined' ? window.__COLORINK_FEEDBACK_ENDPOINT__ : '') || ''
  if (!endpoint) {
    status.value = 'error'
    errorMessage.value = '反馈云函数服务（Cloudflare Worker）等待配置接入。你可以点击下方按钮一键复制刚才填的内容，直接发到 QQ 群（1108560464）或告知作者！'
    return
  }

  try {
    const payload = {
      type: form.type,
      content: form.content,
      contact: form.contact || '未提供',
      url: typeof window !== 'undefined' ? window.location.href : '',
      ua: typeof navigator !== 'undefined' ? navigator.userAgent : ''
    }

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })

    const data = await res.json()
    if (res.ok && data.success) {
      status.value = 'success'
      issueUrl.value = data.issue_url || ''
    } else {
      throw new Error(data.message || '提交失败，请稍后重试')
    }
  } catch (err) {
    status.value = 'error'
    errorMessage.value = `提交未成功（${err.message}）。你可以先点击下方按钮一键复制已输入的内容。`
  }
}

function copyContent() {
  const text = `【Colorink 反馈】[${form.type}] ${form.content}\n联系方式: ${form.contact || '无'}`
  navigator.clipboard.writeText(text).then(() => {
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2500)
  })
}

function resetForm() {
  form.content = ''
  form.contact = ''
  status.value = 'idle'
  errorMessage.value = ''
  issueUrl.value = ''
}
</script>
