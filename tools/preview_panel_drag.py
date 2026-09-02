"""End-to-end check for panel rearranging and tearing off (B-4 / B-5).

Everything else about the panel host is covered by pure tests; this is the
one thing they cannot prove — that a *real* MainWindow, with real slider
blocks, real geometry and the real config round-trip, survives a drop and
keeps a sane layout.

    python tools/preview_panel_drag.py

Runs offscreen against an isolated APPDATA, so it never touches the real
%APPDATA%\\Colorink config. Prints a PASS/FAIL line per check.
"""

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Inside the repo on purpose: the platform temp dir is not always writable,
# and this must never land in the real %APPDATA%\Colorink.
_SANDBOX = os.path.join(_ROOT, ".tmp_panel_drag")
shutil.rmtree(_SANDBOX, ignore_errors=True)
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, _ROOT)

# The console may be GBK (Chinese Windows) while the checks print UI glyphs
# like ▤; a UTF-8 stdout keeps the PASS/FAIL lines readable instead of
# dying with UnicodeEncodeError in the middle of the run.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Import every ui.* module before QApplication exists (see the session
# handoff: the offscreen platform is happier that way).
from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from ui.panels import registry, store  # noqa: E402
from ui.panels.drag import PANEL_MIME, PanelFrame, PanelTitleBar  # noqa: E402
import ui.main_window as main_window  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def quiesce(win):
    """Stop what a MainWindow keeps running, without exiting the process.

    close_application() does this and then calls os._exit(0) — which is the
    right thing for the app and useless here, since this script builds two
    windows and has to outlive the first.
    """
    from core import global_hotkeys

    try:
        global_hotkeys.unbind_all()
    except Exception:
        pass
    overlay = getattr(win, "grayscale_overlay", None)
    if overlay is not None:
        overlay.set_active(False)
    sync = getattr(win, "sync_thread", None)
    if sync is not None:
        sync.sync_enabled = False
        sync.running = False
    win.hide()


def build_window(drag=True):
    cfg = config.load_hotkey_config()
    cfg["panelDrag"] = drag
    config.save_hotkey_config(cfg)
    win = main_window.MainWindow()
    win.resize(360, 700)
    win.show()
    QApplication.processEvents()
    return win


def check_picker(win, when):
    """The picker must never leave the main window.

    The panel host assembles the slider column; the default dock tree also
    names the picker, so any host re-mount that falls back to it would pull
    the colour wheel out of the window entirely (it did — that is why this
    check exists).
    """
    in_layout = any(win.main_layout.itemAt(i).widget() is win.stack
                    for i in range(win.main_layout.count()))
    check(f"取色区还在主布局里（{when}）", in_layout)
    check(f"取色区尺寸正常（{when}）", win.stack.width() > 150,
          f"{win.stack.width()}x{win.stack.height()}")
    check(f"取色区没被抓手接管（{when}）",
          win.panel_host.frame_for(registry.PICKER) is None)


