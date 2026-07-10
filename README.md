# Colorink

基于 PyQt6 的桌面端屏幕取色器 / 调色工具。

## 功能特性

- **双取色模式** — HSV 色轮 + CIELAB 色空间，实时预览
- **多色域滑块** — RGB、HSV、HSL、CIELAB、OKLab、OKLCh，全部可调节
- **前景 / 背景双色槽** — 一键交换、复制、对比
- **取色历史** — 持久化色板，行列和色块大小可调
- **全屏灰度滤镜** — 一键切换感知型（OKLCh）或 BT.709 亮度灰度滤镜，覆盖整个屏幕（支持 DirectComposition / GDI）
- **全局热键** — 无需切换窗口即可取色或开关滤镜
- **Photoshop 桥接** — 通过 JSX 脚本直接发送颜色到 PS 前景 / 背景色
- **DPI 自适应** — 正确处理多显示器 DPI 变化
- **无边框置顶窗口** — 极简悬浮，不用时贴边隐藏
- **高度可定制** — 滑块顺序 / 显隐、色空间模式、主题、UI 缩放

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

## 打包（独立 EXE）

```bash
pip install pyinstaller
python build_pyqt.py
```

打包输出在 `dist/` 目录。

## License

MIT — 详见 [LICENSE](LICENSE)。
