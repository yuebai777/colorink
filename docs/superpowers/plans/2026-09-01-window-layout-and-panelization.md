# 窗口尺寸联动重构 + 组件面板化方案

> 本文分两部分：**A 部分**是可以立刻动手的尺寸/布局重构；**B 部分**是为“每个组件可拆成独立窗口、也可自由组合进一个窗口”预先立好的架构方案。B 依赖 A 的成果，但 A 独立可交付。

**目标：** 让所有组件在窗口缩放时的行为可预测、可测试、互不打架；并为将来的面板化（停靠 / 浮出 / 自由组合）铺好地基。

**技术栈：** Python 3.10+、PyQt6、pytest、Windows；控件测试用 `QT_QPA_PLATFORM=offscreen`。

---

## 0. 现状盘点（均为实测，不是推测）

### 0.1 一次布局要跑什么

`resizeEvent` → `update_geometries()` 五步：

| 步骤 | 做的事 | 问题 |
|---|---|---|
| 0 | ~~`apply_theme(is_resize_event=True)`~~ → **A-2 后改为 `apply_layout()`** | ~~每次缩放都重建全部样式表~~ → 样式与几何已分家，缩放不再碰样式（DPI 变化时补一次） |
| 1 | 摆前景/背景色模块 | 需要 `wheel_size / title_offset / window_h / sliders_h` 四个二手参数 |
| 2 | 下发按钮尺寸 | 面板自己再算位置 |
| 3 | ringless 同步 | 第二套并行的布局系统 |
| 4 | LAB 避让 | **读另一个控件的实时几何**（`mapFromGlobal(preview_box)`） |
| 5 | 设置窗口跟随 | 独立窗口 |

### 0.2 几何知识散落在 14 个入口

`get_wheel_geometry` / `get_slice_geometry` / `plane_geometry` / `_disc_metrics` / `resize_and_position` / `_place_preview_box` / `_anchor_preview_to_circle` / `_sync_lab_lightness_bar` / `_reposition_legacy` / `_reposition_ringless` / `resolve_ringless_layout` / `_update_lab_avoid` / `_adjust_content_height` / `update_geometries`。

~~其中色环尺寸公式 `size = min(w - 16, max(16, h - 6))` 在四个文件里各写了一遍，且 `theme.py` 那份还不一致（`- 4` 而非 `- 6`）。~~ → **A-1 已收敛到 `ui/window_layout.py`**，并由"真控件必须与纯函数逐位一致"的测试守住。

### 0.3 已经付出代价的三类耦合

**(a) 控件读控件的实时几何 → 读到陈旧值**

- QStackedWidget 的非当前页不会被布局：实测色轮页在前时 `lab_square` 报 `100×100`，而它实际画着 302px 的圆盘 —— 拿它算避让会得到一个假圆，导致色块被无谓缩小。
- `isVisible()` 在窗口未真正显示时为 False：实测标题栏偏移塌成 0，前景色模块直接压到标题栏底下。

**(b) 影响布局的属性由面板尺寸推出来 → 自我放大的反馈环**

实测：明度条列用“上下边距 = 色块上下沿”对齐 → 边距计入该列的 `minimumSizeHint` → LAB 面板最小高 → `stack.minimumSizeHint()` → `_adjust_content_height` 撑高窗口 → 面板更高 → 边距更大……350 宽的窗口被顶到 807 高，色盘只有 322，下方空出 114px。

**(c) 悬浮件用窗口坐标摆放**

前景/背景模块、模式按钮、设置侧栏都挂在 MainWindow 上、按窗口坐标定位，但它们语义上属于取色区。所以窗口一长高，模块就跟着窗口底跑，和色环脱节。

### 0.4 高度策略与缩放路径脱节

取色区是方的，高度本该跟宽度走，但 `resizeEvent` 故意跳过 `_adjust_content_height`（怕和拖拽抢、怕 DPI 抖动）。结果窗口拖窄后最小高度还停在旧值 —— 拖不短、色轮下留白。已用 160ms 去抖补上，但根因是“高度策略”和“布局 pass”是两套东西。

---

## A 部分：尺寸联动重构（可立即执行）

### A0 全局约束

- 像素不变：重构完成后同一配置下的截图应与现在逐像素一致（本次要修的 bug 除外）。
- 不新增依赖；守住既有的纯 LOC 上限（`color_preview_box.py` 250 行已顶满）。
- 每波结束全量测试必须绿（当前基线 940 passed / 0 failed；124 个 error 是沙箱临时目录权限，与代码无关）。

### A1 把“样式”和“几何”劈开

`ThemeMixin.apply_theme()` 拆成：

- `apply_style(scale)` —— 样式表、颜色、字号、边框主题。只在主题/设置/DPI 变化时跑；
- `apply_layout(layout)` —— 只吃 A2 的布局数据，只做几何。缩放时只跑这一个。

收益：拖动窗口不再重建几十张样式表；样式回归与几何回归可以分开测。

### A2 一份纯数据的 WindowLayout

新文件 `ui/window_layout.py`：

