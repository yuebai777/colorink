// d3d11_overlay.exe — fullscreen OKLCh grayscale via D3D11 + DXGI Desktop Duplication
//
// Runs as a standalone process. Python controls it via a file:
//   C:\Windows\Temp\d3d11_overlay_mode.txt
//   0 = disabled (hide window), 1 = OKLCh, 2 = Luma (BT.709)
//
// Build: run build.bat (requires MSVC + Windows SDK)
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <cstdio>
#include <atomic>
#include <thread>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dxguid.lib")

// ---------------------------------------------------------------------------
// Control
// ---------------------------------------------------------------------------
#define CTRL_FILE "C:\\Windows\\Temp\\d3d11_overlay_mode.txt"
#define LOG_FILE  "C:\\Windows\\Temp\\d3d11_overlay.log"
#define WDA_EXCLUDEFROMCAPTURE 0x00000011

static void Log(const char* msg) {
    FILE* f = fopen(LOG_FILE, "a");
    if (f) { fprintf(f, "%s\n", msg); fclose(f); }
    OutputDebugStringA(msg);
}

static std::atomic<int> g_mode(0);

int ReadMode() {
    FILE* f = fopen(CTRL_FILE, "r");
    if (!f) return 0;
    char buf[8] = {0};
    fgets(buf, sizeof(buf), f);
    fclose(f);
    return (buf[0] >= '0' && buf[0] <= '2') ? (buf[0] - '0') : 0;
}

// ---------------------------------------------------------------------------
// OKLCh + Luma HLSL shaders (embedded)
// ---------------------------------------------------------------------------
const char* SHADER_SRC = R"(
struct VS_INPUT  { float2 pos : POSITION; float2 tex : TEXCOORD0; };
struct VS_OUTPUT { float4 pos : SV_POSITION;  float2 tex : TEXCOORD0; };
Texture2D screenTex : register(t0);
SamplerState smp : register(s0);

float3 srgbToLinear(float3 c) {
    float3 lo = c / 12.92;
    float3 hi = pow((c + 0.055) / 1.055, float3(2.4,2.4,2.4));
    return lerp(lo, hi, step(0.04045, c));
}
float linearToSrgb(float c) {
    if (c <= 0.0031308) return 12.92 * c;
    return 1.055 * pow(c, 1.0/2.4) - 0.055;
}

VS_OUTPUT VS(VS_INPUT v) {
    VS_OUTPUT o;
    o.pos = float4(v.pos, 0, 1);
    o.tex = v.tex;
    return o;
}

// OKLCh perceptual grayscale
float4 PS_OKLCh(VS_OUTPUT i) : SV_TARGET {
    float3 col = screenTex.Sample(smp, i.tex).rgb;
    float3 lin = srgbToLinear(col);
    float3 lms = float3(
        dot(lin, float3(0.4122214708, 0.5363325363, 0.0514459929)),
        dot(lin, float3(0.2119034982, 0.6806995451, 0.1073969566)),
        dot(lin, float3(0.0883024619, 0.2817188376, 0.6299787005))
    );
    lms = sign(lms) * pow(abs(lms), 1.0 / 3.0);
    float L = dot(lms, float3(0.2104542553, 0.7936177850, -0.0040720468));
    float gray = linearToSrgb(clamp(L * L * L, 0.0, 1.0));
    return float4(gray, gray, gray, 1.0);
}

// BT.709 luma
float4 PS_Luma(VS_OUTPUT i) : SV_TARGET {
    float3 col = screenTex.Sample(smp, i.tex).rgb;
    float gray = dot(col, float3(0.2126, 0.7152, 0.0722));
    return float4(gray, gray, gray, 1.0);
}
)";

