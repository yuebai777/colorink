// dcomp_overlay.exe — D3D11 OKLCh grayscale via DirectComposition
//
// DirectComposition is the official Windows API for GPU overlays.
// We render the grayscale frame to a D3D11 texture, then set it as
// the content of a DirectComposition visual.  No WS_EX_LAYERED
// issues, no swap chain incompatibility.
//
// Control file: C:\Windows\Temp\dcomp_overlay_mode.txt
//   0=disabled, 1=OKLCh, 2=Luma
//
// Build: run build.bat

#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <dcomp.h>
#include <cstdio>
#include <atomic>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dcomp.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "dxguid.lib")

#define CTRL_FILE "C:\\Windows\\Temp\\dcomp_overlay_mode.txt"
#define LOG_FILE  "C:\\Windows\\Temp\\dcomp_overlay.log"
#define WDA_EXCLUDEFROMCAPTURE 0x00000011

static void Log(const char* msg) {
    FILE* f = fopen(LOG_FILE, "a");
    if (f) { fprintf(f, "%s\n", msg); fclose(f); }
}

int ReadMode() {
    FILE* f = fopen(CTRL_FILE, "r");
    if (!f) return 0;
    char buf[8]={0}; fgets(buf,sizeof(buf),f); fclose(f);
    return (buf[0]>='0'&&buf[0]<='2') ? (buf[0]-'0') : 0;
}

// --- Shaders ---
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
IDXGIOutputDuplication* g_dup=0;
ID3D11Texture2D*        g_renderTex=0;   // Keep for compatibility, unused
ID3D11RenderTargetView* g_rtv=0;
ID3D11ShaderResourceView* g_dupSRV=0;     // desktop capture
ID3D11Texture2D*        g_dupTex=0;
int g_screenW=1920,g_screenH=1080;
int g_dupW=0,g_dupH=0;
IDCompositionDevice* g_dcomp=0;
IDCompositionTarget* g_dcompTarget=0;
IDCompositionVisual* g_dcompVis=0;
HWND g_hwnd=0;

void SafeRelease(IUnknown*p){if(p)p->Release();}

bool InitD3D11(HWND hwnd){
    D3D_FEATURE_LEVEL fl=D3D_FEATURE_LEVEL_11_0;
    UINT flags=D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    HRESULT hr = D3D11CreateDevice(0,D3D_DRIVER_TYPE_HARDWARE,0,flags,&fl,1,
        D3D11_SDK_VERSION,&g_dev,0,&g_ctx);
    if(FAILED(hr))return false;
    
    IDXGIDevice1* dxgiDev1 = nullptr;
    g_dev->QueryInterface(__uuidof(IDXGIDevice1), (void**)&dxgiDev1);
    if(dxgiDev1){
        dxgiDev1->SetMaximumFrameLatency(1);
        dxgiDev1->Release();
    }
    return true;
}

bool InitDuplication(int idx){
    IDXGIDevice* dx=0;g_dev->QueryInterface(__uuidof(IDXGIDevice),(void**)&dx);
    IDXGIAdapter* ad=0;dx->GetAdapter(&ad);dx->Release();
    IDXGIOutput* out=0;if(FAILED(ad->EnumOutputs(idx,&out))){ad->Release();return false;}
    ad->Release();
    IDXGIOutput1* o1=0;out->QueryInterface(__uuidof(IDXGIOutput1),(void**)&o1);out->Release();
    HRESULT hr=o1->DuplicateOutput(g_dev,&g_dup);o1->Release();
    return SUCCEEDED(hr);
}