```python
@dataclass(frozen=True, slots=True)
class WindowLayout:
    content: Rect            # 去掉窗口边框后的内容区
    title_band: Rect
    picker: Rect             # 取色区（stack）
    picker_circle: Circle    # 色环/圆盘外圆：唯一权威
    lab_plane: Rect          # 方形色块 / 圆盘外接框
    lab_bar_column: Rect     # 明度条整列
    lab_bar_band: Rect       # 明度条实际绘制的那一段
    preview_cluster: Rect    # 前景/背景 + 透明胶囊
    floating_buttons: tuple[Rect, ...]
    sliders: Rect
    history: Rect


def resolve_window_layout(*, size, ui_scale, title_height, sliders_hint,
                          cfg_flags) -> WindowLayout:
    ...
```

三条铁律（写进模块 docstring，并由测试保证）：

1. 只吃基本类型，不读任何 QWidget 的实时几何；
2. 输出只被“下发”（push），没有任何控件反过来查询别的控件；
3. 任何影响布局的属性（margins / min / max / sizePolicy）都不许由面板尺寸推出。

这套模式仓库里已经验证过 —— `RinglessLayout` 就是这么做的，只是覆盖面太小。

### A3 单向下发

`apply_layout(layout)` 依次把矩形交给：取色区 → 色环/LAB → 明度条 → 悬浮按钮 → 前景背景模块 → 滑块区 → 历史。每个组件新增 `apply_geometry(...)`，内部不再自己算、也不再问别人。`_update_lab_avoid`、`_place_preview_box`、`_sync_lab_lightness_bar`、`_reposition_*` 全部退化成读 layout 的对应字段。

### A4 不变量测试（新文件 `tests/test_window_layout.py`）

| 不变量 | 对应哪个坑 |
|---|---|
| 幂等：同一份 layout 连下发两次，控件几何不变 | 0.3(b) 反馈环 |
| 收敛：一次 pass 后重算 layout，结果相同 | 0.3(b) |
| 独立：面板高度 312 → 900，任何组件 `minimumSizeHint` 都不许变 | 0.3(b) |
| 包含：每个组件矩形都在父矩形内 | 把手被裁 / 胶囊压标题栏 |
| 方形：取色区宽高相等；窗口最小高度随宽度单调变化 | 0.4 拖不短 |
| 绑定：色环圆不变时，前景背景模块矩形不许变 | 0.3(c) 跟着窗口底跑 |
| 无重叠：色块/胶囊 vs 色环圆、明度条 vs 悬浮按钮 | 本次反复修的那些 |

全部是纯函数测试，**不需要真窗口** —— 正是这次被离屏环境反复坑（陈旧几何、`isVisible()` 说谎）的解法。

### A5 缩放期的两级节奏

- **即时级**（每个 resize 事件）：算 layout + 下发几何 + 低精度重绘；
- **落定级**（拖动停止 160ms，只排一次）：内容高度策略 + 全精度预热 + 缓存重建。

现在三处散落的 `schedule_slice_prewarm(350/500)`、`schedule_full_prewarm(0/80/100)` 收敛到这一个落定钩子。

### A 部分波次与进度

| 波 | 内容 | 状态 |
|---|---|---|
| A-0 | **缩放性能与抖动前哨**：样式表去重、滑块重算短路、历史面板不重建、贴合遮罩延迟、贴合系数闭式求解、取色区拖动中保持方形、历史余数摊进缝隙 | ✅ 已完成 |
| A-1 | 建 `ui/window_layout.py` + 纯函数测试；色环公式的四份拷贝收敛成一份 | ✅ 已完成 |
| A-2 | `apply_theme` 拆成 `apply_layout` / `apply_style`，缩放路径只跑前者 | ✅ 已完成 |
| A-3 | 几何入口改读 `WindowLayout`；`_picker_circles` / `_update_lab_avoid` 不再读控件 | ✅ 已完成 |
| A-4 | 两条 pass 共用同一个 `WindowLayout` 对象，悬浮件尺寸/笼子都由它给出 | ✅ 已完成 |
| A-5 | 三个散落的延时（500ms 色环预热 / 80~100ms LAB 预热 / 160ms 高度）收敛成一个 `_schedule_settle` | ✅ 已完成 |

#### A-0 实测结果

| 指标 | 之前 | 之后 |
|---|---|---|
| 一次缩放 pass | 178 ms | **0.5 ms** |
| 每帧写样式表 | 53 张 | 0 张 |
| 级联 polish 事件 | 47 万 / 40 帧 | 7 千 |
| 色环 / 取色区 / 滑块区 拐点 | 0，但 4px 台阶 | **0，1px** |
| 色块直径拐点 | 19 次 / 7px | **3 次 / 1px** |
| 历史网格左边距 | 1→5 锯齿后弹回 | **恒定** |
| 与色环最小净距 | −0.8 ~ +0.3（擦边） | **+1.5 ~ +4.0** |


#### A-3 ~ A-5 实测结果

**消除的最后两处"控件读控件"**

