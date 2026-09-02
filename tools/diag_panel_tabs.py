"""End-to-end diagnostics for the slidersTabs stacking feature.

Drives a *real* MainWindow (offscreen) with tabbed stacking enabled and
checks the things users reported: hidden groups must not become tabs, the
middle of a tabbed panel must stack (add a page) instead of stuffing the
panel into the current page, tab choice survives re-mounts/restarts, and a
dragged tab never leaves the tree in a weird shape.

    python -u tools/diag_panel_tabs.py
"""

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SANDBOX = os.path.join(_ROOT, ".tmp_panel_tabs")
shutil.rmtree(_SANDBOX, ignore_errors=True)
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QPoint, QRect  # noqa: E402
from PyQt6.QtGui import QFontMetrics, QPaintEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

from core import config  # noqa: E402
from ui.panels import rearrange, registry  # noqa: E402
from ui.panels import tree as dock  # noqa: E402
import ui.main_window as main_window  # noqa: E402

_RESULTS = []
_KEEPALIVE = []


def check(name, ok, detail=""):
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def quiesce(win):
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
    timer = getattr(win, "foreground_timer", None)
    if timer is not None:
        timer.stop()


def make_window():
    cfg = config.load_hotkey_config()
    cfg["panelDrag"] = True
    cfg["slidersTabs"] = True
    config.save_hotkey_config(cfg)
    win = main_window.MainWindow()
    win.resize(360, 700)
    win.show()
    QApplication.processEvents()
    return win


def tabs_widget(win):
    found = win.panel_host.findChildren(QTabWidget)
    return found[0] if found else None


def visible_groups(win):
    """The groups that should be in tabs: module allowed + showSliders on."""
    from ui.window.module_defs import _MODULE_DEFS
    module = getattr(win, "_current_module", "hsv")
    allowed = set(_MODULE_DEFS.get(module, _MODULE_DEFS["hsv"])["sliders"])
    out = []
    for group in config.SLIDER_GROUPS:
        if group == "History":
            if win.cfg.get("showSlidersHistory", True):
                out.append("History")
        elif group in allowed and win.cfg.get(f"showSliders{group}", True):
            out.append(group)
    return out


def tab_titles(win):
    tabs = tabs_widget(win)
    if tabs is None:
        return []
    return [tabs.tabText(i) for i in range(tabs.count())]