bool CompileShaders(){
    ID3DBlob*b=0,*e=0;
    if(FAILED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"VS","vs_5_0",0,0,&b,&e)))return false;
    g_dev->CreateVertexShader(b->GetBufferPointer(),b->GetBufferSize(),0,&g_vs);
    D3D11_INPUT_ELEMENT_DESC l[]={{"POSITION",0,DXGI_FORMAT_R32G32_FLOAT,0,0,D3D11_INPUT_PER_VERTEX_DATA,0},
        {"TEXCOORD",0,DXGI_FORMAT_R32G32_FLOAT,0,8,D3D11_INPUT_PER_VERTEX_DATA,0}};
    g_dev->CreateInputLayout(l,2,b->GetBufferPointer(),b->GetBufferSize(),&g_il);b->Release();
    if(SUCCEEDED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"PS_OKLCh","ps_5_0",0,0,&b,&e)))
        {g_dev->CreatePixelShader(b->GetBufferPointer(),b->GetBufferSize(),0,&g_psOKLCh);b->Release();}
    if(SUCCEEDED(D3DCompile(SHADER_SRC,strlen(SHADER_SRC),0,0,0,"PS_Luma","ps_5_0",0,0,&b,&e)))
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
    D3D11_SAMPLER_DESC sd2={};sd2.Filter=D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd2.AddressU=sd2.AddressV=sd2.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;
    g_dev->CreateSamplerState(&sd2,&g_smp);
}

bool InitRenderTexture(int w,int h){
    SafeRelease(g_rtv);
    SafeRelease(g_sc);

    IDXGIDevice* dxgiDev = 0;
    g_dev->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDev);
    if (!dxgiDev) return false;
    IDXGIAdapter* dxgiAdapter = 0;
    dxgiDev->GetAdapter(&dxgiAdapter);
    dxgiDev->Release();
    if (!dxgiAdapter) return false;
    IDXGIFactory2* dxgiFactory = 0;
    dxgiAdapter->GetParent(__uuidof(IDXGIFactory2), (void**)&dxgiFactory);
    dxgiAdapter->Release();
    if (!dxgiFactory) return false;

    DXGI_SWAP_CHAIN_DESC1 sd = {};
    sd.Width = w;
    sd.Height = h;
    sd.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 2;
    sd.Scaling = DXGI_SCALING_STRETCH;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    sd.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;

    HRESULT hr = dxgiFactory->CreateSwapChainForComposition(g_dev, &sd, nullptr, &g_sc);
    dxgiFactory->Release();
    if (FAILED(hr)) {
        char buf[64]; sprintf(buf, "CreateCompositionSwapChain hr=0x%08X", (unsigned)hr); Log(buf);
        return false;
    }

    ID3D11Texture2D* pBackBuffer = nullptr;
    hr = g_sc->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&pBackBuffer);
    if (FAILED(hr)) return false;
    hr = g_dev->CreateRenderTargetView(pBackBuffer, nullptr, &g_rtv);
    pBackBuffer->Release();
    if (FAILED(hr)) return false;

    if (g_dcompVis && g_sc) {
        g_dcompVis->SetContent(g_sc);
        g_dcomp->Commit();
    }
    return true;
}

bool InitDirectComposition(HWND hwnd){
    IDXGIDevice* dxgiDev=0;
    g_dev->QueryInterface(__uuidof(IDXGIDevice),(void**)&dxgiDev);
    HRESULT hr=DCompositionCreateDevice(dxgiDev,__uuidof(IDCompositionDevice),(void**)&g_dcomp);
    if(dxgiDev)dxgiDev->Release();
    if(FAILED(hr)){
        char buf[64];sprintf(buf,"DCompositionCreateDevice hr=0x%08X",(unsigned)hr);Log(buf);
        return false;
    }
    Log("DComp device OK");
    hr=g_dcomp->CreateTargetForHwnd(hwnd,TRUE,&g_dcompTarget);
    if(FAILED(hr)){char buf[64];sprintf(buf,"CreateTarget hr=0x%08X",(unsigned)hr);Log(buf);return false;}
    Log("DComp target OK");
    hr=g_dcomp->CreateVisual(&g_dcompVis);
    if(FAILED(hr)){char buf[64];sprintf(buf,"CreateVisual hr=0x%08X",(unsigned)hr);Log(buf);return false;}
    Log("DComp visual OK");
    hr=g_dcompVis->SetContent(g_sc);
    if(FAILED(hr)){char buf[64];sprintf(buf,"SetContent hr=0x%08X",(unsigned)hr);Log(buf);return false;}
    Log("DComp content OK");
    hr=g_dcompTarget->SetRoot(g_dcompVis);
    if(FAILED(hr)){char buf[64];sprintf(buf,"SetRoot hr=0x%08X",(unsigned)hr);Log(buf);return false;}
    Log("DComp root OK");
    g_dcomp->Commit();
    return true;
}