| 位置 | 之前 | 现在 |
|---|---|---|
| `_picker_circles` | `color_wheel.get_wheel_geometry()` + `mapTo` | `window_layout().picker_circle` |
| `_update_lab_avoid` | `ls.mapFromGlobal(pb.mapToGlobal(...))` | 布局矩形做纯算术 |

`mapFromGlobal` 那条正是"色轮页在前时 LAB 页仍报构造时尺寸"的受害者。

**纯布局 vs 真控件**（5 组窗口尺寸）：取色区矩形与色环圆 **逐位相同（差 0.0px）**。

**不变量**（宽 270~420 逐格）：

- 幂等：同尺寸重复布局 4 次，取色区/色块簇/明度条列/滑块区几何 **完全不动**；
- 双路径一致：`update_geometries` 与 `apply_layout` 落在同一结果；
- 落定后与色环最小净距 **+2.4 ~ +6.8px**（六种页面/形状/角落组合全为正）；
- 色块尺寸在 80 个宽度采样里只有 1 个拐点。

**落定钩子**：`_schedule_settle(width_changed=)` 一个定时器承担"内容高度策略 + 当前页全精度预热"，拖动中自动顺延（不把窗口从光标下抽走），只拖高度时不触发高度策略（不回弹手动高度）。

**测试**：1007 passed / 0 failed（124 个 error 是沙箱临时目录权限，与代码无关）。

仍然存在、但判定为可接受的量化台阶：

- 历史色格**高度**每 8px 宽进位一次（格子是正方形，边长由宽度整除），单调、不回退；
- 窗口高度在拖动停止 160ms 后落定一次（取色区是方的，高度必须跟着宽度走）。


### A-6：拖动/缩放时保持全精度渲染（2026-09-01 追加）

原来"流畅"是靠**降精度**换的：拖动时 LAB 平面固定降到 120px、色环固定隔 3~5 像素采样，肉眼可见发糊。这一波把成本本身打下来，让常见尺寸不再需要降精度。

**渲染器优化**（`ui/lab_prewarm.py`）

| 手段 | 说明 |
|---|---|
| sRGB 曲线查表 | 每像素 3 次 `pow` → 8192 级 LUT 索引；转换函数改为输出线性光，曲线只在打包成字节时应用一次 |
| 去掉每像素三角函数 | `cos(atan2(y,x)) == x/r`、`sin(...) == y/r` —— 每像素省下一次 cos 和一次 sin |
| 极坐标网格缓存 | 半径/方向/角度分箱只跟**图像尺寸**有关，与亮度无关；拖明度条时整张网格可复用 |
| 色域边界 profile 缓存 | 2048 方向 × 16 步二分是与图像大小无关的**固定开销**，按 (模式, 亮度桶) 缓存 |
| float32 | 圆盘路径全程单精度（方形路径本来就是） |
| 方形路径合并 | `lab_visualizer` 里那份重复实现删掉，两种形状共用同一渲染器 |

**实测（单帧渲染，ms）**

| 场景 | 之前 | 之后 |
|---|---|---|
| 圆盘 300px | 22.6 | **4.2** |
| 圆盘 400px | 46.7 | 15.8 |
| 方形 300px | 10.4 | **5.5** |
| 方形 500px | 29.9 | 14.2 |

**自适应精度**（取代写死的降精度）

- LAB 平面：先按全精度渲染并计时，按 `ms/像素` 估算下一帧能画多少 —— 预算 7ms，不够才降，且不低于 200px；
- 色环切片：同理，`subsample` 由实测成本决定，默认 1（全精度），只有真超预算才退回 3/5；
- 结果：**300px 以内的取色区在拖动时全程全精度**；340px 的色环切片四种模式也都是全精度；更大尺寸按机器能力平滑退让（不再是一刀切的 120px）。

**色环切片同样优化**（`ui/color_conversions.py` + `ui/slice_prewarm.py`）：数组版转换拆出**线性光**版本（`lab_to_linear_array` / `oklab_to_linear_array`），sRGB 曲线统一走 `srgb_encode_u8` 查表 —— 这是第三份、也是最后一份还在每像素 `pow` 的实现。

| 切片模式 @ 460px | 之前 | 之后 | 拖动时 |
|---|---|---|---|
| hsv-square | 1.5ms | 1.5ms | 全精度 |
| hls-triangle | 3.6ms | 3.8ms | 全精度 |
| **rgb-slice** | **8.9ms** | **6.4ms** | **全精度**（原先退回步长 5） |
| **oklch-slice** | **17.8ms** | **5.6ms** | **全精度**（原先退回步长 3） |

四种模式在 460px 以内全部全精度拖动；560px 才轮到 rgb/oklch 退让。与优化前 50 组切片参考图比对：RGB 最大差 1/255，透明度完全一致。

**缩放期间**：渲染尺寸对齐到 16px，让极坐标网格在连续帧之间复用（实测命中率 92%，圆盘缩放 8.33 → 5.55 ms/帧）；缩放停止 0.25s 后回到精确尺寸。