// ---------------------------------------------------------------------------
// D3D11 state
// ---------------------------------------------------------------------------
ID3D11Device*           g_dev   = nullptr;
ID3D11DeviceContext*    g_ctx   = nullptr;
IDXGISwapChain*         g_sc    = nullptr;
ID3D11VertexShader*     g_vs    = nullptr;
ID3D11PixelShader*      g_psOKLCh = nullptr;
ID3D11PixelShader*      g_psLuma = nullptr;
ID3D11InputLayout*      g_il    = nullptr;
ID3D11SamplerState*     g_smp   = nullptr;
ID3D11Buffer*           g_vb    = nullptr;

// Desktop duplication
IDXGIOutputDuplication* g_dup   = nullptr;
ID3D11Texture2D*        g_dupTex = nullptr;
ID3D11ShaderResourceView* g_dupSRV = nullptr;
int g_dupW = 0, g_dupH = 0;

// Window
HWND g_hwnd = nullptr;
int g_winW = 0, g_winH = 0;

// ---------------------------------------------------------------------------
void SafeRelease(IUnknown* p) { if (p) p->Release(); }

bool InitD3D11(HWND hwnd, int w, int h) {
    DXGI_SWAP_CHAIN_DESC scd = {};
    scd.BufferCount = 2;
    scd.BufferDesc.Width  = w;
    scd.BufferDesc.Height = h;
    scd.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    scd.BufferDesc.RefreshRate.Numerator   = 60;
    scd.BufferDesc.RefreshRate.Denominator = 1;
    scd.BufferUsage  = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow = hwnd;
    scd.SampleDesc.Count = 1;
    scd.Windowed = TRUE;
    scd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    scd.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_MODE_SWITCH;

    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    UINT createFlags = 0;
#ifdef _DEBUG
    createFlags |= D3D11_CREATE_DEVICE_DEBUG;
#endif
    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, createFlags,
        &fl, 1, D3D11_SDK_VERSION, &scd,
        &g_sc, &g_dev, nullptr, &g_ctx);
    if (FAILED(hr)) return false;
    return true;
}

bool InitDuplication(int outputIdx) {
    IDXGIDevice* dxgiDev = nullptr;
    if (FAILED(g_dev->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDev)))
        return false;

    IDXGIAdapter* adapter = nullptr;
    dxgiDev->GetAdapter(&adapter);
    dxgiDev->Release();

    IDXGIOutput* output = nullptr;
    if (FAILED(adapter->EnumOutputs(outputIdx, &output))) {
        adapter->Release();
        return false;
    }
    adapter->Release();

    IDXGIOutput1* output1 = nullptr;
    if (FAILED(output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&output1))) {
        output->Release();
        return false;
    }
    output->Release();

    HRESULT hr = output1->DuplicateOutput(g_dev, &g_dup);
    output1->Release();
    return SUCCEEDED(hr);
}

bool CompileShaders() {
    // Vertex shader
    ID3DBlob* blob = nullptr, *err = nullptr;
    if (FAILED(D3DCompile(SHADER_SRC, strlen(SHADER_SRC), nullptr, nullptr, nullptr,
                          "VS", "vs_5_0", 0, 0, &blob, &err))) {
        if (err) { OutputDebugStringA((char*)err->GetBufferPointer()); err->Release(); }
        return false;
    }
    g_dev->CreateVertexShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr, &g_vs);

    D3D11_INPUT_ELEMENT_DESC layout[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 8, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    g_dev->CreateInputLayout(layout, 2, blob->GetBufferPointer(), blob->GetBufferSize(), &g_il);
    blob->Release();

    // Pixel shaders
    if (SUCCEEDED(D3DCompile(SHADER_SRC, strlen(SHADER_SRC), nullptr, nullptr, nullptr,
                             "PS_OKLCh", "ps_5_0", 0, 0, &blob, &err))) {
        g_dev->CreatePixelShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr, &g_psOKLCh);
        blob->Release();
    }
    if (SUCCEEDED(D3DCompile(SHADER_SRC, strlen(SHADER_SRC), nullptr, nullptr, nullptr,
                             "PS_Luma", "ps_5_0", 0, 0, &blob, &err))) {
        g_dev->CreatePixelShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr, &g_psLuma);
        blob->Release();
    }
    return g_vs && g_psOKLCh && g_psLuma;
}

