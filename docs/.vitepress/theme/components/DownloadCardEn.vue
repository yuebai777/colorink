<template>
  <div class="ck-download-content">
    <!-- Primary Download Card -->
    <div class="ck-dl-box">
      <!-- Main Download Action (Standalone) -->
      <div class="ck-dl-primary-action">
        <a
          class="ck-dl-btn-huge"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          <span class="ck-btn-icon">🚀</span>
          <span>
            Download Colorink Standalone
            <span class="ck-dl-btn-meta">Single portable file · Double-click to run (.exe)</span>
          </span>
        </a>
      </div>

      <!-- Secondary note for Onedir -->
      <div class="ck-dl-subnote">
        💡 Prefer faster startup or encountering antivirus false positives? Try
        <a
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          Download Directory Package (Colorink-Onedir.zip)
        </a>
        and extract anywhere.
      </div>

      <!-- Mirror Bar -->
      <div class="ck-mirror-bar">
        <span class="ck-mirror-tag">⚡ Fast Mirror</span>
        <span>If direct GitHub download is slow in your region:</span>
        <a
          class="ck-mirror-link"
          href="https://ghfast.top/https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          Standalone Mirror
        </a>
        <span>·</span>
        <a
          class="ck-mirror-link"
          href="https://ghfast.top/https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          Onedir Mirror
        </a>
      </div>
    </div>

    <!-- Version Comparison -->
    <div class="ck-dl-compare">
      <div class="ck-compare-card recommended">
        <span class="ck-compare-badge">Recommended for Most Artists</span>
        <h3>Standalone Onefile</h3>
        <span class="ck-compare-file">Colorink.exe</span>
        <p>All dependencies and runtime assets packaged into a single clean executable file.</p>
        <ul class="ck-compare-features">
          <li><span>✓</span> Truly portable, run straight from Desktop or USB</li>
          <li><span>✓</span> Clean workspace with no file clutter</li>
          <li><span>✓</span> Automatic single-instance locking</li>
        </ul>
        <a
          class="ck-compare-btn"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink.exe"
          download
        >
          Download Standalone (.exe)
        </a>
      </div>

      <div class="ck-compare-card">
        <h3>Directory Package (Onedir)</h3>
        <span class="ck-compare-file">Colorink-Onedir.zip</span>
        <p>Pre-extracted runtime folder, eliminating runtime unpack overhead on startup.</p>
        <ul class="ck-compare-features">
          <li><span>✓</span> Instantaneous startup without decompression delay</li>
          <li><span>✓</span> Prevents antivirus false positives on temp unpacks</li>
          <li><span>✓</span> Ideal for permanent installation in your tools directory</li>
        </ul>
        <a
          class="ck-compare-btn"
          href="https://github.com/yuebai777/colorink/releases/latest/download/Colorink-Onedir.zip"
          download
        >
          Download Directory (.zip)
        </a>
      </div>
    </div>

    <!-- Security Note -->
    <div class="ck-dl-notice">
      <strong>🛡️ First Launch Note:</strong><br />
      Colorink is a 100% free open-source project. If Windows SmartScreen displays "Windows protected your PC", click <strong>"More info"</strong> → <strong>"Run anyway"</strong> to launch. The complete source code is available on GitHub.
    </div>
  </div>
</template>

<script setup>
</script>