**等价性**：与优化前的 60 组参考渲染逐像素比对，可见像素 RGB 最大差 **1/255**，透明度几乎完全一致；另有与标量实现 `lab_to_rgb` / `oklab_to_rgb` 的交叉校验测试。

**测试**：新增 `tests/test_render_quality.py` 17 项，全量 1056 passed / 0 failed。

## B 部分：组件面板化（拆窗 / 自由组合）

### B1 面板边界

| 面板 id | 内容 | 尺寸性质 |
|---|---|---|
| `picker` | 色轮 ⇄ LAB 切换栈 + 模式/形状/调和按钮 | **强方形**（aspect=1） |
| `lightness` | LAB 明度条 | 细长条，高度跟随 picker |
| `swatches` | 前景/背景 + 透明 | 小方块，默认吸附 picker 角落 |
| `sliders.rgb` … `sliders.oklch` | 六组滑块，每组已是独立容器 | 高度由内容定，宽度自由 |
| `history` | 历史色格 | 行列数可配 |
| `titlebar` | 窗口控件 | 属于宿主窗口，不参与面板化 |

`lightness` 与 `swatches` 默认作为 `picker` 的**卫星**渲染在其内部（即现状），但从第一天起就按独立面板建模 —— 这样“拆出去”时不必改数据流。

### B2 面板契约（新目录 `ui/panels/`）

```python
@dataclass(frozen=True, slots=True)
class PanelSpec:
    id: str
    title: str
    factory: Callable[[PanelContext], QWidget]
    min_size: tuple[int, int]
    aspect: float | None          # picker = 1.0，其余 None
    satellites: tuple[str, ...]   # 默认吸附在自己内部的面板
    default_slot: str
```

`PanelContext` 提供：颜色会话、配置读写、i18n、以及“请求重新布局”的回调。**面板拿不到 MainWindow。**

### B3 先解耦状态，再谈拆窗（真正的前置条件）

当前控件直接往上抓宿主：

- `LabSquare` 读 `window().slider_widgets`、`window().lab_slider`（判断是否正在拖动 → 决定渲染精度）；
- `ColorPreviewBox` 调 `self._parent.select_fg_slot()` / `set_active_transparent()`；
- 多处 `cast(Any, win)` 直接摸主窗口私有属性。

面板一旦浮出成独立窗口，`window()` 就不再是 MainWindow，这些路径全断。

方案：抽出 `ColorSession`（收纳现有 `color_state`、`_source_space/_source_values`、active slot、transparent 标记），对外只有信号 + 命令：

```python
session.color_changed        # (rgb, source_space, source_values)
session.slot_changed         # ("fg" | "bg")
session.interaction_started  # 取代“反过来查滑块是不是按下了”
session.interaction_finished
session.set_from(space, values, source=...)
session.select_slot(slot)
```

`interaction_started/finished` 顺带解决渲染精度那件事 —— 现在是被观察者反过来查观察者。

### B4 宿主与停靠树

```python
Node = Split(orientation, children, sizes) | Tabs(panel_ids, current) | Leaf(panel_id)
```

- `PanelHost(QWidget)`：把 `Node` 树渲染成嵌套 `QSplitter` / `QTabWidget`；
- `FloatingPanelWindow`：无边框工具窗，复用现有置顶 / `WS_EX_NOACTIVATE` / 拖动逻辑；
- 布局序列化进 `cfg["panelLayout"]`（配置已有 schemaVersion，加一条迁移即可）；
- 拖拽：拖面板标题条 → 四边 + 中心（页签）落点提示 → 重新挂载；拖到窗口外 → 浮出。

### B5 迁移波次（每波都可单独发布）

| 波 | 内容 | 用户可见变化 |
|---|---|---|
| B-1 | 面板注册表 + `PanelSpec` + 可序列化停靠树（`ui/panels/`） | ✅ 已完成（纯数据，未接管控件） |
| B-2 | 抽 `ColorSession`，切断控件对宿主的直接引用 | ✅ 已完成 |
| B-3 | `PanelHost`（停靠树 → 控件树）+ 布局持久化 + 主窗口 provider 接缝 | 🟡 宿主已就绪并接通真实控件；**接管装配**留待 B-4 |
| B-4 | 打开分割条 + 页签 + **拖拽重排**：同一窗口内自由组合 | 可拖分割、滑块组可叠成页签、拖着标题条换位置 |
| B-5 | 浮出 / 停靠回来 | ✅ **已完成**：抓手拖出窗口即浮出，关闭按钮 / 拖回主窗口即收回，重启还原 |
| B-6 | 预设布局（类 PS / 紧凑 / 只要色环）、一键复位 | 🟡 **一键复位已做**（设置里"复位面板布局"）；预设留待 picker 进树后再做 |


### B 部分进度（截至 2026-09-01）

**B-2 已切断的四处宿主引用**

