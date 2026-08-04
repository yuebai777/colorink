// dcomp_overlay.exe — fullscreen grayscale overlay (ShaderGlass-style reset).
//
// Single-process rewrite of the old two-process (overlay + capture-MMF) design:
//
//   * Capture uses Windows.Graphics.Capture (WGC) — the same capture API that
//     ShaderGlass v1.3.0 uses. WGC hands out GPU textures zero-copy (no CPU
//     round-trip, no staging, no MMF), and it auto-recovers on desktop
//     resolution/topology changes via frame-pool Recreate. The old design
//     memcpy'd every 4K frame GPU->CPU->MMF->CPU->GPU and froze forever the
//     moment the capture helper process died or hit an ACCESS_LOST loop.
//
//   * Output uses DirectComposition ("DComp passthrough"): the swap chain is
//     created with CreateSwapChainForComposition and attached to an
//     IDCompositionVisual. The visual is composited by the DWM on top of the
//     desktop but is NOT a window — so it can never intercept mouse input.
//     The host window is a 1x1 pixel topmost placeholder; clicks and the
//     scroll wheel reach the desktop windows below unconditionally. (A plain
//     fullscreen HWND + HTTRANSPARENT was proven to swallow clicks for
//     normal-z-order apps on this machine.)
//
//   * Present uses sync interval 0 + DXGI_PRESENT_ALLOW_TEARING, so the render
//     loop NEVER blocks on the DWM compositor. The old Present(1,0) could stall
//     indefinitely on throttled systems — which froze the overlay AND made the
//     control file unreadable, i.e. the filter could not be turned off.
//
//   * The host window is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE),
//     which excludes the DComp visual too — no feedback loop.
//
//   * Constant refresh (Win11 24H2+): MinUpdateInterval(0) makes WGC deliver
//     frames at the compositor cadence regardless of content changes, so a
//     static desktop no longer freezes the overlay.
//
// Control file: C:\Windows\Temp\dcomp_overlay_mode.txt   (0=off, 1=OKLCh, 2=Luma)
// Log file:     C:\Windows\Temp\dcomp_overlay.log
// Build: run build.bat
#include <windows.h>
#include <d3d11.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <dcomp.h>
#include <cstdio>
#include <vector>

#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Graphics.Capture.h>
#include "windows.graphics.interop.h"

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dcomp.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "dxguid.lib")
#pragma comment(lib, "windowsapp.lib")

using namespace winrt;
using namespace winrt::Windows::Foundation;
using namespace winrt::Windows::Graphics;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;
namespace WGC = winrt::Windows::Graphics::Capture;

#define CTRL_FILE "C:\\Windows\\Temp\\dcomp_overlay_mode.txt"
#define LOG_FILE  "C:\\Windows\\Temp\\dcomp_overlay.log"
#define WDA_EXCLUDEFROMCAPTURE 0x00000011

// --- Shaders (OKLCh / BT.709 Luma, unchanged from the previous build) ---
const char* SHADER_SRC = R"(
struct VS_IN  { float2 p:POSITION; float2 t:TEXCOORD0; };
struct VS_OUT { float4 p:SV_POSITION;  float2 t:TEXCOORD0; };
Texture2D tex0:register(t0); SamplerState smp:register(s0);
float3 s2l(float3 c){float3 lo=c/12.92;float3 hi=pow((c+0.055)/1.055,float3(2.4,2.4,2.4));return lerp(lo,hi,step(0.04045,c));}
float l2s(float c){if(c<=0.0031308)return 12.92*c;return 1.055*pow(c,1.0/2.4)-0.055;}
VS_OUT VS(VS_IN v){VS_OUT o;o.p=float4(v.p,0,1);o.t=v.t;return o;}
float4 PS_OKLCh(VS_OUT i):SV_TARGET{
    float3 col=tex0.Sample(smp,i.t).rgb;
    float3 lin=s2l(col);
    float3 lms = float3(
        dot(lin, float3(0.4122214708, 0.5363325363, 0.0514459929)),
        dot(lin, float3(0.2119034982, 0.6806995451, 0.1073969566)),
        dot(lin, float3(0.0883024619, 0.2817188376, 0.6299787005))
    );
    lms = sign(lms) * pow(abs(lms), 1.0 / 3.0);
    float L = dot(lms, float3(0.2104542553, 0.7936177850, -0.0040720468));
    float gray = l2s(clamp(L * L * L, 0.0, 1.0));
    return float4(gray, gray, gray, 1.0);
}
float4 PS_Luma(VS_OUT i):SV_TARGET{
    float3 col=tex0.Sample(smp,i.t).rgb;
    float gray=dot(col,float3(0.2126, 0.7152, 0.0722));
    return float4(gray,gray,gray,1.0);
}
)";

