# CSP 智能手机模式 HSV 黑色写入设计

## 目标

在 CSP 智能手机模式下，从 HSV 色图最底部选择 `V=0` 时，直接把用户选择的原始 `H/S/V` 写入 CSP，不再先发送一个 `V=5%` 的临时颜色。

## 当前行为

`CSPCompanionSync.set_color()` 收到显式 `hsv_u32` 且 `V` 接近 0 时，会发送两条 `SetCurrentColor` 消息：

1. 使用原始 `H/S` 和临时 `V=5%` 定位色相。
2. 使用原始 `H/S/V=0` 写入最终黑色。

现在 CSP 能直接读取 `V=0` 消息中的色相，因此第一条临时消息已无必要。

## 设计

仅修改显式 HSV 黑色分支：

- 删除 `V=5%` 临时写入及其响应读取。
- 只发送一次 `SetCurrentColor`，字段为调用方提供的原始 `HSVColorH`、`HSVColorS` 和 `HSVColorV=0`。
- 保留现有 `_last_sat_u32`、`_current_color`、响应读取、异常处理和返回值语义。
- 保留正常亮度写入以及无显式 HSV 时的色相/饱和度记忆逻辑。

不修改 HSV 色图坐标计算、UI 指示器位置、`hsv_u32` 生成或其他同步后端。

## 数据流

HSV 色图底部取色 -> `MainWindow.update_ui_colors()` 生成显式 `hsv_u32` -> `MemorySyncThread.write_color()` -> `CSPCompanionSync.set_color()` -> 一条带原始 `H/S/V=0` 的 `SetCurrentColor` 消息。

## 验证

- 使用伪造的发送/接收方法调用显式 HSV、`V=0` 路径，确认只发送一条消息。
- 解析该消息，确认 `HSVColorH` 和 `HSVColorS` 保持原值，`HSVColorV` 为 0。
- 确认方法返回成功并更新 `_last_sat_u32` 与 `_current_color`。
- 对修改文件运行 Python 语法检查和语言服务器诊断。

## 非目标

- 不删除 RGB 黑色写入时的色相/饱和度回退。
- 不调整 CSP 读取逻辑或去重阈值。
- 不重构其他 `set_color()` 分支。
