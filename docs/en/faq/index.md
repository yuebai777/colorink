# Frequently Asked Questions

## Hotkey Conflicts & Window Recovery

### Right-clicking the tray icon won't open the menu / can't get into Settings?

After binding the mouse right button as a global hotkey (e.g. "Hide window"), Colorink's always-on-top window may cover system menus when you click in the taskbar or tray area.

- **First choice**: Settings → Hotkeys → enable "Exclude taskbar & tray areas" (on by default).
  With it on, mouse-button hotkeys don't fire inside the taskbar / tray / system-menu areas, so native system menus pop up normally.
- **Fallback hotkey**: no matter how you remap things, `Ctrl+Alt+Shift+,` always opens Settings directly
  (hard-coded, cannot be unbound). Use it when every other entry point is lost.
- **Double-click the tray icon with the left button**: if the window is visible, this jumps straight into Settings.
- Still stuck: end Colorink in Task Manager, press `Win+R` and enter `%APPDATA%\Colorink`,
  then edit the hotkey bindings in `hotkey-config.json` (or rename the file to `.bak` to restore defaults).