// --- Globals ---
ID3D11Device*        g_dev=0;
ID3D11DeviceContext* g_ctx=0;
IDXGISwapChain1*     g_sc=0;
ID3D11VertexShader*  g_vs=0;
ID3D11PixelShader*   g_psOKLCh=0,*g_psLuma=0;
ID3D11InputLayout*   g_il=0;
ID3D11SamplerState*  g_smp=0;
ID3D11Buffer*        g_vb=0;
ID3D11RenderTargetView* g_rtv=0;
ID3D11Texture2D*     g_srvTex=0;         // texture the cached SRV was made from
ID3D11ShaderResourceView* g_frameSRV=0;  // SRV cache (pool hands out <=2 textures)
HWND g_hwnd=0;
int g_screenX=0,g_screenY=0;             // primary monitor rect (physical px)
int g_screenW=1920,g_screenH=1080;
int g_renderW=1920,g_renderH=1080;       // swap chain buffer size (= monitor size)
DWORD64 g_lastModeRead=0;
int g_cachedMode=0;

// DirectComposition: the fullscreen content lives in a DComp visual (NOT a
// window), so mouse input can never be intercepted. The host HWND is a 1x1
// pixel topmost placeholder.
IDCompositionDevice*  g_dcomp=0;
IDCompositionTarget*  g_dcompTarget=0;
IDCompositionVisual*  g_dcompVis=0;
int g_tearing=0;   // 1 if the swap chain was created with ALLOW_TEARING

// WGC state (capture runs continuously; presentation is gated by the mode file)
HANDLE g_frameEvt=0;
CRITICAL_SECTION g_frameLock;
ID3D11Texture2D*     g_latestTex=0;      // guarded by g_frameLock
volatile LONG        g_latestW=0,g_latestH=0;
volatile LONG        g_captureClosed=0;  // session force-closed -> hide + rebuild
volatile LONG        g_captureOk=0;      // have we ever received a frame
WGC::Direct3D11CaptureFramePool g_framePool{nullptr};
WGC::GraphicsCaptureSession    g_session{nullptr};
WGC::GraphicsCaptureItem       g_item{nullptr};
IDirect3DDevice                g_wgcDevice{nullptr};
winrt::event_token             g_frameToken;
DWORD64 g_lastCaptureRetry=0;
int g_lastSessionState=-1;

static DWORD64 s_lastFps=0;
static int s_fpsFrames=0;
static int s_renderMs=0, s_presMs=0;
static LONGLONG s_lastPresentQpc=0;   // steady-present clock (ShaderGlass-style 60fps pacing)

void SafeRelease(::IUnknown* p){if(p)p->Release();}

static void Log(const char* msg) {
    FILE* f = fopen(LOG_FILE, "a");
    if (f) { fprintf(f, "%s\n", msg); fclose(f); }
}

static int ReadMode() {
    DWORD64 now = GetTickCount64();
    if (now - g_lastModeRead < 200) return g_cachedMode;
    g_lastModeRead = now;
    FILE* f = fopen(CTRL_FILE, "r");
    if (!f) { g_cachedMode = 0; return 0; }
    char buf[8]={0}; fgets(buf,sizeof(buf),f); fclose(f);
    g_cachedMode = (buf[0]>='0'&&buf[0]<='2') ? (buf[0]-'0') : 0;
    return g_cachedMode;
}

// --- D3D11 setup ---
// adapterIdx: 0 = default hardware, -1 = WARP software adapter
bool InitD3D11(int adapterIdx){
    D3D_FEATURE_LEVEL fl=D3D_FEATURE_LEVEL_11_0;
    HRESULT hr;
    if (adapterIdx < 0) {
        hr = D3D11CreateDevice(0, D3D_DRIVER_TYPE_WARP, 0, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            &fl, 1, D3D11_SDK_VERSION, &g_dev, 0, &g_ctx);
    } else if (adapterIdx == 0) {
        hr = D3D11CreateDevice(0, D3D_DRIVER_TYPE_HARDWARE, 0, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            &fl, 1, D3D11_SDK_VERSION, &g_dev, 0, &g_ctx);
    } else {
        IDXGIFactory1* fac=0; CreateDXGIFactory1(__uuidof(IDXGIFactory1),(void**)&fac);
        IDXGIAdapter* ad=0; fac->EnumAdapters((UINT)adapterIdx,&ad); fac->Release();
        hr = D3D11CreateDevice(ad, D3D_DRIVER_TYPE_UNKNOWN, 0, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            &fl, 1, D3D11_SDK_VERSION, &g_dev, 0, &g_ctx);
        if (ad) ad->Release();
    }
    if(FAILED(hr))return false;
    // D3D11_CREATE_DEVICE_BGRA_SUPPORT is mandatory for WGC interop.
    IDXGIDevice1* d1=0;
    g_dev->QueryInterface(__uuidof(IDXGIDevice1),(void**)&d1);
    if(d1){ d1->SetMaximumFrameLatency(1); d1->Release(); }
    return true;
}