| 控件 | 之前 | 现在 |
|---|---|---|
| `LabSquare` 渲染精度 | 扫 `window().slider_widgets` + 读 `lab_slider.dragging` | `session.interacting` |
| `ColorWheel.is_active_interaction` | 同上 | `session.interacting` |
| `ColorPreviewBox` 切槽位 | `self._parent.select_fg_slot()` | `session.select_slot()` |
| `ColorPreviewBox` 透明 | `self._parent.set_active_transparent()` | `session.request_transparent()` |

会话用**计数**而非布尔量（两个控件同时被拖时不会提前解除低精度），滚轮补发的 release 不会把计数打成负数；没有会话时全部回落到旧路径，裸控件与既有测试不受影响。

**B-1 落地的纯数据模型**

- `ui/panels/spec.py`：`PanelSpec`（id / 标题 / 最小尺寸 / **aspect** / 卫星面板 / 可否浮出），构造即校验；
- `ui/panels/registry.py`：10 个面板（取色区、明度条、前景背景、历史、六组滑块）。取色区声明 `aspect=1.0` —— B6 里那条"必须拍板"的约束现在是数据；明度条与前景背景声明为取色区的**卫星**；
- `ui/panels/tree.py`：`Leaf / Tabs / Split` 停靠树 + JSON 往返 + **健壮解析**（陌生 id 丢弃、单子节点塌陷、页签索引夹紧、尺寸数量不匹配则忽略、什么都不剩就回默认树）。

默认树 = 今天的单列布局，且 `missing_panels()` 为空（每个非卫星面板恰好摆一次）。

**测试**：B-2 13 项 + B-1 19 项，全量 1039 passed / 0 failed。

**下一步（B-3）**：`PanelHost` 用停靠树渲染出嵌套 splitter，先固定成单列（像素不变），把布局存进 `cfg["panelLayout"]`。


**B-3 已落地的部分**

- `ui/panels/host.py` —— `PanelHost` 把停靠树渲染成嵌套 `QSplitter` / `QTabWidget`；面板控件由 provider 提供，重挂布局时**重新挂载而非重建**（否则会丢掉用户正在编辑的状态）；能把当前分割比例和页签选中项**读回**成树；
- `ui/panels/store.py` —— 布局存进 `cfg["panelLayout"]`，带版本号；未来版本号、损坏数据、已下架的面板 id 一律回落默认布局而不是把窗口搞空；
- `ui/window/panels_mixin.py` —— `PanelProviderMixin`：10 个面板 id 全部解析到真实控件（取色区→stack、明度条→条列、前景背景→预览框、历史→色格、六组滑块→各自容器），`missing_panel_widgets()` 实测为空。

**B-3 第二步（本轮）：排布决策已交给停靠树**

- 停靠树新增 `resizable` 语义：滑块区是**内容定高的堆叠**（每块与内容等高、块间固定间距、不可拖动），不是可拖动分割 —— 只认识 splitter 的宿主复现不出今天的窗口。`PanelHost` 相应支持两种呈现；
- `refresh_slider_visibility_and_order` 的顺序改由 `panel_layout_tree()` 给出（树由既有的每组顺序键推导），可见性逻辑原样保留；
- 布局写进 `cfg["panelLayout"]`（带版本号），实测能落盘并原样读回；存档只在"放置的面板集合与本次推导一致"时才生效，避免在用户背后出现第二个真相来源。

**像素证据**：同一份代码内做同构 A/B（新旧排序逻辑二选一 + 关掉缩放量化与自适应精度以消除渲染时序），色轮页/LAB 页 × 左上/左下 × 两种宽度共 8 组截图，**逐像素差 0**。（中途一次 187 的差异经排查是渲染时序噪声，不是排布变化；另有一个我自己写的比较脚本因 numpy 视图指向已释放的 QImage 而给出假"一致"，已弃用。）

**已做**：`PanelHost` 已成为滑块列的装配者 —— 主窗口创建 `PanelHost` 并作为 `sliders_layout` 唯一子控件；`refresh_slider_visibility_and_order` 只负责可见性，顺序经 `_slider_column_tree_for` 交给树挂载；主题 pass 通过 `set_stack_spacing` 调间距（不重建）。**逐部件 A/B 全部 0 差**（色轮/滑块列/历史/标题栏/预览簇五个候选控件，宿主装配 vs 手工装配）。全窗截图比对不可靠的原因是捕获本身不确定（同代码两次差 254），已用"比较最小渲染单元"替代。**布局持久化**：`cfg["panelLayout"]` 落盘/读回一致（面板集合不符时回落推导，避免双真相）。

**B-4 拖拽重排（本轮完成，B-4 收尾）**

拖拽这件事被劈成"纯数据"和"控件"两层，和 A 部分同一套路：

| 层 | 文件 | 负责 |
|---|---|---|
| 纯数据 | `ui/panels/rearrange.py` | 落点几何（`zone_at` 四边 25% 边带 + 中心=页签、`drop_rect`）与树手术（`remove_panel` / `insert_panel` / `move_panel`），**不 import 任何 Qt** |
| 控件 | `ui/panels/drag.py` | `PanelTitleBar`（16px 抓手，QDrag 携带 `application/x-colorink-panel`）、`PanelFrame`（面板 + 抓手打包成一个布局项）、`DropIndicator` |
| 宿主 | `ui/panels/host.py` | `set_drag_enabled` / `drop_target_at` / `show_drop_hint` / `apply_drop` + 四个 drag 事件，投放后发 `rearranged(tree)` |

