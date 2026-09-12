import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import FeedbackCard from './components/FeedbackCard.vue'
import DownloadCard from './components/DownloadCard.vue'
import DownloadCardEn from './components/DownloadCardEn.vue'
import RoadmapBoard from './components/RoadmapBoard.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    app.component('FeedbackCard', FeedbackCard)
    app.component('DownloadCard', DownloadCard)
    app.component('DownloadCardEn', DownloadCardEn)
    app.component('RoadmapBoard', RoadmapBoard)
  }
}