bool CompileShaders(){
    ID3DBlob*b=0,*e=0;
    if(FAILED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"VS","vs_5_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&b,&e)))return false;
    g_dev->CreateVertexShader(b->GetBufferPointer(),b->GetBufferSize(),0,&g_vs);
    D3D11_INPUT_ELEMENT_DESC l[]={{"POSITION",0,DXGI_FORMAT_R32G32_FLOAT,0,0,D3D11_INPUT_PER_VERTEX_DATA,0},
        {"TEXCOORD",0,DXGI_FORMAT_R32G32_FLOAT,0,8,D3D11_INPUT_PER_VERTEX_DATA,0}};
    g_dev->CreateInputLayout(l,2,b->GetBufferPointer(),b->GetBufferSize(),&g_il);b->Release();
    if(SUCCEEDED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"PS_OKLCh","ps_5_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&b,&e)))
        {g_dev->CreatePixelShader(b->GetBufferPointer(),b->GetBufferSize(),0,&g_psOKLCh);b->Release();}
    if(SUCCEEDED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"PS_Luma","ps_5_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&b,&e)))
        {g_dev->CreatePixelShader(b->GetBufferPointer(),b->GetBufferSize(),0,&g_psLuma);b->Release();}
    return g_vs&&g_psOKLCh&&g_psLuma;
}

void InitQuad(){
    float v[16]={
        -1.0f,  1.0f, 0.0f, 0.0f,
         1.0f,  1.0f, 1.0f, 0.0f,
        -1.0f, -1.0f, 0.0f, 1.0f,
         1.0f, -1.0f, 1.0f, 1.0f
    };
    D3D11_BUFFER_DESC bd={sizeof(v),D3D11_USAGE_IMMUTABLE,D3D11_BIND_VERTEX_BUFFER};
    D3D11_SUBRESOURCE_DATA sd={v};
    g_dev->CreateBuffer(&bd,&sd,&g_vb);
    D3D11_SAMPLER_DESC sd2={};sd2.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd2.AddressU=sd2.AddressV=sd2.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;
    g_dev->CreateSamplerState(&sd2,&g_smp);
}

// --- Swap chain (never blocks: sync interval 0 + ALLOW_TEARING) ---
bool InitSwapChain(){
    SafeRelease(g_rtv);
    SafeRelease(g_sc);

    IDXGIDevice* dxgiDev=0;
    g_dev->QueryInterface(__uuidof(IDXGIDevice),(void**)&dxgiDev);
    if(!dxgiDev)return false;
    IDXGIAdapter* dxgiAdapter=0;
    dxgiDev->GetAdapter(&dxgiAdapter);
    dxgiDev->Release();
    if(!dxgiAdapter)return false;
    IDXGIFactory2* dxgiFactory=0;
    dxgiAdapter->GetParent(__uuidof(IDXGIFactory2),(void**)&dxgiFactory);
    dxgiAdapter->Release();
    if(!dxgiFactory)return false;

    DXGI_SWAP_CHAIN_DESC1 sd={};
    sd.Width=g_renderW; sd.Height=g_renderH;
    sd.Format=DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.SampleDesc.Count=1;
    sd.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount=3;
    sd.Scaling=DXGI_SCALING_STRETCH;       // required for composition swap chains
    sd.SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD;
    sd.AlphaMode=DXGI_ALPHA_MODE_IGNORE;   // opaque composition overlay
    sd.Flags=DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;   // non-blocking Present(0)
    HRESULT hr=dxgiFactory->CreateSwapChainForComposition(g_dev,&sd,nullptr,&g_sc);
    if(FAILED(hr)){
        // Some drivers reject ALLOW_TEARING — retry conservative.
        char b[96]; sprintf(b,"overlay: tearing swapchain hr=0x%08X, retrying",(unsigned)hr); Log(b);
        sd.Flags=0;
        hr=dxgiFactory->CreateSwapChainForComposition(g_dev,&sd,nullptr,&g_sc);
    }
    dxgiFactory->Release();
    if(FAILED(hr)){Log("overlay: CreateSwapChainForComposition FAILED");return false;}
    ID3D11Texture2D* pBB=nullptr;
    hr=g_sc->GetBuffer(0,__uuidof(ID3D11Texture2D),(void**)&pBB);
    if(FAILED(hr))return false;
    hr=g_dev->CreateRenderTargetView(pBB,nullptr,&g_rtv);
    pBB->Release();
    if(SUCCEEDED(hr)){
        DXGI_SWAP_CHAIN_DESC1 d1={}; g_sc->GetDesc1(&d1);
        g_tearing=(d1.Flags & DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING)?1:0;
        char b[160];
        sprintf(b,"overlay: swapchain %dx%d buf=%u eff=%u scale=%u alpha=%u flags=%u",
            d1.Width,d1.Height,(unsigned)d1.BufferCount,(unsigned)d1.SwapEffect,
            (unsigned)d1.Scaling,(unsigned)d1.AlphaMode,(unsigned)d1.Flags);
        Log(b);
    }
    return SUCCEEDED(hr);
}

// Attach the swap chain to a DirectComposition visual. The visual is
// composited above the desktop by the DWM but is not a window — mouse input
// goes to the desktop windows below, unconditionally.
bool InitDCompVisual(){
    if (!g_dcomp) {
        IDXGIDevice* dxgiDev=0;
        g_dev->QueryInterface(__uuidof(IDXGIDevice),(void**)&dxgiDev);
        if (!dxgiDev) return false;
        HRESULT hr=DCompositionCreateDevice(dxgiDev, __uuidof(IDCompositionDevice), (void**)&g_dcomp);
        dxgiDev->Release();
        if (FAILED(hr)) { Log("overlay: DCompositionCreateDevice FAILED"); return false; }
        hr=g_dcomp->CreateTargetForHwnd(g_hwnd, TRUE, &g_dcompTarget);   // TRUE = topmost band
        if (FAILED(hr)) { Log("overlay: CreateTargetForHwnd FAILED"); return false; }
        hr=g_dcomp->CreateVisual(&g_dcompVis);
        if (FAILED(hr)) { Log("overlay: CreateVisual FAILED"); return false; }
        g_dcompTarget->SetRoot(g_dcompVis);
        Log("overlay: DComp visual ready");
    }
    if (g_sc && g_dcompVis) {
        g_dcompVis->SetContent(g_sc);
        g_dcompVis->SetOffsetX((float)g_screenX);   // cover the primary monitor
        g_dcompVis->SetOffsetY((float)g_screenY);
        g_dcomp->Commit();
    }
    return true;
}

// --- Window (borderless, topmost, click-through, excluded from capture) ---
LRESULT CALLBACK WndProc(HWND h,UINT m,WPARAM w,LPARAM l){
    if(m==WM_NCHITTEST)return HTTRANSPARENT;   // click-through: mouse goes to the desktop below
    if(m==WM_DESTROY){PostQuitMessage(0);return 0;}
    return DefWindowProcW(h,m,w,l);
}

bool InitWindow(){
    // Diagnose the monitor/DPI environment from INSIDE this process.
    {
        char b[256];
        UINT dpi = GetDpiForSystem();
        HMONITOR mon0 = MonitorFromWindow(NULL, MONITOR_DEFAULTTOPRIMARY);
        MONITORINFO mi0 = {sizeof(mi0)};
        GetMonitorInfoW(mon0, &mi0);
        sprintf(b, "overlay: sysdpi=%u primary=(%d,%d)-(%d,%d)", dpi,
                mi0.rcMonitor.left, mi0.rcMonitor.top, mi0.rcMonitor.right, mi0.rcMonitor.bottom);
        Log(b);
        // enumerate all monitors from this process
        auto enumFn = [](HMONITOR h, HDC, LPRECT, LPARAM) -> BOOL {
            MONITORINFO mi = {sizeof(mi)};
            GetMonitorInfoW(h, &mi);
            char b2[160];
            sprintf(b2, "overlay:   monitor (%d,%d)-(%d,%d)%s", mi.rcMonitor.left, mi.rcMonitor.top,
                    mi.rcMonitor.right, mi.rcMonitor.bottom,
                    (mi.dwFlags & MONITORINFOF_PRIMARY) ? " PRIMARY" : "");
            Log(b2);
            return TRUE;
        };
        EnumDisplayMonitors(nullptr, nullptr, enumFn, 0);
    }
    HMONITOR mon=MonitorFromWindow(NULL,MONITOR_DEFAULTTOPRIMARY);
    MONITORINFO mi={sizeof(mi)};
    GetMonitorInfoW(mon,&mi);
    g_screenX=mi.rcMonitor.left; g_screenY=mi.rcMonitor.top;
    g_screenW=mi.rcMonitor.right-mi.rcMonitor.left;
    g_screenH=mi.rcMonitor.bottom-mi.rcMonitor.top;
    // Full resolution render (user preference: do not downscale).
    g_renderW=g_screenW; g_renderH=g_screenH;

    WNDCLASSEXW wc={sizeof(wc),0,WndProc,0,0,GetModuleHandleW(0),0,0,0,0,L"DCompOverlay",0};
    RegisterClassExW(&wc);
    // Fullscreen host window for the DComp target. The visual content is
    // CLIPPED TO THE WINDOW's visible region, so the window must be fullscreen
    // (a 1x1 host renders nothing). Input transparency comes from:
    //   * WS_EX_LAYERED — on Win8+ layered windows are hit-tested by their
    //     shape/alpha; without SetLayeredWindowAttributes the window does not
    //     intercept mouse input (this is the original DComp build's recipe and
    //     the documented click-through mechanism).
    //   * WS_EX_TRANSPARENT | WS_EX_NOACTIVATE + WM_NCHITTEST -> HTTRANSPARENT
    //     as belt-and-suspenders.
    g_hwnd=CreateWindowExW(
        WS_EX_TRANSPARENT|WS_EX_TOPMOST|WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW|WS_EX_LAYERED,
        L"DCompOverlay",L"DC",WS_POPUP,
        g_screenX,g_screenY,g_screenW,g_screenH,0,0,GetModuleHandleW(0),0);
    if(!g_hwnd)return false;
    ShowWindow(g_hwnd,SW_HIDE);
    // Must be applied after the window exists; WGC respects it, breaking the
    // feedback loop (overlay would otherwise capture itself).
    SetWindowDisplayAffinity(g_hwnd,WDA_EXCLUDEFROMCAPTURE);
    char b[96];
    sprintf(b,"overlay: desktop %d,%d %dx%d",g_screenX,g_screenY,g_screenW,g_screenH);
    Log(b);
    return true;
}

// --- WGC capture (zero-copy GPU frames) ---

// FrameArrived fires on a WGC threadpool thread. Extract the GPU texture,
// swap it in under the lock, and wake the render loop.
void OnFrameArrived(WGC::Direct3D11CaptureFramePool const& sender,
                    winrt::Windows::Foundation::IInspectable const&){
    try {
        auto frame = sender.TryGetNextFrame();
        if (!frame) return;

        // Resolution / topology change -> resize the frame pool (WGC heals
        // itself; no session restart needed).
        SizeInt32 cs = frame.ContentSize();
        if (cs.Width  != g_latestW || cs.Height != g_latestH) {
            if (g_latestW > 0 && g_latestH > 0) {
                char b[96];
                sprintf(b,"overlay: content size %dx%d -> %dx%d",
                    (int)g_latestW,(int)g_latestH,cs.Width,cs.Height);
                Log(b);
            }
            try { sender.Recreate(g_wgcDevice, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, cs); }
            catch (...) { }
        }

        // WGC frame surface -> underlying ID3D11Texture2D, zero-copy. The
        // free helper GetDXGIInterfaceFromObject no longer ships on modern
        // Windows, so QI the surface for IDirect3DDxgiInterfaceAccess and call
        // its GetInterface method (same result, always available).
        ID3D11Texture2D* tex=nullptr;
        {
            ::IDirect3DDxgiInterfaceAccess* access=nullptr;
            winrt::check_hresult(
                reinterpret_cast<::IUnknown*>(winrt::get_abi(frame.Surface()))
                    ->QueryInterface(__uuidof(IDirect3DDxgiInterfaceAccess),
                                    (void**)&access));
            winrt::check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D),
                                                      (void**)&tex));
            access->Release();
        }
        if (!tex) return;

        EnterCriticalSection(&g_frameLock);
        if (g_latestTex) g_latestTex->Release();
        g_latestTex=tex;                       // addref'd by GetDXGIInterfaceFromObject
        g_latestW=cs.Width; g_latestH=cs.Height;
        InterlockedExchange((volatile LONG*)&g_captureOk,1);
        LeaveCriticalSection(&g_frameLock);
        SetEvent(g_frameEvt);
    } catch (...) {
        Log("overlay: FrameArrived exception");
    }
}