def current_center(win, panel_id):
    """Host-local point at a mounted panel's centre (works with frames on)."""
    box = win.panel_host.widget_for(panel_id)
    if box is None:
        return None
    return box.mapTo(win.panel_host, QPoint(box.width() // 2, box.height() // 2))


def main():
    app = QApplication(sys.argv)
    win = make_window()

    visible = visible_groups(win)
    pages_expected = (len(visible) + 1) // 2
    titles = tab_titles(win)
    check("初始页签数量 = 可见组数/2 向上取整",
          len(titles) == pages_expected,
          f"titles={titles} visible={visible}")

    # 1) Hidden groups must disappear from tabs.
    win.cfg["showSlidersRGB"] = False
    win.cfg["showSlidersHistory"] = False
    win.refresh_slider_visibility_and_order()
    QApplication.processEvents()
    titles = tab_titles(win)
    visible2 = visible_groups(win)
    check("关掉 RGB/History 后页签不再有它们",
          "RGB" not in titles and "History" not in titles,
          f"titles={titles}")
    check("关掉后页签数量对",
          len(titles) == (len(visible2) + 1) // 2,
          f"titles={titles} visible2={visible2}")

    # 2) Turn them back on: tabs must rebuild without ghosts.
    win.cfg["showSlidersRGB"] = True
    win.cfg["showSlidersHistory"] = True
    win.refresh_slider_visibility_and_order()
    QApplication.processEvents()
    titles = tab_titles(win)
    visible_after = visible_groups(win)
    expected_after = (len(visible_after) + 1) // 2
    check("重新开启后回到完整页签",
          len(titles) == expected_after
          and any("RGB" in title for title in titles),
          f"titles={titles} expected={expected_after}")
    check("页签标题列出整页面板",
          any("RGB/HSV" in title for title in titles),
          f"titles={titles}")
    tabs0 = tabs_widget(win)
    if tabs0 is not None:
        bar0 = tabs0.tabBar()
        metrics0 = QFontMetrics(bar0.font())
        fits0 = all(metrics0.horizontalAdvance(bar0.tabText(i))
                    <= bar0.tabRect(i).width()
                    for i in range(bar0.count()))
        check("页签标题完整显示不截断", bool(fits0),
              f"fits={fits0} widths={[metrics0.horizontalAdvance(bar0.tabText(i)) for i in range(bar0.count())]} rects={[bar0.tabRect(i).width() for i in range(bar0.count())]}")

    # 3) Center drop on a tabbed panel adds a NEW page (stacking), not stuffing.
    host = win.panel_host
    tree_before = host.tree()
    count_before = len(tree_before.pages) if hasattr(tree_before, "pages") else -1
    src = registry.slider_panel_id("OKLab")
    tgt = registry.slider_panel_id("RGB")
    center = current_center(win, tgt)
    ok = center is not None and host.apply_drop(src, center)
    QApplication.processEvents()
    # Give the freshly rebuilt tab stack a real layout pass so geometry and
    # visibility are not read frozen mid-rebuild.
    for _ in range(3):
        host.layout().activate()
        for child in host.findChildren(QWidget):
            layout = child.layout()
            if layout is not None:
                layout.activate()
    QApplication.processEvents()
    tree_after = host.tree()
    check("中心落点被接受（叠放）", bool(ok), f"tree={tree_after}")
    check("叠放后仍是 Tabs 且页数 +1",
          isinstance(tree_after, dock.Tabs),
          f"count {count_before} -> {len(tree_after.pages) if hasattr(tree_after, 'pages') else 'n/a'}")
    if hasattr(tree_after, "pages"):
        count_after = len(tree_after.pages)
        check("页签数量确实增加一页", count_after == count_before + 1,
              f"{count_before} -> {count_after}")
        flat = tree_after.panels()
        check("拖进叠放后没有面板丢失/重复",
              len(flat) == len(set(flat)) and len(flat) == len(tree_before.panels()),
              f"{flat}")
    check("QTabWidget 页数同步增加",
          tabs_widget(win) is not None and tabs_widget(win).count() == len(
              tree_after.pages) if hasattr(tree_after, "pages") else False,
          f"tab count={tabs_widget(win).count() if tabs_widget(win) else None}")
    # 4) After a real layout pass, only the bookmarked current page may be
    #    visible. QStackedWidget hides the others behind a plain Hide event on
    #    their pages; if PanelHolder mirrored that back, every page would be
    #    visible and steal drops aimed at the current one.
    oklab = src
    tabs = tabs_widget(win)
    if tabs is not None:
        current = tabs.currentIndex()
        visible_pages = [tabs.widget(i).isVisibleTo(host)
                         for i in range(tabs.count())]
        check("叠放后只有当前页可见",
              tabs.widget(current) is host.frame_for(oklab)
              and tabs.widget(current).isVisibleTo(host)
              and all(not tabs.widget(i).isVisibleTo(host)
                      for i in range(tabs.count()) if i != current),
              f"current={current} visible={visible_pages}")
        # 首帧回归：Qt 在首次布局前会把所有页重新 setVisible(True)；宿主在
        # tab 的第一个 Paint 事件里重新隐藏非当前页，否则用户看到的就是
        # 一瞬间的叠层（切两次页签才恢复）。
        for i in range(tabs.count()):
            tabs.widget(i).setVisible(True)
        QApplication.sendEvent(tabs, QPaintEvent(QRect(0, 0, 1, 1)))
        check("首帧绘制前只有当前页可见",
              not tabs.widget(current).isHidden()
              and all(tabs.widget(i).isHidden()
                      for i in range(tabs.count()) if i != current),
              f"current={current}")

    # 5) Edge drop inside the current page must stay in that page, not leak
    #    into the hidden pages (and must not lose panels).
    oklab = registry.slider_panel_id("OKLab")
    oklch = registry.slider_panel_id("OKLCh")
    oklab_box = host.frame_for(oklab)
    top = oklab_box.mapTo(host, QPoint(oklab_box.width() // 2, 2))
    before_edge = host.tree()
    edge_target = host.drop_target_at(top)
    ok_edge = host.apply_drop(oklch, top)
    after_edge = host.tree()
    edge_flat = after_edge.panels()
    check("页内边落点被接受", bool(ok_edge),
          f"target={edge_target} tree={after_edge}")
    check("边落点不丢面板且页签仍在",
          isinstance(after_edge, dock.Tabs)
          and len(edge_flat) == len(set(edge_flat))
          and len(edge_flat) == len(before_edge.panels()),
          f"{edge_flat}")

    # 5) Current page survives a re-mount that does NOT change the arrangement.
    if hasattr(after_edge, "pages") and tabs_widget(win):
        tabs = tabs_widget(win)
        target_index = min(1, tabs.count() - 1)
        tabs.setCurrentIndex(target_index)
        QApplication.processEvents()
        # save then refresh; the saved tree should keep current.
        win.save_panel_layout(host.tree())
        win.refresh_slider_visibility_and_order()
        QApplication.processEvents()
        check("重挂后当前页保留",
              tabs_widget(win) is not None and tabs_widget(win).currentIndex() == target_index,
              f"wanted {target_index}, got {tabs_widget(win).currentIndex() if tabs_widget(win) else None}")

    # 6) Tab-header interaction: pages are draggable by their header, and a
    #    panel dropped onto a header merges into that page.
    tabs = tabs_widget(win)
    if tabs is not None:
        bar = tabs.tabBar()
        check("页签条可以拖动重排", bar.isMovable())
        header = bar.mapTo(host, QPoint(bar.width() // 2, bar.height() // 2))
        header_target = host.drop_target_at(header)
        check("页签头是合并落点",
              header_target is not None
              and header_target[1] == rearrange.MERGE_PAGE,
              f"target={header_target}")
        if header_target is not None:
            box = host.frame_for(header_target[0])
            check("合并落点是指向该页的面板",
                  box is not None and box.parent() is not None)

    # 7) Float a panel out of tabs and dock it back: pages must survive.
    rgb = registry.slider_panel_id("RGB")
    before_float = host.tree()
    ok_float = win.float_panel(rgb)
    QApplication.processEvents()
    after_float = host.tree()
    check("从页签里浮出一个面板", bool(ok_float) and rgb not in host.mounted_panels(),
          f"mounted={host.mounted_panels()}")
    check("浮出后树仍记着它（页签结构不坏）",
          rgb in after_float.panels(), f"tree={after_float}")
    win.dock_panel(rgb)
    QApplication.processEvents()
    after_dock = host.tree()
    check("收回后面板回页签且队伍完整",
          rgb in after_dock.panels()
          and len(after_dock.panels()) == len(before_float.panels()),
          f"tree={after_dock}")

    # 7) Restart: saved tabs come back with the same pages.
    config.save_hotkey_config(win.cfg)
    quiesce(win)
    win2 = main_window.MainWindow()
    _KEEPALIVE.append(win2)
    win2.resize(360, 700)
    win2.show()
    QApplication.processEvents()
    restart_tree = win2.panel_host.tree()
    check("重启后页签树还在",
          isinstance(restart_tree, dock.Tabs)
          and len(restart_tree.panels()) == len(after_dock.panels()),
          f"{restart_tree.panels()} vs {after_dock.panels()}")
    check("重启后页签数量还原",
          tabs_widget(win2) is not None
          and tabs_widget(win2).count() == len(restart_tree.pages),
          f"count={tabs_widget(win2).count() if tabs_widget(win2) else None}")

    quiesce(win2)
    failures = [name for name, ok, _ in _RESULTS if not ok]
    print(f"\n{len(_RESULTS)} 项检查, {'全部通过' if not failures else 'FAIL: ' + ', '.join(failures)}")
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    os._exit(0 if not failures else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"DIAG FATAL: {exc!r}", file=sys.stderr)
        os._exit(2)