树手术的三条规矩（都有测试守着）：

1. **顺着父容器的轴投放 = 纯换位**，不把内容定高的滑块列偷偷变成可拖动分割；横着投放才新建 `Split`（用户确实横着拖了）；
2. 中心投放 = 页签：落在普通面板上生成 `Tabs`，落在已有页签上插一页并选中它；
3. 摘掉面板时**边收边塌**（单子节点的 Split 塌成子节点、只剩一页的 Tabs 塌成列），剩余比例按原样归一化；拖不动就原样返回（自己拖自己、未知落点、拖走最后一个）。

**存档的归属问题（这一轮真正的坑）**：树一旦可以被拖动，"排布"就有了两个来源——开关推导 vs 用户存档。谁赢？做法是给存档记一个 **seed**（`stack` / `split` / `tabs`，见 `ui/panels/store.py` + `arrangement_seed()`）：存档只能覆盖**它自己出身的那次推导**。于是拖拽结果能活过刷新和重启，而勾选"并排/页签"仍然立刻生效（换了 seed，旧存档自动作废回落推导）。没有 seed 的老存档不认领。

**顺带修掉的三个窟窿**（都是既有代码，拖拽把它们变得可达）：

1. `PanelHost.column_hint()` 以前把每个 stack 容器的高度**加起来** —— 并排两列时等于向窗口多要一列的高度，页签时把所有页加在一起。改成按树走：列相加、**行取最高**、页签取最高的一页 + 标签条。真实窗口 A/B：经典单列 **328 == 328**（逐像素不变）、抓手 **392 == 392**，只有本来就算错的两档变小（并排 276 → 204、页签 216 → 186）。
2. `set_drag_enabled()` 在**还没挂过树**的时候会去挂"默认树"，而默认树里有 picker —— 于是开着 `panelDrag` 启动时，取色区会被拽进滑块列、从主布局里消失（窗口开起来没有色轮）。现在没挂过就不挂，且 `tools/preview_panel_drag.py` 三个时点都断言取色区还在主布局里、尺寸 352×352。
3. `PanelHost._read()` 把页签读回来时丢掉 `pages`（一页叠两块会被拆成两页）。

以及另外两处：`refresh_slider_visibility_and_order` 以前存的是"推导出来的列"而不是**真正挂上去的树**（页签模式下会被存成单列）；`mount_changed` 信号声明了、接了、但从来没人 `emit`（现在在挂载的面板集合变化时发，仅换顺序不发）。

**开关**：`panelDrag`（默认关）。关着时窗口与今天逐像素相同 —— 抓手是新增 chrome，开着才占 16px/面板。

**一键复位（B-6 的前半）**：拖拽没有撤销，所以配套做了"复位面板布局"（设置 → 取色区那张卡）。它只 `store.clear` 掉存档，**不动那三个布局开关**——开关是用户的设置，不是刚拖出来的一团。走的是仓库既有的设置约定（改 cfg → 落盘 → `settingChanged` → 窗口重载重装），不新开第二条路径。

**验证**：`tools/preview_panel_drag.py` 在**真实 MainWindow**（隔离 APPDATA、离屏）上跑 **24 项**端到端检查，全过：真实 Qt 拖放事件被接受、拖到谁头上就落在谁前面、面板不丢不重、落点提示撤掉、排布写进配置并落盘、重启后还在、可见性没被重挂弄丢、窗口高度不失控（808 → 808）、取色区三个时点都还在主布局里（352×352）、关掉抓手回到经典布局。单元测试 `tests/test_panel_drag.py` **49 项**（落点几何 5 / 摘除 6 / 插入 6 / 搬家 5 / 抓手与宿主 17 / 高度 3 / Qt 事件 3 …）+ `tests/test_panel_reset.py` 4 项 + `tests/test_panel_arrangement.py` 复位 3 项，全量 **1169 passed / 0 failed**。

**更早的记录（B-4 起）**：让 `PanelHost` 真正接管主窗口的装配。现窗口有三处结构性依赖需要一并搬家 —— 悬浮件是 MainWindow 的子控件并按窗口坐标定位、滑块分组的顺序/可见性由配置逻辑维护、取色区的方形上限作用在 stack 上。这一步要做成"逐像素不变"才有意义，所以单独成波，不与本轮混做。

### B-5 浮出 / 停靠回来（已完成）

**关键决定：浮出状态不进停靠树。** 树记的是这块面板的**家**，浮出只是"暂时不在家"：

- `PanelHost.set_floating_panels(ids)` —— 这些 id 不挂载（连 provider 都不问），树原样不动；
- 收回时把 id 从集合里去掉、重挂，面板**自动回到它原来的槽位**，而不是排到队尾；
- 存档另起一份 `cfg["floatingPanels"] = {id: [x, y, w, h]}`（`store.load_floating_from` / `save_floating_into`，脏数据一律丢弃）。若浮出写进树，就得在树里表达"不在任何容器里"，收回时还得猜位置。