def centre_of(host, panel_id):
    box = host.frame_for(panel_id) or host.widget_for(panel_id)
    return box.mapTo(host, QPoint(box.width() // 2, box.height() // 2))


def top_band_of(host, panel_id):
    box = host.frame_for(panel_id) or host.widget_for(panel_id)
    return box.mapTo(host, QPoint(box.width() // 2, 2))


# Everything here outlives the function that made it. Two reasons, both of
# which end in an access violation that looks exactly like a product crash:
#   * a synthesised drag event borrows its QMimeData — Qt does not own it;
#   * a MainWindow destroyed by Python while its hotkey / sync threads are
#     still running takes the process down with it (the app itself never
#     destroys its window: close_application() calls os._exit).
_KEEPALIVE = []


def send_drop(host, panel_id, pos):
    """Drive the real Qt drag events, not just the helper methods."""
    bar = host.frame_for(panel_id).findChild(PanelTitleBar)
    mime = bar.mime_data()
    _KEEPALIVE.append(mime)
    for event_type in (QDragEnterEvent, QDragMoveEvent):
        event = event_type(pos, Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        _KEEPALIVE.append(event)
        QApplication.sendEvent(host, event)
    drop = QDropEvent(QPointF(pos), Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    _KEEPALIVE.append(drop)
    QApplication.sendEvent(host, drop)
    QApplication.processEvents()
    return drop.isAccepted()


def main():
    app = QApplication(sys.argv)
    _KEEPALIVE.append(app)
    win = build_window(drag=True)
    _KEEPALIVE.append(win)
    host = win.panel_host

    visible = [pid for pid in host.mounted_panels()
               if not host.widget_for(pid).isHidden()]
    check("每个挂载的面板都有抓手",
          all(isinstance(host.frame_for(pid), PanelFrame)
              for pid in host.mounted_panels()),
          f"{len(host.mounted_panels())} 个面板")
    check("隐藏的组不留下孤零零的标题条",
          all(host.frame_for(pid).isHidden() == host.widget_for(pid).isHidden()
              for pid in host.mounted_panels()),
          f"可见 {len(visible)} 组")

    check_picker(win, "开机")
    for probe_h in (win.height(), win.height() + 120, win.height() + 260):
        win.resize(win.width(), probe_h)
        QApplication.processEvents()
        if win.title_bar.y() != 0:
            break
    check("标题栏永远贴着窗口顶边", win.title_bar.y() == 0,
          f"窗高={win.height()} 标题栏 y={win.title_bar.y()}")
    grips_button = win.title_bar.btn_panels
    check("标题栏上有抓手开关，并且反映当前状态",
          grips_button.isCheckable() and grips_button.isChecked() is True,
          grips_button.text())
    grips_button.setChecked(False)
    win.title_bar._toggle_panel_grips()
    QApplication.processEvents()
    check("按一下就关掉抓手",
          win.cfg["panelDrag"] is False
          and win.panel_host.frame_for(host.mounted_panels()[0]) is None)
    grips_button.setChecked(True)
    win.title_bar._toggle_panel_grips()
    QApplication.processEvents()
    check("再按一下就回来", win.panel_host.frame_for(host.mounted_panels()[0]) is not None)

    before_order = list(host.tree().panels())
    before_height = win.height()
    source = visible[-1]
    target = visible[0]
    accepted = send_drop(host, source, top_band_of(host, target))
    after_order = list(host.tree().panels())
    check("真实拖放事件被接受", accepted)
    check("排布确实变了", after_order != before_order,
          f"{before_order[:2]}… → {after_order[:2]}…")
    # Hidden groups stay in the tree (the module filter only hides them), so
    # "first" means first *visible* — the drop lands right before its target.
    check("拖到谁头上就落在谁前面",
          after_order.index(source) == after_order.index(target) - 1,
          f"{after_order.index(source)} vs {after_order.index(target)}")
    check("面板没丢也没重复", sorted(after_order) == sorted(before_order))
    check("落点提示已经撤掉", host.drop_hint_rect() is None)
    zones = {host.drop_target_at(QPoint(x, y))[1]
             for x in range(4, host.width() - 4, 17)
             for y in range(4, min(host.height(), 400) - 4, 17)
             if host.drop_target_at(QPoint(x, y)) is not None}
    check("落点只有上下左右四种", zones <= {"top", "bottom", "left", "right"},
          str(sorted(zones)))
    check_picker(win, "拖完")

    saved = store.load_from(win.cfg, win.arrangement_seed())
    check("排布写进了配置", list(saved.panels()) == after_order)
    on_disk = config.load_hotkey_config()
    check("配置落到了磁盘",
          list(store.load_from(on_disk, "stack").panels()) == after_order)

    check("可见性没被重挂弄丢",
          {pid for pid in host.mounted_panels()
           if not host.widget_for(pid).isHidden()} == set(visible))
    QApplication.processEvents()
    check("窗口高度没有失控",
          abs(win.height() - before_height) < 200,
          f"{before_height} → {win.height()}")
    check("窗口内容没被清空", win.panel_host.mounted_panels() != ())

    # 面板全部浮出后，窗口的最小高度必须贴着取色区，不能给空列留位置
    all_out = [pid for pid in win.panel_host.mounted_panels()]
    for pid in all_out:
        win.float_panel(pid)
    QApplication.processEvents()
    check("面板全出去后滑块列彻底让位",
          win.sliders_container.isVisible() is False
          and win.panel_host.column_hint() == 0)
    check("面板全出去后窗口自己缩回来了",
          win.height() == win.minimumHeight(),
          f"窗高={win.height()} 最小高={win.minimumHeight()}")
    picker_bottom = win.stack.geometry().bottom()
    slack = win.height() - picker_bottom
    check("缩完之后贴着取色区", 0 < slack <= 12,
          f"取色区底={picker_bottom} 窗高={win.height()} 余={slack}")
    for pid in all_out:
        win.dock_panel(pid)
    QApplication.processEvents()
    tall_again = win.height()
    check("再收回来一切照旧",
          set(win.panel_host.mounted_panels()) == set(all_out)
          and win.sliders_container.isVisible() is True)
    check("收回来窗口也自己涨回去了", tall_again > win.minimumHeight() - 1
          and tall_again >= picker_bottom, f"窗高={tall_again}")

    # Restart with the saved arrangement: the drag must survive.
    quiesce(win)
    win2 = main_window.MainWindow()
    _KEEPALIVE.append(win2)
    win2.resize(360, 700)
    win2.show()
    QApplication.processEvents()
    check("重启后排布还在",
          list(win2.panel_host.tree().panels()) == after_order,
          f"{list(win2.panel_host.tree().panels())[:2]}…")

    # Reset, through the button a user would actually press: the sidebar
    # writes the config and emits settingChanged, the window re-assembles.
    win2.settings_sidebar.btn_reset_panel_layout.click()
    QApplication.processEvents()
    reset_order = list(win2.panel_host.tree().panels())
    check("复位回到开关决定的排布", reset_order == before_order,
          f"{reset_order[:2]}…")
    check("复位后配置里没有残留的排布",
          store.CONFIG_KEY not in config.load_hotkey_config()
          or list(store.load_from(config.load_hotkey_config(), "stack").panels())
          == before_order)
    check_picker(win2, "复位后")

    # Grips off: the classic window, no chrome.
    win2.cfg["panelDrag"] = False
    win2.refresh_slider_visibility_and_order()
    QApplication.processEvents()
    check("关掉抓手就没有标题条了",
          win2.panel_host.frame_for(registry.slider_panel_id("RGB")) is None)
    check("关掉抓手后面板还在",
          set(win2.panel_host.mounted_panels()) == set(after_order))
    check_picker(win2, "关掉抓手")

    # ── B-5: 拖出窗口成独立浮窗，再收回来 ──────────────────────────────
    win2.cfg["panelDrag"] = True
    win2.refresh_slider_visibility_and_order()
    QApplication.processEvents()
    host2 = win2.panel_host
    docked_order = list(host2.tree().panels())
    victim = [pid for pid in host2.mounted_panels()
              if not host2.widget_for(pid).isHidden()][-1]
    victim_widget = win2.panel_widget(victim)
    victim_size = (victim_widget.width(), victim_widget.height())

    # 这就是真实路径：抓手拖出去、没人接，宿主转发成"浮出"
    host2.frame_for(victim).title_bar._finish_reorder_drag(
        Qt.DropAction.IgnoreAction)
    QApplication.processEvents()
    check("抓手拖出窗口就是浮出", victim in win2.floating_windows(), victim)
    floater = win2.floating_windows().get(victim)
    check("浮窗端着的还是原来那块",
          floater is not None and floater.panel() is victim_widget)
    check("宿主里已经没有它了", victim not in host2.mounted_panels())
    check("浮窗置顶、无边框、不占任务栏",
          bool(floater.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
          and bool(floater.windowFlags() & Qt.WindowType.FramelessWindowHint)
          and bool(floater.windowFlags() & Qt.WindowType.Tool))
    strays = [type(x).__name__ for x in QApplication.topLevelWidgets()
              if not x.isHidden()
              and type(x).__name__ in ("QWidget", "PanelFrame")]
    check("浮出没有甩出空窗口", strays == [], str(strays))
    check("屏幕上只多出这一个浮窗",
          sum(1 for x in QApplication.topLevelWidgets()
              if not x.isHidden()
              and type(x).__name__ == "FloatingPanelWindow") == 1)
    check("停靠树仍然记着它的位置", victim in host2.tree().panels())
    # 仅在软件前台显示：浮窗要跟着主窗一起藏/现（趁窗口还在干净的 shown 态）
    win2.set_floating_foreground_visible(True)
    for _ in range(5):
        QApplication.processEvents()
    check("前台回来浮窗跟着现出来", not floater.isHidden())
    win2.hide()
    for _ in range(5):
        QApplication.processEvents()
    check("主窗隐藏后浮窗跟着藏起来", floater.isHidden())
    win2.show()
    for _ in range(5):
        QApplication.processEvents()
    check("主窗重新显示后浮窗跟着现出来", not floater.isHidden())
    check("浮出写进了磁盘",
          victim in store.load_floating_from(config.load_hotkey_config()))
    check_picker(win2, "浮出后")

    # 外观：比像素，不比样式表字符串（上一版就是因为只比字符串才漏掉）
    main_bar = win2.title_bar.grab().toImage()
    float_bar = floater.title_bar.grab().toImage()
    def band_color(image):
        """The bar's background = the colour most of the strip is made of.

        Sampling one point is fragile: the middle of the main title bar is
        where the window title sits, so a font change flips the answer.
        """
        counts = {}
        y = image.height() // 2
        for x in range(0, image.width(), 3):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
        return max(counts.items(), key=lambda item: item[1])[0]

    main_color = band_color(main_bar)
    float_color = band_color(float_bar)
    check("浮窗标题条和主窗标题条是同一个颜色",
          main_color == float_color, f"{main_color} vs {float_color}")
    body = floater.grab().toImage()
    edge = body.pixelColor(1, body.height() // 2).name()
    check("浮窗边框用的是主题边框色",
          edge == floater.title_bar._chrome.border_color, f"{edge}")
    bw = floater.title_bar._chrome.border_width
    inset = floater.title_bar._chrome.title_inset
    margins = floater.layout().contentsMargins()
    check("标题条和边框严丝合缝",
          (margins.left(), margins.right()) == (bw, bw)
          and margins.top() == (bw if inset else 0)
          and floater.title_bar.x() == bw
          and floater.title_bar.y() == (bw if inset else 0)
          and floater.title_bar.width() == floater.width() - 2 * bw,
          f"边宽={bw} inset={inset} 标题条=({floater.title_bar.x()},"
          f"{floater.title_bar.y()},{floater.title_bar.width()})")
    inner = floater.body.layout().contentsMargins()
    check("浮窗里的滑块不贴边",
          floater.panel().x() >= inner.left() > 0 and floater.panel().y() > 0,
          f"内边距=({inner.left()},{inner.top()},{inner.right()},{inner.bottom()})")
    check("撕下来之后面板还是原来那么大",
          (floater.panel().width(), floater.panel().height()) == victim_size,
          f"{victim_size} → {(floater.panel().width(), floater.panel().height())}")
    check("边框厚度跟着主题走",
          floater.title_bar._chrome.border_width > 0
          and floater.layout().contentsMargins().left()
          >= floater.title_bar._chrome.border_width,
          f"{floater.title_bar._chrome.border_width}px")

    # 右键菜单
    entries = dict(win2.panel_menu_for(victim))
    from ui.panels import menu as panel_menu
    check("浮窗右键能收回、能隐藏、能复位",
          panel_menu.DOCK in entries and panel_menu.HIDE in entries
          and panel_menu.RESET in entries, str(list(entries)))

    # 拖边调大小
    before_size = floater.geometry_record()
    floater.begin_resize("bottomright",
                         QPoint(before_size[0] + before_size[2],
                                before_size[1] + before_size[3]))
    floater.resize_to(QPoint(before_size[0] + before_size[2] + 60,
                             before_size[1] + before_size[3] + 40))
    floater.end_resize()
    QApplication.processEvents()
    resized = floater.geometry_record()
    check("浮窗可以拖边调大小",
          resized[2] == before_size[2] + 60 and resized[3] == before_size[3] + 40,
          f"{before_size[2]}x{before_size[3]} → {resized[2]}x{resized[3]}")
    check("面板跟着窗口一起变大",
          floater.panel().width() > 0 and floater.panel().height() > 0)

    # 取消置顶
    floater.set_always_on_top(False)
    QApplication.processEvents()
    check("取消置顶后不再压在别人上面",
          not (floater.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
    saved_state = store.load_floating_from(config.load_hotkey_config())[victim]
    check("大小和层级都写进了磁盘",
          saved_state.rect == resized and saved_state.on_top is False,
          f"{saved_state.rect} on_top={saved_state.on_top}")

    # 重启：浮窗自己回到外面
    quiesce(win2)
    win3 = main_window.MainWindow()
    _KEEPALIVE.append(win3)
    win3.resize(360, 700)
    win3.show()
    QApplication.processEvents()
    check("重启后它还在外面", victim in win3.floating_windows())
    check("重启后宿主里也确实没有它",
          victim not in win3.panel_host.mounted_panels())
    reopened = win3.floating_windows()[victim]
    check("重启后大小还是那个大小", reopened.geometry_record() == resized,
          f"{reopened.geometry_record()}")
    check("重启后层级设置也记得", reopened.always_on_top() is False)

    # 关闭按钮 = 收回原位
    win3.floating_windows()[victim].title_bar.close_requested.emit(victim)
    QApplication.processEvents()
    check("关闭按钮把它收回来了", victim not in win3.floating_windows())
    check("收回的是原位，不是队尾",
          list(win3.panel_host.tree().panels()) == docked_order
          and victim in win3.panel_host.mounted_panels(),
          f"{list(win3.panel_host.tree().panels())[-2:]}")
    check("收回后磁盘上不再有浮出记录",
          store.load_floating_from(config.load_hotkey_config()) == {})
    # 这一条是真正的回归闸门：之前"收回来"只是账面上的，控件其实没了爹、
    # 还是隐藏的，用户再也调不出来。断言看得见的东西，不是记录。
    back = win3.panel_widget(victim)
    check("收回来的面板真的挂回去了", back.parent() is not None,
          type(back.parent()).__name__ if back.parent() else "None")
    check("收回来的面板真的看得见", back.isHidden() is False)
    check_picker(win3, "收回后")

    # 双击 = 出去 / 回来（不用拖到窗口外这种没人猜得到的手势）
    win3.panel_host.frame_for(victim).title_bar.toggled.emit(victim)
    QApplication.processEvents()
    check("双击抓手把它送出去", victim in win3.floating_windows())
    win3.floating_windows()[victim].title_bar.toggled.emit(victim)
    QApplication.processEvents()
    check("双击浮窗标题把它收回来", victim not in win3.floating_windows())
    docked_entries = dict(win3.panel_menu_for(victim))
    check("停靠状态下右键给的是浮出",
          panel_menu.FLOAT in docked_entries
          and panel_menu.DOCK not in docked_entries)
    check("来回一趟之后面板还看得见",
          win3.panel_widget(victim).parent() is not None
          and win3.panel_widget(victim).isHidden() is False)

    # 右键"隐藏这一组" —— 放最后，因为它真的会把面板藏起来
    hide_key = panel_menu.visibility_key(victim)
    check("右键隐藏这一组真的把它藏起来",
          win3.run_panel_action(victim, panel_menu.HIDE) is True
          and win3.panel_widget(victim).isHidden() is True)
    check("藏起来用的就是设置里那个开关", win3.cfg.get(hide_key) is False,
          str(hide_key))
    win3.cfg[hide_key] = True
    win3.refresh_slider_visibility_and_order()
    QApplication.processEvents()
    check("再打开开关它就回来了",
          win3.panel_widget(victim).isHidden() is False)
    quiesce(win3)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    QApplication.processEvents()
    return 1 if failed else 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    # Leave through the front door: tearing two live MainWindows (hotkey
    # thread, sync thread, grayscale overlay) down through the interpreter's
    # static destructors segfaults on exit, which looks exactly like a
    # product crash and is not one.
    os._exit(status)