void OnItemClosed(WGC::GraphicsCaptureItem const&,
                  winrt::Windows::Foundation::IInspectable const&){
    InterlockedExchange((volatile LONG*)&g_captureClosed,1);
    Log("overlay: capture item CLOSED");
}

void StopCapture();   // fwd (StartCapture tears down any stale state first)

// Start (or rebuild) the WGC capture session for the primary monitor.
bool StartCapture(){
    try {
        // Tear down anything stale first.
        StopCapture();

        IDXGIDevice* dxgiDev=0;
        g_dev->QueryInterface(__uuidof(IDXGIDevice),(void**)&dxgiDev);
        if(!dxgiDev){Log("capture: no IDXGIDevice");return false;}
        winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(
            dxgiDev, reinterpret_cast<::IInspectable**>(winrt::put_abi(g_wgcDevice))));
        dxgiDev->Release();
        if(!g_wgcDevice){Log("capture: CreateDirect3D11Device failed");return false;}

        // GraphicsCaptureItem from the primary monitor handle (interop).
        HMONITOR mon=MonitorFromWindow(NULL,MONITOR_DEFAULTTOPRIMARY);
        auto factory=winrt::get_activation_factory<WGC::GraphicsCaptureItem>();
        ::IGraphicsCaptureItemInterop* interop=nullptr;
        winrt::check_hresult(winrt::get_unknown(factory)->QueryInterface(
            __uuidof(IGraphicsCaptureItemInterop), (void**)&interop));
        g_item=nullptr;
        HRESULT hr=interop->CreateForMonitor(
            mon, winrt::guid_of<WGC::GraphicsCaptureItem>(), winrt::put_abi(g_item));
        interop->Release();
        winrt::check_hresult(hr);
        if(!g_item){Log("capture: CreateForMonitor failed");return false;}

        g_framePool=WGC::Direct3D11CaptureFramePool::CreateFreeThreaded(
            g_wgcDevice, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, g_item.Size());
        g_session=g_framePool.CreateCaptureSession(g_item);

        // Remove the yellow capture border (Win10 2004+).
        try { g_session.IsBorderRequired(false); } catch (...) { }
        // Exclude the cursor from the capture (Win10 2004+): the overlay shows
        // the desktop WITHOUT a captured cursor, so the real (live, DWM-drawn)
        // cursor is the only one on screen — no ghost/trailing cursor inside
        // the filter (same trick ShaderGlass's CaptureCursor option uses).
        try { g_session.IsCursorCaptureEnabled(false); } catch (...) { }

        g_frameToken=g_framePool.FrameArrived(OnFrameArrived);
        g_item.Closed(OnItemClosed);
        g_session.StartCapture();

        // Content-driven capture: WGC delivers a frame whenever the desktop
        // actually changes — including cursor movement — so the overlay always
        // tracks the live desktop while the DWM idles on truly static screens.
        // (MinUpdateInterval(0) forced the compositor to keep producing 4K
        // frames even with zero changes, saturating the virtual GPU and
        // dragging the whole desktop down to ~20-30fps under load — that was
        // the "jitter" and the reason the refresh rate never went up.)

        InterlockedExchange((volatile LONG*)&g_captureClosed,0);
        InterlockedExchange((volatile LONG*)&g_captureOk,0);
        Log("capture: WGC session started (content-driven)");
        return true;
    } catch (winrt::hresult_error const& e) {
        char b[128];
        sprintf(b,"capture: WGC start failed hr=0x%08X",(unsigned)e.code().value);
        Log(b);
        return false;
    } catch (...) {
        Log("capture: WGC start failed (unknown)");
        return false;
    }
}

