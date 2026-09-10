---
layout: home

hero:
  name: "Colorink"
  text: "A desktop color companion built for painting & design"
  tagline: "Lightning-fast global color picking · High-precision color spaces · Seamless painting app sync · Hardware-aware grayscale filter"
  actions:
    - theme: brand
      text: Quick Start →
      link: /en/guide/getting-started
    - theme: alt
      text: Download Latest (v1.8.8)
      link: https://github.com/yuebai777/colorink/releases/latest
    - theme: alt
      text: ⚡ Sponsor on Afdian
      link: https://afdian.com/a/touyimoyuebai

features:
  - icon: 🎯
    title: Global Picking & Instant Inking
    details: Summon a full-screen magnifier anywhere with a hotkey; pixel-perfect sampling on HiDPI displays. The sync channel wakes instantly — your very first stroke never shifts color.
    link: /en/guide/color-picking
    linkText: Picking techniques
  - icon: 🎨
    title: LAB Ring Palette & Harmony
    details: Five classic harmony systems (complementary, split-complementary, analogous, triadic, rectangle) with pen-anchor dragging and 5px anti-jitter for stylus users.
    link: /en/guide/palette-and-harmony
    linkText: Explore the color engine
  - icon: 🔄
    title: Deep Painting-App Sync
    details: Works with CSP (Companion mode via QR pairing), Photoshop (privilege-free CEP extension), SAI2 memory sync, and transparent-color eraser support.
    link: /en/sync/overview
    linkText: View the sync matrix
  - icon: 🪟
    title: Freeform Panels & Translucent Floating
    details: Drag panels by their grips, stack them as tabs, or tear them off as floating windows. Adjust background opacity from 0% to 100% and paint right over your canvas.
    link: /en/guide/floating-and-layout
    linkText: Layout customization
  - icon: 👁️
    title: Hardware-Accelerated Grayscale Filter
    details: Toggle any monitor into a black-and-white view (OKLCh perceptual grayscale or BT.709 Luma) to check value structure at any moment.
    link: /en/guide/grayscale-filter
    linkText: Value checking
  - icon: 🛡️
    title: Portable & Rock-Solid
    details: A single EXE that runs on double-click — no Python, no setup, no admin rights. Single-instance lock and atomic config protection built in.
    link: /en/guide/getting-started
    linkText: Up and running in 3 minutes
---

<div class="ck-section">
  <h2 class="ck-section-title">A Workbench You Can See</h2>
  <p class="ck-section-sub">From picking and tuning to syncing the final stroke — every step is shaped around a painter's workflow.</p>
  <div class="ck-shot-grid">
    <div class="ck-shot">
      <img src="/screenshot.png" alt="Colorink main window" />
      <div class="ck-shot-cap">Main window · Color wheel / LAB ring workspace</div>
    </div>
    <!-- Placeholder: replace this div.ck-img-placeholder with <img src="/xxx.png" alt="..." /> once the screenshot is available -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">🔍</span>
        <span class="ck-ph-label">Full-screen Magnifier</span>
        <span class="ck-ph-note">Screenshot coming soon</span>
      </div>
      <div class="ck-shot-cap">Magnifier picking · Pixel-perfect sampling</div>
    </div>
    <!-- Placeholder: grayscale filter screenshot -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">👁️</span>
        <span class="ck-ph-label">Hardware Grayscale Filter</span>
        <span class="ck-ph-note">Screenshot coming soon</span>
      </div>
      <div class="ck-shot-cap">Grayscale filter · One-key value check</div>
    </div>
    <!-- Placeholder: translucent floating panel screenshot -->
    <div class="ck-shot">
      <div class="ck-img-placeholder">
        <span class="ck-ph-icon">🪟</span>
        <span class="ck-ph-label">Translucent Floating Panels</span>
        <span class="ck-ph-note">Screenshot coming soon</span>
      </div>
      <div class="ck-shot-cap">Floating layout · Paint right over your canvas</div>
    </div>
  </div>
</div>

<div class="ck-section">
  <h2 class="ck-section-title">Works Seamlessly With the Apps You Love</h2>
  <p class="ck-section-sub">Auto-detects the foreground app, pushes colors in milliseconds, with full foreground / background / transparent channel support.</p>
  <div class="ck-sync-grid">
    <div class="ck-sync-card">
      <h3>CLIP STUDIO PAINT</h3>
      <span class="ck-sync-mode">Companion QR Pairing</span>
      <p>Official protocol, compatible with 1.x ~ 5.x forever. Full foreground / background / transparent color support; pair once, auto-reconnect for good.</p>
    </div>
    <div class="ck-sync-card">
      <h3>Adobe Photoshop</h3>
      <span class="ck-sync-mode">Privilege-free CEP Extension</span>
      <p>CC 2015 through 2026+, one-click install into the user directory, with two-way foreground / background sync.</p>
    </div>
    <div class="ck-sync-card">
      <h3>PaintTool SAI 2</h3>
      <span class="ck-sync-mode">Memory-Level Sync</span>
      <p>Direct brush-color injection. SAI's color wheel and slider-track gradients follow in real time, with smart full UI refresh.</p>
    </div>
    <div class="ck-sync-card">
      <h3>UDM PAINT (优动漫)</h3>
      <span class="ck-sync-mode">Memory-Level Sync</span>
      <p>Auto-detects UDM Paint 4.0 Pro / EX. Works out of the box — no extra plugins required.</p>
    </div>
  </div>
  <p style="text-align:center; margin-top: 1.5rem;">
    <a href="/colorink/en/sync/overview" style="font-weight:600;">Read the full sync setup guides →</a>
  </p>
</div>

<div class="ck-section">
  <div class="ck-sponsor">
    <h2>☕ Support the Author, Keep Colorink Growing</h2>
    <p>
      Colorink is completely free and open source. If it has helped your creative work,
      consider sponsoring on Afdian or buying the author a coffee — your support is the biggest motivation for continued updates.
    </p>
    <div class="ck-sponsor-actions">
      <a class="ck-btn ck-btn-afdian" href="https://afdian.com/a/touyimoyuebai" target="_blank" rel="noopener">
        ⚡ Sponsor on Afdian
      </a>
      <a class="ck-btn ck-btn-ghost" href="https://github.com/yuebai777/colorink" target="_blank" rel="noopener">
        ⭐ Star on GitHub
      </a>
    </div>
    <img class="ck-sponsor-img" src="/buy-me-a-coffee.jpg" alt="Buy me a coffee · donation QR code" />
  </div>
</div>

<div class="ck-section" style="margin-bottom: 3rem;">
  <div class="ck-community">
    <h2>Join the Colorink User Community</h2>
    <p>
      Ran into a problem, have a feature idea, or just want to swap color tips with other painters? Scan the QR code to join the official QQ group:
    </p>
    <img src="/community-qrcode.jpg" alt="Colorink community QQ group QR code" />
    <p style="margin: 0;"><strong>QQ Group: 1108560464</strong></p>
  </div>
</div>
