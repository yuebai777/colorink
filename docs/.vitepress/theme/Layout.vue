<script setup>
/**
 * 自定义 Layout：在默认主题之上挂一层全屏「动态极光雾」背景。
 *
 * 背景由四层构成（全部 pointer-events: none，且整体 z-index: -1）：
 *   1. field  —— 5 团高斯模糊的浅粉色雾，各自以不同周期缓慢漂移、缩放
 *   2. veil   —— 自下而上的柔光罩层，让页面底部更沉、顶部更透
 *   3. fog    —— SVG feTurbulence 分形噪声（真实云雾颗粒，静态零开销）
 *   4. cursor —— 跟随鼠标的柔光斑，带缓动阻尼（交互感）
 *
 * 鼠标视差：把指针位置写进 --ck-px / --ck-py，交给 CSS 做 translate3d，
 * 只在数值真正变化时跑 rAF，静止时自动停机，不做无谓的逐帧计算。
 */
import DefaultTheme from 'vitepress/theme'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useData } from 'vitepress'

const { Layout } = DefaultTheme
const { frontmatter } = useData()

// 首页给满强度，文档页收一点，保证长文阅读时底色足够安静
const isHome = computed(() => frontmatter.value.layout === 'home')

const root = ref(null)

const pointer = {
  tx: 0,
  ty: 0,
  x: 0,
  y: 0,
  rawX: 0,
  rawY: 0,
  cx: 0,
  cy: 0,
  awake: false
}

let raf = 0

function render() {
  const el = root.value
  if (!el) return
  el.style.setProperty('--ck-px', pointer.x.toFixed(4))
  el.style.setProperty('--ck-py', pointer.y.toFixed(4))
  el.style.setProperty('--ck-cx', `${pointer.cx.toFixed(1)}px`)
  el.style.setProperty('--ck-cy', `${pointer.cy.toFixed(1)}px`)
}

function tick() {
  raf = 0
  const p = pointer

  // 视差跟手快一点，光斑跟手慢一点，两层速度差带来纵深
  p.x += (p.tx - p.x) * 0.07
  p.y += (p.ty - p.y) * 0.07
  p.cx += (p.rawX - p.cx) * 0.1
  p.cy += (p.rawY - p.cy) * 0.1

  render()

  const settling =
    Math.abs(p.tx - p.x) > 0.0015 ||
    Math.abs(p.ty - p.y) > 0.0015 ||
    Math.abs(p.rawX - p.cx) > 0.5 ||
    Math.abs(p.rawY - p.cy) > 0.5

  if (settling) raf = requestAnimationFrame(tick)
}

function onPointerMove(event) {
  const p = pointer
  p.tx = (event.clientX / window.innerWidth - 0.5) * 2
  p.ty = (event.clientY / window.innerHeight - 0.5) * 2
  p.rawX = event.clientX
  p.rawY = event.clientY

  // 首次移动时把光斑直接放到指针处，避免从左上角飞进来
  if (!p.awake) {
    p.awake = true
    p.cx = event.clientX
    p.cy = event.clientY
    root.value?.classList.add('is-awake')
  }

  if (!raf) raf = requestAnimationFrame(tick)
}

let bound = false

onMounted(() => {
  // 触屏设备没有悬停概念，减弱动态效果时也不做视差
  const fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (!fine || calm) return

  window.addEventListener('pointermove', onPointerMove, { passive: true })
  bound = true
  render()
})

onBeforeUnmount(() => {
  if (bound) window.removeEventListener('pointermove', onPointerMove)
  if (raf) cancelAnimationFrame(raf)
  raf = 0
})
</script>

<template>
  <Layout>
    <template #layout-top>
      <div
        ref="root"
        class="ck-aurora"
        :class="{ 'ck-aurora--home': isHome }"
        aria-hidden="true"
      >
        <div class="ck-aurora__field">
          <span class="ck-orb ck-orb--rose"></span>
          <span class="ck-orb ck-orb--violet"></span>
          <span class="ck-orb ck-orb--azure"></span>
          <span class="ck-orb ck-orb--aqua"></span>
          <span class="ck-orb ck-orb--peach"></span>
        </div>
        <div class="ck-aurora__veil"></div>
        <div class="ck-aurora__fog"></div>
        <div class="ck-aurora__sweep"></div>
        <div class="ck-aurora__cursor"></div>
        <div class="ck-aurora__grain"></div>
      </div>
    </template>
  </Layout>
</template>
