# Colorink

基于 PyQt6 的桌面端屏幕取色器 / 调色工具。

## 功能特性

- **色空间模块** — HSV / HLS / LCH 三模块，一键切换对应色轮与滑块组合
- **多色域滑块** — RGB、HSV、HSL、CIELAB、OKLab、OKLCh，全部可调节
- **前景 / 背景双色槽** — 一键交换、复制、对比
- **取色历史** — 持久化色板，行列和色块大小可调
- **全屏灰度滤镜** — 原生 D3D11 + DirectComposition 的 OKLCh 感知灰度，覆盖全部屏幕与 ColorInk 自身；另保留 Windows Mag 的低延迟 Luma 备用模式
- **全局热键** — 无需切换窗口即可取色或开关滤镜
- **CSP Companion 同步** — 支持 CLIP STUDIO PAINT 智能手机连接协议，无需内存扫描即可同步颜色
- **Photoshop 桥接** — 通过 JSX 脚本直接发送颜色到 PS 前景 / 背景色
- **DPI 自适应** — 正确处理多显示器 DPI 变化
- **无边框置顶窗口** — 极简悬浮，不用时贴边隐藏
- **高度可定制** — 滑块顺序 / 显隐、色空间模块、主题、UI 缩放

## 截图

![Colorink 截图](screenshots/screenshot.png)

## 环境要求

- Windows 10+（64 位）
- Python 3.10+

## 安装

```bash
git clone https://github.com/yuebai777/colorink.git
cd colorink
pip install -r requirements.txt
```

## 使用

```bash
python main.py
```

或双击 `run.bat`。

### 快捷键（可在设置中修改）

| 功能 | 默认 |
|------|------|
| 取前景色 | `F11` |
| 切换跟随鼠标 | `Ctrl + R` |
| 显示 / 隐藏窗口 | `Ctrl + H` |
| 切换灰度滤镜 | `Ctrl + G` |
| 切换 LAB 视图（鼠标悬停色轮 / LAB 区域） | `Space`（也可设为鼠标侧键 / 中键） |
| 切换 LAB 视图（全局） | `Ctrl + L` |

所有快捷键均可绑定鼠标按键（侧键 / 中键等）。全局快捷键绑定鼠标按键时，点击不会被拦截，画画软件仍会收到该点击。

## 打包（独立 EXE）

```bash
pip install pyinstaller
python build_pyqt.py
```

打包输出在 `dist/` 目录：

- `dist/Onedir/Colorink/`：目录版，便于排查运行依赖
- `dist/Onefile/Colorink.exe`：单文件版，适合作为 GitHub Release 下载资产

CSP Companion 的二维码自动扫描依赖 `pyzbar`、`Pillow`、`mss`；源码安装会随 `requirements.txt` 安装，发行包已内置。二维码不可用时仍可手动粘贴连接 URL。

## License

MIT — 详见 [LICENSE](LICENSE)。