void StopCapture(){
    if (g_framePool) {
        try { g_framePool.FrameArrived(g_frameToken); } catch (...) { }
        g_framePool=nullptr;
    }
    if (g_session) { g_session=nullptr; }  // close() via destructor
    if (g_item)    { g_item=nullptr; }
    if (g_wgcDevice){ g_wgcDevice=nullptr; }
    EnterCriticalSection(&g_frameLock);
    if (g_latestTex){ g_latestTex->Release(); g_latestTex=0; }
    g_latestW=g_latestH=0;
    LeaveCriticalSection(&g_frameLock);
}

// --- Rendering ---
void Render(int mode){
    ID3D11PixelShader* ps=(mode==2)?g_psLuma:g_psOKLCh;
    if(!ps||!g_rtv)return;

    ID3D11Texture2D* tex=nullptr;
    EnterCriticalSection(&g_frameLock);
    if (g_latestTex){ tex=g_latestTex; tex->AddRef(); }
    LeaveCriticalSection(&g_frameLock);
    if(!tex)return;

    if (tex!=g_srvTex) {
        if (g_frameSRV){ SafeRelease(g_frameSRV); SafeRelease(g_srvTex); }
        D3D11_TEXTURE2D_DESC d; tex->GetDesc(&d);
        if (d.BindFlags & D3D11_BIND_SHADER_RESOURCE) {
            if (SUCCEEDED(g_dev->CreateShaderResourceView(tex,nullptr,&g_frameSRV))) {
                g_srvTex=tex; g_srvTex->AddRef();
            }
        } else {
            // WGC frame textures are normally SRV-able; skip if not.
            tex->Release();
            return;
        }
    }
    if(!g_frameSRV){ tex->Release(); return; }

    D3D11_VIEWPORT vp={0,0,(float)g_renderW,(float)g_renderH,0,1};
    g_ctx->RSSetViewports(1,&vp);
    g_ctx->OMSetRenderTargets(1,&g_rtv,0);
    g_ctx->PSSetShader(ps,0,0);
    g_ctx->PSSetShaderResources(0,1,&g_frameSRV);
    g_ctx->Draw(4,0);
    ID3D11ShaderResourceView* n=0;
    g_ctx->PSSetShaderResources(0,1,&n);
    tex->Release();

    LARGE_INTEGER qpf,qa,qb;
    QueryPerformanceFrequency(&qpf);
    QueryPerformanceCounter(&qa);
    // Async present with sync interval 0 — exactly ShaderGlass's approach:
    // the render loop NEVER blocks on the DWM compositor. Present(1,0) was
    // observed to add visible judder on this VM: every DWM tick hiccup froze
    // our loop (blocked inside Present) and then jumped, amplifying the
    // compositor's timing jitter. With sync 0 the DWM picks up each queued
    // frame at its own cadence and a compositor stall cannot freeze us.
    UINT flags=g_tearing?DXGI_PRESENT_ALLOW_TEARING:0;
    if (g_sc) g_sc->Present(0,flags);
    QueryPerformanceCounter(&qb);
    s_presMs += (int)((qb.QuadPart-qa.QuadPart)*1000/qpf.QuadPart);
}