void InitQuad() {
    float verts[16] = {
        -1,-1, 0,1,   1,-1, 1,1,   1, 1, 1,0,   -1, 1, 0,0,
    };
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = sizeof(verts);
    bd.Usage = D3D11_USAGE_IMMUTABLE;
    bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    D3D11_SUBRESOURCE_DATA sd = { verts };
    g_dev->CreateBuffer(&bd, &sd, &g_vb);

    D3D11_SAMPLER_DESC sd2 = {};
    sd2.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd2.AddressU = sd2.AddressV = sd2.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    g_dev->CreateSamplerState(&sd2, &g_smp);
}

bool CaptureDesktop() {
    IDXGIResource* res = nullptr;
    DXGI_OUTDUPL_FRAME_INFO fi;
    HRESULT hr = g_dup->AcquireNextFrame(0, &fi, &res);
    if (FAILED(hr)) return false;

    ID3D11Texture2D* tex = nullptr;
    res->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&tex);
    res->Release();

    if (!tex) {
        g_dup->ReleaseFrame();
        return false;
    }

    // Recreate SRV if texture size changed
    D3D11_TEXTURE2D_DESC desc;
    tex->GetDesc(&desc);
    if (desc.Width != (UINT)g_dupW || desc.Height != (UINT)g_dupH) {
        SafeRelease(g_dupSRV);
        SafeRelease(g_dupTex);
        g_dupTex = tex;
        g_dupTex->AddRef();
        g_dev->CreateShaderResourceView(tex, nullptr, &g_dupSRV);
        g_dupW = desc.Width;
        g_dupH = desc.Height;
    } else {
        // Copy to our texture for shader input
        if (g_dupTex) g_ctx->CopyResource(g_dupTex, tex);
    }

    tex->Release();
    g_dup->ReleaseFrame();
    return g_dupSRV != nullptr;
}

void Render(int mode) {
    ID3D11RenderTargetView* rtv = nullptr;
    ID3D11Texture2D* backBuf = nullptr;
    g_sc->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&backBuf);
    if (!backBuf) return;
    g_dev->CreateRenderTargetView(backBuf, nullptr, &rtv);
    backBuf->Release();
    if (!rtv) return;

    float clear[4] = {0,0,0,0};
    g_ctx->ClearRenderTargetView(rtv, clear);
    g_ctx->OMSetRenderTargets(1, &rtv, nullptr);

    D3D11_VIEWPORT vp = {0, 0, (float)g_winW, (float)g_winH, 0, 1};
    g_ctx->RSSetViewports(1, &vp);

    ID3D11PixelShader* ps = (mode == 2) ? g_psLuma : g_psOKLCh;
    if (!ps) { rtv->Release(); return; }

    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->IASetInputLayout(g_il);
    UINT stride = 16, offset = 0;
    g_ctx->IASetVertexBuffers(0, 1, &g_vb, &stride, &offset);
    g_ctx->VSSetShader(g_vs, nullptr, 0);
    g_ctx->PSSetShader(ps, nullptr, 0);
    g_ctx->PSSetShaderResources(0, 1, &g_dupSRV);
    g_ctx->PSSetSamplers(0, 1, &g_smp);

    g_ctx->Draw(4, 0);

    // Unbind SRV
    ID3D11ShaderResourceView* nullSRV = nullptr;
    g_ctx->PSSetShaderResources(0, 1, &nullSRV);

    rtv->Release();
    g_sc->Present(1, 0);  // vsync on
}

