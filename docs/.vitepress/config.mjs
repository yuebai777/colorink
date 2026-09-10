import { defineConfig } from 'vitepress'

const zhSidebarGuide = [
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
      { text: 'SAI2 / 优动漫 PAINT (UDM)', link: '/sync/sai2' }
    ]
  },
  {
    text: '帮助与支持',
    items: [
      { text: '常见问题解答', link: '/faq/' }
    ]
  }
]

const zhSidebarSync = [
  {
    text: '绘画软件同步指南',
    items: [
      { text: '同步概览与原理', link: '/sync/overview' },
      { text: 'CLIP STUDIO PAINT (CSP)', link: '/sync/csp' },
      { text: 'Adobe Photoshop (PS)', link: '/sync/photoshop' },
      { text: 'SAI2 / 优动漫 PAINT (UDM)', link: '/sync/sai2' }
    ]
  },
  {
    text: '返回基础指南',
    items: [
      { text: '快速上手', link: '/guide/getting-started' }
    ]
  }
]

const zhSidebarFaq = [
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

const enSidebarGuide = [
  {
    text: 'Getting Started',
    items: [
      { text: 'Quick Start', link: '/en/guide/getting-started' },
      { text: 'Global Color Picking', link: '/en/guide/color-picking' },
      { text: 'Palette & Color Harmony', link: '/en/guide/palette-and-harmony' },
      { text: 'Floating Panels & Layout', link: '/en/guide/floating-and-layout' },
      { text: 'Grayscale Filter', link: '/en/guide/grayscale-filter' }
    ]
  },
  {
    text: 'App Sync',
    items: [
      { text: 'Sync Overview', link: '/en/sync/overview' },
      { text: 'CLIP STUDIO PAINT (CSP)', link: '/en/sync/csp' },
      { text: 'Adobe Photoshop (PS)', link: '/en/sync/photoshop' },
      { text: 'SAI2 / UDM PAINT', link: '/en/sync/sai2' }
    ]
  },
  {
    text: 'Help & Support',
    items: [
      { text: 'FAQ', link: '/en/faq/' }
    ]
  }
]

const enSidebarSync = [
  {
    text: 'App Sync Guides',
    items: [
      { text: 'Sync Overview', link: '/en/sync/overview' },
      { text: 'CLIP STUDIO PAINT (CSP)', link: '/en/sync/csp' },
      { text: 'Adobe Photoshop (PS)', link: '/en/sync/photoshop' },
      { text: 'SAI2 / UDM PAINT', link: '/en/sync/sai2' }
    ]
  },
  {
    text: 'Back to Basics',
    items: [
      { text: 'Quick Start', link: '/en/guide/getting-started' }
    ]
  }
]

const enSidebarFaq = [
  {
    text: 'Help & Support',
    items: [
      { text: 'FAQ', link: '/en/faq/' }
    ]
  },
  {
    text: 'Back to Guides',
    items: [
      { text: 'Quick Start', link: '/en/guide/getting-started' }
    ]
  }
]

export default defineConfig({
  title: 'Colorink',
  description: '专为绘画与设计打造的 Windows 极速取色调色辅助工具',
  base: '/colorink/',
  srcExclude: ['superpowers/**'],
  head: [
    ['meta', { name: 'theme-color', content: '#6d5ae8' }]
  ],

  locales: {
    root: {
      label: '简体中文',
      lang: 'zh-CN',
      description: '专为绘画与设计打造的 Windows 极速取色调色辅助工具',
      themeConfig: {
        siteTitle: 'Colorink',
        nav: [
          { text: '首页', link: '/' },
          { text: '快速上手', link: '/guide/getting-started' },
          { text: '核心功能', link: '/guide/color-picking' },
          { text: '绘画软件同步', link: '/sync/overview' },
          { text: '常见问题', link: '/faq/' },
          { text: '⚡ 爱发电', link: 'https://afdian.com/a/touyimoyuebai' },
          {
            text: 'v1.8.9 下载',
            link: 'https://github.com/yuebai777/colorink/releases/latest'
          }
        ],
        sidebar: {
          '/guide/': zhSidebarGuide,
          '/sync/': zhSidebarSync,
          '/faq/': zhSidebarFaq
        },
        footer: {
          message: '基于 GPL-3.0 协议开源 · <a href="https://afdian.com/a/touyimoyuebai" target="_blank" rel="noopener">⚡ 爱发电赞助支持</a>',
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
        returnToTopLabel: '返回顶部',
        langMenuLabel: '多语言'
      }
    },
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      description: 'A blazing-fast desktop color picking & palette companion for painters and designers on Windows',
      themeConfig: {
        siteTitle: 'Colorink',
        nav: [
          { text: 'Home', link: '/en/' },
          { text: 'Quick Start', link: '/en/guide/getting-started' },
          { text: 'Features', link: '/en/guide/color-picking' },
          { text: 'App Sync', link: '/en/sync/overview' },
          { text: 'FAQ', link: '/en/faq/' },
          { text: '⚡ Sponsor', link: 'https://afdian.com/a/touyimoyuebai' },
          {
            text: 'Download v1.8.9',
            link: 'https://github.com/yuebai777/colorink/releases/latest'
          }
        ],
        sidebar: {
          '/en/guide/': enSidebarGuide,
          '/en/sync/': enSidebarSync,
          '/en/faq/': enSidebarFaq
        },
        footer: {
          message: 'Released under the GPL-3.0 License · <a href="https://afdian.com/a/touyimoyuebai" target="_blank" rel="noopener">⚡ Sponsor on Afdian</a>',
          copyright: 'Copyright © 2024-present yuebai777 & Colorink Contributors'
        },
        docFooter: {
          prev: 'Previous',
          next: 'Next'
        },
        outline: {
          level: [2, 3],
          label: 'On this page'
        },
        darkModeSwitchLabel: 'Appearance',
        returnToTopLabel: 'Back to top',
        langMenuLabel: 'Languages'
      }
    }
  },

  themeConfig: {
    search: {
      provider: 'local',
      options: {
        locales: {
          en: {
            translations: {
              button: {
                buttonText: 'Search docs',
                buttonAriaLabel: 'Search docs'
              },
              modal: {
                noResultsText: 'No results for',
                resetButtonTitle: 'Reset search',
                footer: {
                  selectText: 'to select',
                  navigateText: 'to navigate',
                  closeText: 'to close'
                }
              }
            }
          },
          root: {
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
        }
      }
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/yuebai777/colorink' }
    ]
  }
})