// Probe GPU texture-read latency (the pathological path on broken drivers);
// fall back to WARP if the hardware path is unusably slow.
bool IsAdapterFast(){
    const UINT PW=(UINT)g_screenW, PH=(UINT)g_screenH;
    ID3D11Texture2D* rt=0;
    D3D11_TEXTURE2D_DESC rd={};
    rd.Width=PW; rd.Height=PH; rd.MipLevels=1; rd.ArraySize=1;
    rd.Format=DXGI_FORMAT_B8G8R8A8_UNORM; rd.SampleDesc.Count=1;
    rd.Usage=D3D11_USAGE_DEFAULT; rd.BindFlags=D3D11_BIND_RENDER_TARGET;
    if(FAILED(g_dev->CreateTexture2D(&rd,0,&rt)))return false;
    ID3D11RenderTargetView* rtv=0;
    if(FAILED(g_dev->CreateRenderTargetView(rt,0,&rtv))){rt->Release();return false;}

    ID3D11Texture2D* staging=0;
    rd.Usage=D3D11_USAGE_STAGING; rd.BindFlags=0; rd.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    if(FAILED(g_dev->CreateTexture2D(&rd,0,&staging))){rtv->Release();rt->Release();return false;}

    ID3D11Texture2D* st=0;
    D3D11_TEXTURE2D_DESC sd={};
    sd.Width=PW; sd.Height=PH; sd.MipLevels=1; sd.ArraySize=1;
    sd.Format=DXGI_FORMAT_B8G8R8A8_UNORM; sd.SampleDesc.Count=1;
    sd.Usage=D3D11_USAGE_DEFAULT; sd.BindFlags=D3D11_BIND_SHADER_RESOURCE;
    ID3D11ShaderResourceView* srv=0;
    if(SUCCEEDED(g_dev->CreateTexture2D(&sd,0,&st))){
        std::vector<unsigned> buf((size_t)PW*PH,0xFFFFFFFF);
        g_ctx->UpdateSubresource(st,0,nullptr,buf.data(),PW*4,0);
        g_dev->CreateShaderResourceView(st,0,&srv);
        st->Release();
    }
    if(!srv){staging->Release();rtv->Release();rt->Release();return false;}

    D3D11_VIEWPORT vp={0,0,(float)PW,(float)PH,0,1};
    g_ctx->RSSetViewports(1,&vp);
    g_ctx->OMSetRenderTargets(1,&rtv,0);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->IASetInputLayout(g_il);
    UINT str=16,off=0;
    g_ctx->IASetVertexBuffers(0,1,&g_vb,&str,&off);
    g_ctx->VSSetShader(g_vs,0,0);
    g_ctx->PSSetShader(g_psOKLCh,0,0);
    g_ctx->PSSetShaderResources(0,1,&srv);
    g_ctx->PSSetSamplers(0,1,&g_smp);
    g_ctx->Draw(4,0);
    g_ctx->CopyResource(staging,rt);
    g_ctx->Flush();
    LARGE_INTEGER qpf,qa,qb;
    QueryPerformanceFrequency(&qpf);
    QueryPerformanceCounter(&qa);
    D3D11_MAPPED_SUBRESOURCE mapped;
    HRESULT hr=g_ctx->Map(staging,0,D3D11_MAP_READ,0,&mapped);
    QueryPerformanceCounter(&qb);
    double ms=(qb.QuadPart-qa.QuadPart)*1000.0/qpf.QuadPart;
    bool fast=(hr==S_OK)&&ms<15.0;
    char b[96];
    sprintf(b,"overlay: probe draw %.1fms -> %s",ms,fast?"fast":"SLOW, switching to WARP");
    Log(b);
    if(hr==S_OK)g_ctx->Unmap(staging,0);
    srv->Release();staging->Release();rtv->Release();rt->Release();
    return fast;
}