// ---------------------------------------------------------------------------
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR, int) {
    SetProcessDPIAware();
    int screenIdx = 0;  // default to primary monitor

    // Target screen geometry
    int x = 0, y = 0, w = 1920, h = 1080;
    {
        // Get screen info via DXGI
        IDXGIFactory1* factory = nullptr;
        if (SUCCEEDED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory))) {
            IDXGIAdapter* adapter = nullptr;
            if (SUCCEEDED(factory->EnumAdapters(0, &adapter))) {
                IDXGIOutput* output = nullptr;
                if (SUCCEEDED(adapter->EnumOutputs(screenIdx, &output))) {
                    DXGI_OUTPUT_DESC desc;
                    output->GetDesc(&desc);
                    x = desc.DesktopCoordinates.left;
                    y = desc.DesktopCoordinates.top;
                    w = desc.DesktopCoordinates.right  - desc.DesktopCoordinates.left;
                    h = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top;
                    output->Release();
                }
                adapter->Release();
            }
            factory->Release();
        }
    }
    g_winW = w; g_winH = h;

    // Create frameless layered topmost window
    WNDCLASSEX wc = {sizeof(wc), CS_HREDRAW|CS_VREDRAW, WndProc, 0, 0, hInst,
                     nullptr, nullptr, nullptr, nullptr, "D3D11OverlayClass", nullptr};
    RegisterClassEx(&wc);

    g_hwnd = CreateWindowEx(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
        "D3D11OverlayClass", "D3D11 Overlay",
        WS_POPUP,
        x, y, w, h,
        nullptr, nullptr, hInst, nullptr);

    if (!g_hwnd) return 1;
    // NO SetLayeredWindowAttributes — DWM uses per-pixel alpha from
    // our D3D11 content (shader outputs alpha=1.0 = fully opaque).
    // This ensures WDA_EXCLUDEFROMCAPTURE works (requires WS_EX_LAYERED).
    SetWindowDisplayAffinity(g_hwnd, WDA_EXCLUDEFROMCAPTURE);

    // Init D3D11
    Log("InitD3D11...");
    if (!InitD3D11(g_hwnd, w, h)) { Log("InitD3D11 FAILED"); return 2; }
    Log("InitD3D11 OK");
    if (!CompileShaders()) { Log("CompileShaders FAILED"); return 3; }
    Log("CompileShaders OK");
    InitQuad();
    if (!InitDuplication(screenIdx)) {
        Log("InitDuplication FAILED — capture will not work");
    } else {
        Log("InitDuplication OK");
    }

    ShowWindow(g_hwnd, SW_SHOW);
    UpdateWindow(g_hwnd);
    Log("Window shown, entering main loop");

    // Main loop
    MSG msg;
    int lastMode = -1;
    while (true) {
        // Pump messages
        while (PeekMessage(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) goto exit;
            TranslateMessage(&msg);
            DispatchMessage(&msg);
        }

        int mode = ReadMode();
        if (mode != lastMode) {
            lastMode = mode;
            if (mode == 0) ShowWindow(g_hwnd, SW_HIDE);
            else           ShowWindow(g_hwnd, SW_SHOW);
        }

        if (mode > 0 && g_dup) {
            static bool first_frame = true;
            if (first_frame) { Log("First frame: capturing desktop..."); first_frame = false; }
            if (CaptureDesktop()) {
                static bool first_render = true;
                if (first_render) { Log("First render: drawing grayscale quad"); first_render = false; }
                Render(mode);
            } else {
                g_sc->Present(1, 0);
            }
        } else {
            Sleep(16);
        }
    }

exit:
    SafeRelease(g_dupSRV);
    SafeRelease(g_dupTex);
    SafeRelease(g_dup);
    SafeRelease(g_smp);
    SafeRelease(g_vb);
    SafeRelease(g_il);
    SafeRelease(g_vs);
    SafeRelease(g_psOKLCh);
    SafeRelease(g_psLuma);
    SafeRelease(g_sc);
    SafeRelease(g_ctx);
    SafeRelease(g_dev);
    if (g_hwnd) DestroyWindow(g_hwnd);
    return 0;
}