**触发点是现成的**：抓手拖动本来就是 `QDrag`，`drag.exec()` 返回 `IgnoreAction` 就意味着"松手时没有任何宿主接住"，也就是窗口外 —— `PanelTitleBar.float_requested` → 宿主转发 → `MainWindow.float_panel()`。

**`FloatingPanelWindow`**（`ui/panels/floating.py`）：无边框 + 置顶 + `Tool`（不占任务栏）+ 可选 `WS_EX_NOACTIVATE`。无焦点那套 Win32 代码从 `picker_actions` 抽成共用的 `apply_no_activate()`，主窗口改为委托 —— 两处窗口的"不抢焦点"从此是同一份实现，切换 `noFocusMode` 也会同步给已经浮出的窗口。同一条标题栏 `PanelTitleBar` 两种模式：停靠时拖动 = 起 `QDrag`，浮出时拖动 = 移动窗口本身，松手位置落在宿主矩形内就收回。

**一个顺序上的坑**：浮出必须**先让宿主卸载、再交给浮窗**。反过来做的话，宿主重挂时 `_detach_mounted` 会把控件从浮窗里再抢回去（它还在 `_mounted` 里）。代码里有注释钉住这一点。

**修掉的一个致命 bug（用户报的）**：浮出再收回，面板**彻底消失、调不出来**。根因是 `PanelFrame.set_panel()` 按"还是同一个控件"短路——浮出时控件被浮窗端走了，但框还记着它，收回时框既不重新挂载也不显示，控件成了没爹的隐藏顶层窗口。修法是把"端着一块面板"的行为抽成共用的 `PanelHolder`（`ui/panels/drag.py`），**认爹不认名**：`widget.parent() is not self` 就重新收养。两个端着面板的容器（停靠框 / 浮窗）从此是同一份实现，不会再各自跑偏。

> **教训**（已进交接坑清单）：之前的端到端"通过"是因为它断言的是**账面**（`mounted_panels()` 里有它、`floating_windows()` 里没它），而真正该断言的是**看得见的东西**（`widget.parent()` 不是 None、`isHidden()` 是 False）。现在两条都断言。

**后续三项交互补齐**（用户提的两项 + 一项主动加的）：

| 交互 | 做法 |
|---|---|
| **浮窗调大小** | 无边框窗口自己 hit-test 4px 边框：纯函数 `resize_edge_at()` 判八个方向（含四角），`begin_resize/resize_to/end_resize` 应用几何、限最小尺寸、松手才落盘。悬停时光标自动变成对应的双向箭头 |
| **层级开关** | 浮窗标题栏加图钉按钮：实心 = 置顶（默认），空心 = 允许被别的窗口盖住。状态跟几何一起存进 `floatingPanels`（记录升级成 `{"rect": [...], "onTop": bool}`，旧的裸数组仍然认） |
| **双击 = 出去/回来** | "拖到窗口外面"这个手势没人猜得到。双击抓手 → 浮出；双击浮窗标题 → 收回。两条标题栏共用同一个 `toggled` 信号 |
| **拖回来时看得见落点** | 拖着浮窗经过滑块列会显示落点高亮，松手就落在**你放的地方**（四边 + 中心，和窗口内拖拽同一套规则），而不是闷头回原位 |

**再修一个用户报的 bug：浮出会多冒出空窗口。** 实测浮出一次，顶层控件从 1 个变成 **4 个**（多出一个 `PanelFrame` 和一个 `QWidget`）。两个原因叠在一起：

1. 面板被浮窗端走后，**旧的停靠框还订阅着它的显隐** —— 控件一显示，那个已经没有父控件的框就把自己也显示出来，成了一个空窗口。修法是把"一块面板同一时刻只能有一个 holder"变成 `PanelHolder.set_panel` 的硬规则：新 holder 收养前先让旧 holder 撒手。
2. 宿主换树时，**卸下来的框和被替换掉的旧根**在 detach 与 `deleteLater` 之间都是顶层控件，任何东西把它们显示出来就是一个空窗口。现在一律 `hide()`（只对框和根，不对裸面板 —— 显式 hide 会活过 re-parent，那会让整列消失）。

单元测试看不见这个（离屏时什么都没 show，`isHidden()` 全是 True），所以这条闸门放在真实窗口的端到端里：**浮出后不许出现 `QWidget`/`PanelFrame` 顶层控件，且 `FloatingPanelWindow` 只能有一个**。

**面板的皮跟着主窗走**：主题 pass 把整套值打包成一个 `PanelChrome`（背景 / 边框色 / 边宽 / 圆角 / 文字色 / **标题条底色** / 标题文字色 / 分隔线 / UI 缩放 / 字号，背景不透明度已在颜色里），一次推给 `PanelHost`（所有抓手）和每个浮窗；值缓存在 `_floating_chrome`，之后浮出的窗口一出生就是对的。抓手/浮窗标题条从此和主窗标题条**同一个底色**、同一条分隔线、同一个缩放。

