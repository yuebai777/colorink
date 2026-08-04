// mag_filter.exe — full-desktop grayscale via MagSetFullscreenColorEffect
//
// Applies a 5x5 color matrix at the DWM compositor level (the same path as
// Windows' built-in color filters). No capture, no overlay window, and it
// does NOT touch the display's ICC profile — so it avoids the color-managed
// output transform that tints grayscale on some displays.
//
// Control file: %SYSTEMROOT%\Temp\mag_filter_mode.txt   (0=off, 1=on)
// Log    file: %SYSTEMROOT%\Temp\mag_filter.log
//
// Build: run build.bat

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <string>

#pragma comment(lib, "user32.lib")

typedef BOOL(WINAPI* FnMagInitialize)(void);
typedef BOOL(WINAPI* FnMagUninitialize)(void);
typedef BOOL(WINAPI* FnMagSetFullscreenTransform)(float magLevel, int xOffset, int yOffset);
typedef BOOL(WINAPI* FnMagSetFullscreenColorEffect)(const float* pEffect);

static FnMagInitialize g_MagInitialize;
static FnMagUninitialize g_MagUninitialize;
static FnMagSetFullscreenTransform g_MagSetFullscreenTransform;
static FnMagSetFullscreenColorEffect g_MagSetFullscreenColorEffect;

static std::wstring g_sysTemp;

static void Log(const wchar_t* msg) {
    std::wstring p = g_sysTemp + L"\\mag_filter.log";
    HANDLE h = CreateFileW(p.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME st; GetLocalTime(&st);
    wchar_t buf[1024];
    int n = swprintf(buf, 1024, L"[%02d:%02d:%02d.%03d] %s\r\n", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, msg);
    DWORD w; WriteFile(h, buf, n * sizeof(wchar_t), &w, nullptr);
    CloseHandle(h);
}

static std::wstring GetEnv(const wchar_t* name, const wchar_t* fallback) {
    DWORD n = GetEnvironmentVariableW(name, nullptr, 0);
    if (n == 0) return fallback ? fallback : L"";
    std::wstring v(n, L'\0');
    GetEnvironmentVariableW(name, &v[0], n);
    v.resize(n - 1);
    return v;
}

static std::string ReadAsciiFile(const std::wstring& path) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return "";
    DWORD size = GetFileSize(h, nullptr);
    std::string s(size, '\0');
    DWORD read = 0;
    ReadFile(h, &s[0], size, &read, nullptr);
    CloseHandle(h);
    return s;
}

static int ReadMode() {
    std::string content = ReadAsciiFile(g_sysTemp + L"\\mag_filter_mode.txt");
    if (content.empty()) return 0;
    return content[0] == '1' ? 1 : 0;
}

// Identity 5x5 color matrix (COLOREFFECT layout, row-major)
static const float IDENTITY[25] = {
    1, 0, 0, 0, 0,
    0, 1, 0, 0, 0,
    0, 0, 1, 0, 0,
    0, 0, 0, 1, 0,
    0, 0, 0, 0, 1,
};

// BT.709 luminance grayscale (sRGB-encoded space, matches the existing
// OpenGL/DComp "Luma" shaders).
//
// NOTE: MagSetFullscreenColorEffect applies the matrix per-COLUMN (out channel
// j = column j · input), so the grayscale coefficients go in the COLUMNS.
static const float GRAYSCALE[25] = {
    0.2126f, 0.2126f, 0.2126f, 0, 0,
    0.7152f, 0.7152f, 0.7152f, 0, 0,
    0.0722f, 0.0722f, 0.0722f, 0, 0,
    0, 0, 0, 1, 0,
    0, 0, 0, 0, 1,
};

// NVDA workaround: after a color effect + MagUninitialize, the next
// MagSetFullscreenTransform silently fails. Run a dummy init/effect/uninit
// cycle to clear the stale API state.
static void ClearStaleApiState() {
    if (!g_MagInitialize || !g_MagSetFullscreenColorEffect || !g_MagUninitialize) return;
    if (g_MagInitialize()) {
        g_MagSetFullscreenColorEffect(IDENTITY);
        g_MagUninitialize();
    }
}

static bool ApplyEffect(bool on) {
    if (!g_MagInitialize || !g_MagSetFullscreenColorEffect) return false;
    if (on) {
        if (!g_MagInitialize()) {
            Log(L"MagInitialize failed");
            return false;
        }
        // Own the pipeline at zoom 1.0 (full-screen, no magnification).
        if (g_MagSetFullscreenTransform) g_MagSetFullscreenTransform(1.0f, 0, 0);
        BOOL ok = g_MagSetFullscreenColorEffect(GRAYSCALE);
        Log(ok ? L"Grayscale ON" : L"MagSetFullscreenColorEffect failed (ON)");
        return ok != FALSE;
    } else {
        BOOL ok = g_MagSetFullscreenColorEffect(IDENTITY);
        if (g_MagUninitialize) g_MagUninitialize();
        Log(ok ? L"Grayscale OFF" : L"MagSetFullscreenColorEffect failed (OFF)");
        return ok != FALSE;
    }
}

int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    g_sysTemp = GetEnv(L"SYSTEMROOT", L"C:\\Windows") + L"\\Temp";

    HMODULE mag = LoadLibraryW(L"Magnification.dll");
    if (!mag) { Log(L"Magnification.dll not found"); return 1; }
    g_MagInitialize = (FnMagInitialize)GetProcAddress(mag, "MagInitialize");
    g_MagUninitialize = (FnMagUninitialize)GetProcAddress(mag, "MagUninitialize");
    g_MagSetFullscreenTransform = (FnMagSetFullscreenTransform)GetProcAddress(mag, "MagSetFullscreenTransform");
    g_MagSetFullscreenColorEffect = (FnMagSetFullscreenColorEffect)GetProcAddress(mag, "MagSetFullscreenColorEffect");
    if (!g_MagInitialize || !g_MagSetFullscreenColorEffect) {
        Log(L"Magnification API not available");
        return 2;
    }

    // NVDA stale-state workaround before first real init.
    ClearStaleApiState();

    int mode = ReadMode();
    if (mode) ApplyEffect(true);
    Log(mode ? L"Startup: active" : L"Startup: idle");

    int lastMode = mode;
    for (;;) {
        Sleep(200);
        int m = ReadMode();
        if (m != lastMode) {
            lastMode = m;
            ApplyEffect(m != 0);
        }
    }
    return 0;
}