bool CaptureDesktop(){
    IDXGIResource*res=0;DXGI_OUTDUPL_FRAME_INFO fi;
    if(FAILED(g_dup->AcquireNextFrame(0,&fi,&res)))return false;
    ID3D11Texture2D*tex=0;res->QueryInterface(__uuidof(ID3D11Texture2D),(void**)&tex);res->Release();
    if(!tex){g_dup->ReleaseFrame();return false;}
    D3D11_TEXTURE2D_DESC desc;tex->GetDesc(&desc);
    
    if(g_dupTex == nullptr || desc.Width != (UINT)g_dupW || desc.Height != (UINT)g_dupH){
        SafeRelease(g_dupSRV);
        SafeRelease(g_dupTex);
        
        D3D11_TEXTURE2D_DESC copyDesc = desc;
        copyDesc.Usage = D3D11_USAGE_DEFAULT;
        copyDesc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        copyDesc.CPUAccessFlags = 0;
        copyDesc.MiscFlags = 0;
        
        HRESULT hr = g_dev->CreateTexture2D(&copyDesc, nullptr, &g_dupTex);
        if (FAILED(hr)) {
            tex->Release(); g_dup->ReleaseFrame();
            return false;
        }
        hr = g_dev->CreateShaderResourceView(g_dupTex, nullptr, &g_dupSRV);
        if (FAILED(hr)) {
            SafeRelease(g_dupTex);
            tex->Release(); g_dup->ReleaseFrame();
            return false;
        }
        
        g_dupW = desc.Width;
        g_dupH = desc.Height;
        
        if (g_screenW != (int)desc.Width || g_screenH != (int)desc.Height) {
            g_screenW = desc.Width; g_screenH = desc.Height;
            InitRenderTexture(g_screenW, g_screenH);
        }
    }
    
    if (g_dupTex) {
        g_ctx->CopyResource(g_dupTex, tex);
    }
    
    tex->Release();
    g_dup->ReleaseFrame();
    return g_dupSRV != nullptr;
}

void Render(int mode){
    ID3D11PixelShader*ps=(mode==2)?g_psLuma:g_psOKLCh;
    if(!ps||!g_rtv)return;
    float clear[4]={0,0,0,1};
    g_ctx->ClearRenderTargetView(g_rtv,clear);
    g_ctx->OMSetRenderTargets(1,&g_rtv,0);
    D3D11_VIEWPORT vp={0,0,(float)g_screenW,(float)g_screenH,0,1};
    g_ctx->RSSetViewports(1,&vp);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->IASetInputLayout(g_il);
    UINT str=16,off=0;
    g_ctx->IASetVertexBuffers(0,1,&g_vb,&str,&off);
    g_ctx->VSSetShader(g_vs,0,0);
    g_ctx->PSSetShader(ps,0,0);
    g_ctx->PSSetShaderResources(0,1,&g_dupSRV);
    g_ctx->PSSetSamplers(0,1,&g_smp);
    g_ctx->Draw(4,0);
    ID3D11ShaderResourceView*n=0;
    g_ctx->PSSetShaderResources(0,1,&n);
    if (g_sc) g_sc->Present(1,0);
    // Commit the DirectComposition visual tree
    g_dcomp->Commit();
}

LRESULT CALLBACK WndProc(HWND h,UINT m,WPARAM w,LPARAM l){
    if(m==WM_DESTROY){PostQuitMessage(0);return 0;}
    return DefWindowProc(h,m,w,l);
}