> 第一版只推了窗口背景和边框，**标题条还是自己按调色板猜的** —— 于是换主题时它是窗口里唯一没跟着变的东西（用户一眼看出来了）。当时的端到端"通过"是因为它断言的是**样式表字符串里有 background-color**。现在断言的是**像素**：抓主窗标题条和浮窗标题条各取中心点，颜色必须一模一样（三套 UI 主题实测 `#2d2d2d/#b2b2b2/#787878` 全部逐一相等），边框颜色也和主题边框色比像素。

**抓手右键菜单**：`ui/panels/menu.py` 是纯数据（`panel_menu_actions(panel_id, floating)` → (动作, 标签)），窗口侧 `run_panel_action()` 执行 —— 浮出/收回、隐藏这一组（就是设置里那个 `showSliders*` 开关）、复位面板布局。菜单只是把同一批动作放到手边，没有第二套实现。

**落点收敛成四向**：`zone_at()` 默认按四条边把整块面板分成四个三角区，取消了"中间 = 页签"的落点 —— 拖一块滑块上移两行的人，不会是想把它塞进另一块里面。页签能力保留为 `allow_center=True`（树、手术、测试都还在），只是默认不开。

**标题条与边框严丝合缝**：浮窗外层 margins 精确等于主题边框宽度，标题条从边框内缘铺到另一侧；面板本体另有一圈 4px 内缩，那圈就是左右/底部的调整手柄；标题条最外 4px 的按压 `event.ignore()` 交还给窗口，所以贴边也能拉伸。端到端断言 `标题条.x == 边宽` 且 `宽 == 窗宽 - 2×边宽`。

**设置整合**：新开一张「面板布局」卡（抓手 / 并排 / 页签 + **浮出面板清单** + 全部收回 + 复位），从「色环与 LAB」搬出来。清单 +「全部收回」是"把面板拖到屏幕外找不着了"的唯一兜底 —— 右键菜单救不了，因为得先找到那个窗口。同时**删掉滑块顺序的 ▲▼ 按钮**（顺序现在由拖面板决定，设置里的行只保留显示开关、行序跟随实际顺序）。

**又一个顺序坑**：还原浮窗时"取消置顶"这一下会发 `geometry_changed`，而保存槽已经接上了 —— 半成品几何直接把存档覆盖成最小尺寸，重启后窗口缩水。修法是**窗口彻底摆好之后**才接保存槽（代码里有注释，测试里钉死）。

**验证**：`tools/preview_panel_drag.py` 扩到 **66 项**端到端（真实窗口）：抓手拖出即浮出、浮窗端着的还是原来那块控件、置顶/无边框/不占任务栏、停靠树仍记着位置、落盘、**重启后自己回到外面**、关闭按钮收回**原位**（不是队尾）、收回后磁盘记录清空、取色区在浮出/收回后都还在主布局里（352×352）。另有拖边调大小、取消置顶、重启后大小/层级都还在、收回来的控件**真的挂回去且看得见**、双击一来一回。单元测试 `tests/test_panel_floating.py` **70 项** + `tests/test_panel_menu.py` 11 项；端到端 **66 项**；全量 **1249 passed / 0 failed**。

### B6 现在就要拍板的五个决定

> 第 2 条（内容驱动窗口高度要退场）在 B-4 拖拽落地后**更紧了一点**：拖出横向分割后，列高由最高的那一列决定，"增长不收缩"仍然成立，但 `fit` / `free` 两档策略还没做——今天靠"手动调过高度就不再自动跟随"顶着。

1. **取色区方形 vs 分割条**：picker 声明 `aspect=1.0`，`PanelHost` 需要一个“按比例吃高度”的布局项。否则用户拖出 300×800 的 picker，色环只能缩在顶部 —— 就是这次“大片空白”的翻版。
2. **内容驱动窗口高度要退场**：`_adjust_content_height` 与自由停靠天然冲突。建议改成宿主策略：`fit`（今天的行为，单列时启用）/ `free`（用户一旦手动分割或浮出任何面板即切换，并写进配置）。
3. **浮窗是否继承“无焦点取色”**：每个浮出窗口都要复刻 `WS_EX_NOACTIVATE` + 置顶 + 跟随绘画软件前台，否则取色时会抢焦点。建议做成 `FloatingPanelWindow` 的统一能力。
4. **快捷键/托盘的宿主**：全局热键、托盘、同步都挂在 MainWindow。即使一个面板都不剩，它也必须继续存在（可隐藏）作为会话宿主。
5. **面板 id 消失的兜底**：读到未知 id 直接丢弃并落回默认槽位，不能让一份旧布局把窗口变空。

---

## 建议的下一步

先做 **A-1 + A-2**（纯数据布局 + 样式/几何分离）。这两波不改任何视觉，却能一次性堵死“控件互相读几何”和“边距反过来撑窗口”两类坑 —— 本次会话的三轮返工全部出自它们。做完再评估 B-1。