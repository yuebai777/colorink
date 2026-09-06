import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Colorink',
  description: '专为绘画与设计打造的 Windows 极速取色调色辅助工具',
  base: '/colorink/',
  lang: 'zh-CN',
  srcExclude: ['superpowers/**'],
  head: [
    ['meta', { name: 'theme-color', content: '#3eaf7c' }]
  ],
  themeConfig: {
    siteTitle: 'Colorink',
    nav: [
      { text: '首页', link: '/' },
      { text: '快速上手', link: '/guide/getting-started' },
      { text: '核心功能', link: '/guide/color-picking' },
      { text: '绘画软件同步', link: '/sync/overview' },
      { text: '常见问题 (FAQ)', link: '/faq/' },
      {
        text: 'v1.8.5 下载',
        link: 'https://github.com/yuebai777/colorink/releases/latest'
      }
    ],
    sidebar: {
      '/guide/': [
        {
          text: '基础使用',
          items: [
            { text: '快速上手', link: '/guide/getting-started' },
            { text: '全局取色与放大镜', link: '/guide/color-picking' },
            { text: '色盘与调和配色', link: '/guide/palette-and-harmony' },
            { text: '自由面板与悬浮布局', link: '/guide/floating-and-layout' },
            { text: '灰度滤镜与明度检查', link: '/guide/grayscale-filter' }
          ]
        },
        {
          text: '软件同步',
          items: [
            { text: '同步概览与原理', link: '/sync/overview' },
            { text: 'CLIP STUDIO PAINT (CSP)', link: '/sync/csp' },
            { text: 'Adobe Photoshop (PS)', link: '/sync/photoshop' },
            { text: 'PaintTool SAI2 / UDM', link: '/sync/sai2' }
          ]
        },
        {
          text: '帮助与支持',
          items: [
            { text: '常见问题解答', link: '/faq/' }
          ]
        }
      ],
      '/sync/': [
        {
          text: '绘画软件同步指南',
          items: [
            { text: '同步概览与原理', link: '/sync/overview' },
            { text: 'CLIP STUDIO PAINT (CSP)', link: '/sync/csp' },
            { text: 'Adobe Photoshop (PS)', link: '/sync/photoshop' },
            { text: 'PaintTool SAI2 / UDM', link: '/sync/sai2' }
          ]
        },
        {
          text: '返回基础指南',
          items: [
            { text: '快速上手', link: '/guide/getting-started' }
          ]
        }
      ],
      '/faq/': [
        {
          text: '帮助与支持',
          items: [
            { text: '常见问题解答 (FAQ)', link: '/faq/' }
          ]
        },
        {
          text: '回到指南',
          items: [
            { text: '快速上手', link: '/guide/getting-started' }
          ]
        }
      ]
    },
    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonText: '搜索文档',
            buttonAriaLabel: '搜索文档'
          },
          modal: {
            noResultsText: '无法找到相关结果',
            resetButtonTitle: '清除查询条件',
            footer: {
              selectText: '选择',
              navigateText: '切换',
              closeText: '关闭'
            }
          }
        }
      }
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/yuebai777/colorink' }
    ],
    footer: {
      message: '基于 GPL-3.0 协议开源',
      copyright: 'Copyright © 2024-present yuebai777 & Colorink Contributors'
    },
    docFooter: {
      prev: '上一篇',
      next: '下一篇'
    },
    outline: {
      level: [2, 3],
      label: '本页目录'
    },
    darkModeSwitchLabel: '外观主题',
    returnToTopLabel: '返回顶部'
  }
})