int WINAPI WinMain(HINSTANCE hInst,HINSTANCE,LPSTR,int){
    int screenIdx=0;
    // Get screen geometry via DXGI
    int x=0,y=0,w=1920,h=1080;
    {IDXGIFactory1*f=0;CreateDXGIFactory1(__uuidof(IDXGIFactory1),(void**)&f);
    IDXGIAdapter*a=0;f->EnumAdapters(0,&a);
    IDXGIOutput*o=0;a->EnumOutputs(screenIdx,&o);
    DXGI_OUTPUT_DESC d;o->GetDesc(&d);
    x=d.DesktopCoordinates.left;y=d.DesktopCoordinates.top;
    w=d.DesktopCoordinates.right-d.DesktopCoordinates.left;
    h=d.DesktopCoordinates.bottom-d.DesktopCoordinates.top;
    o->Release();a->Release();f->Release();}
    g_screenW=w;g_screenH=h;

    // Create tiny invisible window as composition target
    WNDCLASSEX wc={sizeof(wc),CS_HREDRAW|CS_VREDRAW,WndProc,0,0,hInst,0,0,0,0,"DCompOverlay",0};
    RegisterClassEx(&wc);
    g_hwnd=CreateWindowEx(WS_EX_TRANSPARENT|WS_EX_TOPMOST|WS_EX_NOACTIVATE|WS_EX_LAYERED,
        "DCompOverlay","DC",WS_POPUP,x,y,w,h,0,0,hInst,0);
    if(!g_hwnd)return 1;
    // WDA_EXCLUDEFROMCAPTURE on layered window — works reliably
    SetWindowDisplayAffinity(g_hwnd,WDA_EXCLUDEFROMCAPTURE);
    // Make window fully transparent — DirectComposition content handles visuals
    SetLayeredWindowAttributes(g_hwnd,0,255,LWA_ALPHA);

    Log("InitD3D11...");
    if(!InitD3D11(g_hwnd)){Log("FAILED");return 2;}
    Log("D3D11 OK");
    if(!CompileShaders()){Log("Shaders FAILED");return 3;}
    Log("Shaders OK");
    InitQuad();
    if(!InitRenderTexture(w,h)){Log("RenderTex FAILED");return 4;}
    Log("RenderTex OK");
    if(!InitDuplication(screenIdx)){Log("Duplication FAILED");}
    else Log("Duplication OK");
    if(!InitDirectComposition(g_hwnd)){Log("DComp FAILED");return 5;}
    Log("DComp OK");

    ShowWindow(g_hwnd,SW_SHOW);
    UpdateWindow(g_hwnd);
    Log("Running");

    MSG msg;int lastMode=-1;
    while(true){
        while(PeekMessage(&msg,0,0,0,PM_REMOVE)){
            if(msg.message==WM_QUIT)goto exit;
            TranslateMessage(&msg);DispatchMessage(&msg);
        }
        int mode=ReadMode();
        if(mode!=lastMode){lastMode=mode;
            ShowWindow(g_hwnd,mode==0?SW_HIDE:SW_SHOW);}
        if(mode>0&&g_dup){
            if(CaptureDesktop())Render(mode);
            else{Sleep(1);}
        }else{Sleep(16);}
    }
exit:
    SafeRelease(g_dcompVis);SafeRelease(g_dcompTarget);SafeRelease(g_dcomp);
    SafeRelease(g_rtv);SafeRelease(g_renderTex);SafeRelease(g_sc);
    SafeRelease(g_dupSRV);SafeRelease(g_dupTex);SafeRelease(g_dup);
    SafeRelease(g_smp);SafeRelease(g_vb);SafeRelease(g_il);
    SafeRelease(g_vs);SafeRelease(g_psOKLCh);SafeRelease(g_psLuma);
    SafeRelease(g_ctx);SafeRelease(g_dev);
    if(g_hwnd)DestroyWindow(g_hwnd);
    return 0;
}
