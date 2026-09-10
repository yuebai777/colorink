/* picker_hook.c - WH_MOUSE_LL hook in a tiny DLL.
   Compile: cl /LD /O2 picker_hook.c /Fe:picker_hook.dll user32.lib */

#include <windows.h>

static HHOOK g_hook = NULL;
static volatile LONG g_left_clicked  = 0;
static volatile LONG g_right_clicked = 0;
static volatile LONG g_wheel_delta   = 0;
static volatile LONG g_active        = 0;

/* Buttons whose DOWN we already swallowed but whose matching UP has not been
   swallowed yet.  Swallowing the pair (not just the DOWN) keeps the
   foreground app from receiving an *orphan* button-up: drawing apps such as
   Photoshop run a mouse/stylus state machine, and an unmatched UP desyncs it
   so the next stylus stroke starts without pressure (the classic Wintab
   "first stroke loses pressure" bug).
   The hook therefore stays installed after the picker stops (g_active == 0)
   until every owed UP has been eaten; maintenance()/uninstall() decide when
   it is safe to unhook. */
static volatile LONG g_pending_left_up  = 0;
static volatile LONG g_pending_right_up = 0;

static LONG pending_count(void) {
    return InterlockedCompareExchange(&g_pending_left_up, 0, 0)
         + InterlockedCompareExchange(&g_pending_right_up, 0, 0);
}

/* Remove the hook only once it is idle (picker inactive + nothing owed). */
static void unhook_if_idle(void) {
    if (!g_active && pending_count() == 0 && g_hook) {
        HHOOK h = g_hook;
        g_hook = NULL;
        UnhookWindowsHookEx(h);
    }
}

LRESULT CALLBACK MouseProc(int nCode, WPARAM wParam, LPARAM lParam) {
    if (nCode >= 0) {
        /* Swallow the UP paired with a DOWN we ate.  Runs even after the
           picker stopped, so the hook can keep each click symmetric. */
        if (wParam == WM_LBUTTONUP &&
            InterlockedCompareExchange(&g_pending_left_up, 0, 0) > 0) {
            InterlockedDecrement(&g_pending_left_up);
            return 1; /* swallow */
        }
        if (wParam == WM_RBUTTONUP &&
            InterlockedCompareExchange(&g_pending_right_up, 0, 0) > 0) {
            InterlockedDecrement(&g_pending_right_up);
            return 1; /* swallow */
        }
        if (g_active) {
            if (wParam == WM_LBUTTONDOWN) {
                InterlockedExchange(&g_left_clicked, 1);
                InterlockedIncrement(&g_pending_left_up);
                return 1; /* swallow */
            }
            if (wParam == WM_RBUTTONDOWN) {
                InterlockedExchange(&g_right_clicked, 1);
                InterlockedIncrement(&g_pending_right_up);
                return 1; /* swallow */
            }
            if (wParam == WM_MOUSEWHEEL) {
                PMSLLHOOKSTRUCT p = (PMSLLHOOKSTRUCT)lParam;
                if (p) {
                    short delta = (short)HIWORD(p->mouseData);
                    InterlockedExchangeAdd(&g_wheel_delta, (LONG)delta);
                    return 1; /* swallow so background app doesn't scroll */
                }
            }
        }
    }
    return CallNextHookEx(NULL, nCode, wParam, lParam);
}

__declspec(dllexport) int install(void) {
    g_left_clicked = g_right_clicked = g_wheel_delta = 0;
    if (g_hook) {
        /* Reuse the live hook: a previous drain may still owe a button-up
           (pick restarted before the user released).  Just re-arm it so we
           never stack two hooks and never drop an owed UP. */
        g_active = 1;
        return 1;
    }
    g_active = 1;
    g_hook = SetWindowsHookExW(WH_MOUSE_LL, MouseProc,
                               GetModuleHandleW(NULL), 0);
    return g_hook != NULL;
}

__declspec(dllexport) void uninstall(void) {
    g_active = 0;
    /* Keep the hook alive while a button-up is still owed: it must stay
       installed to swallow it.  maintenance() unhooks when drained. */
    unhook_if_idle();
}

/* Force the hook out, dropping any owed UP (deadline / teardown path). */
__declspec(dllexport) void uninstall_force(void) {
    g_active = 0;
    g_pending_left_up = 0;
    g_pending_right_up = 0;
    if (g_hook) {
        HHOOK h = g_hook;
        g_hook = NULL;
        UnhookWindowsHookEx(h);
    }
}

/* Number of button-ups still owed to the foreground app. */
__declspec(dllexport) int pending(void) {
    return (int)pending_count();
}

/* Unhook once idle; returns 1 while the hook is still installed. */
__declspec(dllexport) int maintenance(void) {
    unhook_if_idle();
    return g_hook != NULL ? 1 : 0;
}

__declspec(dllexport) int left_clicked(void) {
    return InterlockedExchange(&g_left_clicked, 0);
}

__declspec(dllexport) int right_clicked(void) {
    return InterlockedExchange(&g_right_clicked, 0);
}

__declspec(dllexport) int get_wheel_delta(void) {
    return InterlockedExchange(&g_wheel_delta, 0);
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved) {
    (void)h; (void)reserved;
    if (reason == DLL_PROCESS_DETACH) uninstall_force();
    return TRUE;
}