void InitRenderStateStatic(){
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->IASetInputLayout(g_il);
    UINT str=16,off=0;
    g_ctx->IASetVertexBuffers(0,1,&g_vb,&str,&off);
    g_ctx->VSSetShader(g_vs,0,0);
    g_ctx->PSSetSamplers(0,1,&g_smp);
}

int WINAPI WinMain(HINSTANCE hInst,HINSTANCE,LPSTR,int){
    // DPI awareness is CRITICAL for input: a DPI-unaware process sees the
    // desktop in virtualized coordinates (2560x1440 @150% = 3840x2160), which
    // breaks HTTRANSPARENT click-forwarding to DPI-aware apps below (mixed-DPI
    // hit-testing drops the message -> clicks/wheel die). Must be per-monitor
    // aware so the overlay lives in real physical coordinates.
    if (!SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) {
        Log("overlay: PER_MONITOR_AWARE_V2 unavailable, falling back to SetProcessDPIAware");
        if (!SetProcessDPIAware())
            Log("overlay: WARNING SetProcessDPIAware also failed");
    }
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    InitializeCriticalSection(&g_frameLock);
    g_frameEvt=CreateEventW(nullptr,FALSE,FALSE,nullptr);

    if(!InitWindow()){Log("overlay: InitWindow FAILED");return 1;}
    Log("InitD3D11...");
    if(!InitD3D11(0)){Log("FAILED");return 2;}
    if(!CompileShaders()){Log("Shaders FAILED");return 3;}
    InitQuad();
    if(!IsAdapterFast()){
        SafeRelease(g_ctx);SafeRelease(g_dev);
        g_ctx=0;g_dev=0;
        if(!InitD3D11(-1)){Log("WARP FAILED");return 2;}   // WARP software adapter
        CompileShaders();
        InitQuad();
    }
    Log("D3D11 OK");
    if(!InitSwapChain()){Log("RenderTex FAILED");return 4;}
    if(!InitDCompVisual()){Log("DComp FAILED");return 6;}
    InitRenderStateStatic();
    if(!StartCapture()){Log("capture: start FAILED");return 5;}
    Log("capture: ready");

    MSG msg;
    int lastMode=-1;
    for(;;){
        while(PeekMessage(&msg,0,0,0,PM_REMOVE)){
            if(msg.message==WM_QUIT)goto exit;
            TranslateMessage(&msg);DispatchMessage(&msg);
        }

        int mode=ReadMode();
        if(mode!=lastMode){
            lastMode=mode;
            if(mode==0){ShowWindow(g_hwnd,SW_HIDE);Log("overlay: disabled");}
            else       {ShowWindow(g_hwnd,SW_SHOWNOACTIVATE);Log("overlay: enabled");}
        }

        // WGC session force-closed (screen lock / GPU reset / capture revoked):
        // hide so no stale frame lingers, and transparently re-establish.
        if(mode!=0 && g_captureClosed){
            if(GetTickCount64()-g_lastCaptureRetry>2000){
                g_lastCaptureRetry=GetTickCount64();
                if(StartCapture()){
                    ShowWindow(g_hwnd,SW_SHOWNOACTIVATE);
                    Log("overlay: capture re-established");
                }
            }
            Sleep(16);
            continue;
        }

        // Re-query the primary monitor geometry every second so the overlay
        // follows real resolution changes (swap chain resize + visual offset).
        {
            static DWORD64 s_lastGeom=0;
            DWORD64 nowG=GetTickCount64();
            if(nowG-s_lastGeom>1000){
                s_lastGeom=nowG;
                HMONITOR mon=MonitorFromWindow(NULL,MONITOR_DEFAULTTOPRIMARY);
                MONITORINFO mi={sizeof(mi)};
                GetMonitorInfoW(mon,&mi);
                int w=mi.rcMonitor.right-mi.rcMonitor.left;
                int h=mi.rcMonitor.bottom-mi.rcMonitor.top;
                int x=mi.rcMonitor.left,y=mi.rcMonitor.top;
                if(w!=g_screenW||h!=g_screenH||x!=g_screenX||y!=g_screenY){
                    g_screenX=x;g_screenY=y;g_screenW=w;g_screenH=h;
                    g_renderW=w;g_renderH=h;
                    InitSwapChain();
                    InitDCompVisual();   // re-attach the new chain to the visual
                    char b[96];
                    sprintf(b,"overlay: resized to %dx%d",w,h);
                    Log(b);
                }
            }
        }

        if(mode==0){Sleep(16);continue;}

        // Present on frame arrival (content-driven), async Present(0) — the
        // ShaderGlass loop model. A fixed 60fps present clock was tried but
        // backfired under compositor load: forced presents pile up in the
        // swap chain and block, dragging FPS down. Presenting only when a new
        // capture frame arrives matches the compositor's actual rate and never
        // blocks (pres ~0ms).
        if (WaitForSingleObject(g_frameEvt, 16) != WAIT_OBJECT_0) continue;
        if(!g_captureOk)continue;             // no frame yet — skip, never black

        LARGE_INTEGER qpf,qa,qb;
        QueryPerformanceFrequency(&qpf);
        QueryPerformanceCounter(&qa);
        Render(mode);
        QueryPerformanceCounter(&qb);
        s_renderMs+=(int)((qb.QuadPart-qa.QuadPart)*1000/qpf.QuadPart);
        s_fpsFrames++;
        if(GetTickCount64()-s_lastFps>=5000){
            char buf[96];
            sprintf(buf,"FPS: %d | render %dms (pres %dms)",
                s_fpsFrames/5,
                s_fpsFrames?s_renderMs/s_fpsFrames:0,
                s_fpsFrames?s_presMs/s_fpsFrames:0);
            Log(buf);
            s_fpsFrames=0;s_renderMs=0;s_presMs=0;
            s_lastFps=GetTickCount64();
        }
    }
exit:
    Log("overlay: exiting");
    StopCapture();
    if(g_frameEvt)CloseHandle(g_frameEvt);
    DeleteCriticalSection(&g_frameLock);
    SafeRelease(g_dcompVis);SafeRelease(g_dcompTarget);SafeRelease(g_dcomp);
    SafeRelease(g_frameSRV);SafeRelease(g_srvTex);
    SafeRelease(g_rtv);SafeRelease(g_sc);
    SafeRelease(g_smp);SafeRelease(g_vb);SafeRelease(g_il);
    SafeRelease(g_vs);SafeRelease(g_psOKLCh);SafeRelease(g_psLuma);
    SafeRelease(g_ctx);SafeRelease(g_dev);
    if(g_hwnd)DestroyWindow(g_hwnd);
    winrt::uninit_apartment();
    return 0;
}
