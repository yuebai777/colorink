/* picker_hook.c — WH_MOUSE_LL hook in a tiny DLL.
   Compile: cl /LD /O2 picker_hook.c /Fe:picker_hook.dll user32.lib */

#include <windows.h>

static HHOOK g_hook = NULL;
static volatile LONG g_left_clicked  = 0;
static volatile LONG g_right_clicked = 0;
static volatile LONG g_active        = 0;

LRESULT CALLBACK MouseProc(int nCode, WPARAM wParam, LPARAM lParam) {
    if (nCode >= 0 && g_active) {
        if (wParam == WM_LBUTTONDOWN) {
            InterlockedExchange(&g_left_clicked, 1);
            return 1; /* swallow */
        }
        if (wParam == WM_RBUTTONDOWN) {
            InterlockedExchange(&g_right_clicked, 1);
            return 1; /* swallow */
        }
    }
    return CallNextHookEx(NULL, nCode, wParam, lParam);
}

__declspec(dllexport) int install(void) {
    g_left_clicked = g_right_clicked = 0;
    g_active = 1;
    g_hook = SetWindowsHookExW(WH_MOUSE_LL, MouseProc,
                               GetModuleHandleW(NULL), 0);
    return g_hook != NULL;
}

__declspec(dllexport) void uninstall(void) {
    g_active = 0;
    if (g_hook) {
        UnhookWindowsHookEx(g_hook);
        g_hook = NULL;
    }
}

__declspec(dllexport) int left_clicked(void) {
    return InterlockedExchange(&g_left_clicked, 0);
}

__declspec(dllexport) int right_clicked(void) {
    return InterlockedExchange(&g_right_clicked, 0);
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved) {
    (void)h; (void)reserved;
    if (reason == DLL_PROCESS_DETACH) uninstall();
    return TRUE;
}
