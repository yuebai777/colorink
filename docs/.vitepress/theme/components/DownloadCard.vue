<template>
  <div class="ck-download-content">
    <!-- 核心下载主卡片 -->
    <div class="ck-dl-box">
      <!-- 主下载按钮（独立单文件版） -->
      <div class="ck-dl-primary-action">
        <a
          class="ck-dl-btn-huge"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          <span class="ck-btn-icon">🚀</span>
          <span>
            立即下载 Colorink 独立版
            <span class="ck-dl-btn-meta">单文件免安装 · 双击即可运行 (.exe)</span>
          </span>
        </a>
      </div>

      <!-- 辅助小字（引导下载 Onedir 版） -->
      <div class="ck-dl-subnote">
        💡 启动速度慢或遇到杀毒软件误报？推荐使用
        <a
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          下载解压目录版 (Colorink-Onedir.zip)
        </a>
        ，解压后即开即用。
      </div>

      <!-- 国内高速下载加速通道 -->
      <div class="ck-mirror-bar">
        <span class="ck-mirror-tag">⚡ 国内加速</span>
        <span>如遇 GitHub 直连下载缓慢，请点击高速镜像：</span>
        <a
          class="ck-mirror-link"
          href="https://ghfast.top/https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          独立版镜像
        </a>
        <span>·</span>
        <a
          class="ck-mirror-link"
          href="https://ghfast.top/https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          解压版镜像
        </a>
      </div>
    </div>

    <!-- 双版本差异对比 -->
    <div class="ck-dl-compare">
      <div class="ck-compare-card recommended">
        <span class="ck-compare-badge">推荐大多数画师</span>
        <h3>独立单文件版</h3>
        <span class="ck-compare-file">Colorink.exe</span>
        <p>所有运行所需依赖与资源全部封装在一个可执行文件中，极度便携干净。</p>
        <ul class="ck-compare-features">
          <li><span>✓</span> 单文件免安装，放桌面即可启动</li>
          <li><span>✓</span> 适合经常随身携带或拷贝使用</li>
          <li><span>✓</span> 自动建立单实例锁，避免重复开启</li>
        </ul>
        <a
          class="ck-compare-btn"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          下载单文件版 (.exe)
        </a>
      </div>

      <div class="ck-compare-card">
        <h3>解压目录版 (Onedir)</h3>
        <span class="ck-compare-file">Colorink-Onedir.zip</span>
        <p>运行时文件已预先解压在独立文件夹内，省去了单文件版的临时解压环节。</p>
        <ul class="ck-compare-features">
          <li><span>✓</span> 毫秒级极速响应，启动零等待</li>
          <li><span>✓</span> 彻底避免杀软对单文件临时释放的误报</li>
          <li><span>✓</span> 解压到常用软件盘，长驻使用体验最佳</li>
        </ul>
        <a
          class="ck-compare-btn"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          下载解压目录版 (.zip)
        </a>
      </div>
    </div>

    <!-- 初次启动安全提示 -->
    <div class="ck-dl-notice">
      <strong>🛡️ 初次启动提示：</strong><br />
      Colorink 为个人全免费开源软件，尚未采购微软企业代码签名证书。若初次运行提示「Windows 已保护你的电脑」或「未知发行者」：<br />
      请点击界面上的 <strong>「更多信息」</strong> → <strong>「仍要运行」</strong> 即可正常开启。代码完全开源透明，请放心使用。
    </div>
  </div>
</template>

<script setup>
// Pure Vue component avoids markdown indentation code-block quirks completely.
</script>
