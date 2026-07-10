// swapchain_overlay.exe — D3D11 OKLCh via swap chain + hide-before-capture
//
// Uses a regular (non-layered) WS_POPUP window with a D3D11 swap chain.
// Hides the window during desktop capture to avoid feedback loop.
// At 60+ fps the hide/show flicker is imperceptible.
//
// Control: C:\Windows\Temp\swapchain_overlay_mode.txt (0/1/2)
// Build: run build.bat

#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <cstdio>
#include <atomic>

#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"d3dcompiler.lib")
#pragma comment(lib,"dxgi.lib")
#pragma comment(lib,"user32.lib")
#pragma comment(lib,"dxguid.lib")

#define CTRL "C:\\Windows\\Temp\\swapchain_overlay_mode.txt"
#define LOGF "C:\\Windows\\Temp\\swapchain_overlay.log"

static void Log(const char*m){FILE*f=fopen(LOGF,"a");if(f){fprintf(f,"%s\n",m);fclose(f);}}
int Mode(){FILE*f=fopen(CTRL,"r");if(!f)return 0;char b[8]={0};fgets(b,8,f);fclose(f);return b[0]>='0'&&b[0]<='2'?b[0]-'0':0;}

const char*SHADERS=R"(
struct VI{float2 p:POSITION;float2 t:TEXCOORD0;};
struct VO{float4 p:SV_POSITION;float2 t:TEXCOORD0;};
Texture2D tx:register(t0);SamplerState sm:register(s0);
float3 s2l(float3 c){float3 l=c/12.92,h=pow((c+0.055)/1.055,float3(2.4,2.4,2.4));return lerp(l,h,step(0.04045,c));}
float l2s(float c){return c<=0.0031308?12.92*c:1.055*pow(c,1.0/2.4)-0.055;}
VO VS(VI v){VO o;o.p=float4(v.p,0,1);o.t=v.t;return o;}
float4 PS_OKLCh(VO i):SV_TARGET{
    float3 c=tx.Sample(sm,i.t).rgb;float3 l=s2l(c);
    float L=0.2104542553f*pow(abs(0.4122214708f*l.r+0.5363325363f*l.g+0.0514459929f*l.b),1.0f/3.0f)
           +0.7936177850f*pow(abs(0.2119034982f*l.r+0.6806995451f*l.g+0.1073969566f*l.b),1.0f/3.0f)
           -0.0040720468f*pow(abs(0.0883024619f*l.r+0.2817188376f*l.g+0.6299787005f*l.b),1.0f/3.0f);
    float g=l2s(clamp(L*L*L,0.0,1.0));
    return float4(g,g,g,1.0);
}
float4 PS_Luma(VO i):SV_TARGET{float c=dot(tx.Sample(sm,i.t).rgb,float3(0.2126,0.7152,0.0722));return float4(c,c,c,1.0);}
)";

ID3D11Device*d=0;ID3D11DeviceContext*c=0;IDXGISwapChain*sc=0;
ID3D11VertexShader*vs=0;ID3D11PixelShader*psO=0,*psL=0;
ID3D11InputLayout*il=0;ID3D11SamplerState*sm=0;ID3D11Buffer*vb=0;
IDXGIOutputDuplication*dup=0;ID3D11ShaderResourceView*srv=0;ID3D11Texture2D*dupTex=0;
HWND hw=0;int ww=1920,wh=1080,dupW=0,dupH=0;

void SR(IUnknown*p){if(p)p->Release();}

bool Init(HWND h,int ww_,int wh_){
    DXGI_SWAP_CHAIN_DESC s={};s.BufferCount=2;s.BufferDesc.Width=ww_;s.BufferDesc.Height=wh_;
    s.BufferDesc.Format=DXGI_FORMAT_B8G8R8A8_UNORM;s.BufferDesc.RefreshRate.Numerator=60;s.BufferDesc.RefreshRate.Denominator=1;
    s.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;s.OutputWindow=h;s.SampleDesc.Count=1;s.Windowed=TRUE;
    s.SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD;
    D3D_FEATURE_LEVEL fl=D3D_FEATURE_LEVEL_11_0;
    return SUCCEEDED(D3D11CreateDeviceAndSwapChain(0,D3D_DRIVER_TYPE_HARDWARE,0,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        &fl,1,D3D11_SDK_VERSION,&s,&sc,&d,0,&c));
}

bool Dup(int idx){
    IDXGIDevice*dx=0;d->QueryInterface(__uuidof(IDXGIDevice),(void**)&dx);
    IDXGIAdapter*a=0;dx->GetAdapter(&a);dx->Release();
    IDXGIOutput*o=0;a->EnumOutputs(idx,&o);a->Release();
    IDXGIOutput1*o1=0;o->QueryInterface(__uuidof(IDXGIOutput1),(void**)&o1);o->Release();
    HRESULT hr=o1->DuplicateOutput(d,&dup);o1->Release();return SUCCEEDED(hr);
}

bool CS(){
    ID3DBlob*b=0,*e=0;
    if(FAILED(D3DCompile(SHADERS,strlen(SHADERS),0,0,0,"VS","vs_5_0",0,0,&b,&e)))return false;
    d->CreateVertexShader(b->GetBufferPointer(),b->GetBufferSize(),0,&vs);
    D3D11_INPUT_ELEMENT_DESC l[]={{"POSITION",0,DXGI_FORMAT_R32G32_FLOAT,0,0,D3D11_INPUT_PER_VERTEX_DATA,0},
        {"TEXCOORD",0,DXGI_FORMAT_R32G32_FLOAT,0,8,D3D11_INPUT_PER_VERTEX_DATA,0}};
    d->CreateInputLayout(l,2,b->GetBufferPointer(),b->GetBufferSize(),&il);b->Release();
    if(SUCCEEDED(D3DCompile(SHADERS,strlen(SHADERS),0,0,0,"PS_OKLCh","ps_5_0",0,0,&b,&e)))
        {d->CreatePixelShader(b->GetBufferPointer(),b->GetBufferSize(),0,&psO);b->Release();}
    if(SUCCEEDED(D3DCompile(SHADERS,strlen(SHADERS),0,0,0,"PS_Luma","ps_5_0",0,0,&b,&e)))
        {d->CreatePixelShader(b->GetBufferPointer(),b->GetBufferSize(),0,&psL);b->Release();}
    return vs&&psO&&psL;
}

void IQ(){
    float v[16]={-1,-1,0,1,1,-1,1,1,1,1,1,0,-1,1,0,0};
    D3D11_BUFFER_DESC bd={sizeof(v),D3D11_USAGE_IMMUTABLE,D3D11_BIND_VERTEX_BUFFER};
    D3D11_SUBRESOURCE_DATA sd={v};d->CreateBuffer(&bd,&sd,&vb);
    D3D11_SAMPLER_DESC sd2={};sd2.Filter=D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd2.AddressU=sd2.AddressV=sd2.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;
    d->CreateSamplerState(&sd2,&sm);
}

bool Cap(){
    IDXGIResource*r=0;DXGI_OUTDUPL_FRAME_INFO fi;
    if(FAILED(dup->AcquireNextFrame(0,&fi,&r)))return false;
    ID3D11Texture2D*t=0;r->QueryInterface(__uuidof(ID3D11Texture2D),(void**)&t);r->Release();
    if(!t){dup->ReleaseFrame();return false;}
    D3D11_TEXTURE2D_DESC desc;t->GetDesc(&desc);
    if(desc.Width!=(UINT)dupW||desc.Height!=(UINT)dupH){
        SR(srv);SR(dupTex);
        dupTex=t;dupTex->AddRef();d->CreateShaderResourceView(t,0,&srv);
        dupW=desc.Width;dupH=desc.Height;
    }else if(dupTex){c->CopyResource(dupTex,t);}
    t->Release();dup->ReleaseFrame();return srv!=0;
}

void Render(int mode){
    ID3D11RenderTargetView*rtv=0;ID3D11Texture2D*bb=0;
    sc->GetBuffer(0,__uuidof(ID3D11Texture2D),(void**)&bb);
    if(!bb)return;d->CreateRenderTargetView(bb,0,&rtv);bb->Release();
    if(!rtv)return;
    float clr[4]={0,0,0,1};c->ClearRenderTargetView(rtv,clr);
    c->OMSetRenderTargets(1,&rtv,0);
    D3D11_VIEWPORT vp={0,0,(float)ww,(float)wh,0,1};c->RSSetViewports(1,&vp);
    ID3D11PixelShader*ps=(mode==2)?psL:psO;if(!ps){rtv->Release();return;}
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);c->IASetInputLayout(il);
    UINT st=16,off=0;c->IASetVertexBuffers(0,1,&vb,&st,&off);
    c->VSSetShader(vs,0,0);c->PSSetShader(ps,0,0);
    c->PSSetShaderResources(0,1,&srv);c->PSSetSamplers(0,1,&sm);
    c->Draw(4,0);ID3D11ShaderResourceView*n=0;c->PSSetShaderResources(0,1,&n);
    rtv->Release();sc->Present(1,0);
}

LRESULT CALLBACK WP(HWND h,UINT m,WPARAM w,LPARAM l){return m==WM_DESTROY?(PostQuitMessage(0),0):DefWindowProc(h,m,w,l);}

int WINAPI WinMain(HINSTANCE hi,HINSTANCE,LPSTR,int){
    int sx=0,x=0,y=0;{IDXGIFactory1*f=0;CreateDXGIFactory1(__uuidof(IDXGIFactory1),(void**)&f);
    IDXGIAdapter*a=0;f->EnumAdapters(0,&a);IDXGIOutput*o=0;a->EnumOutputs(sx,&o);
    DXGI_OUTPUT_DESC dd;o->GetDesc(&dd);x=dd.DesktopCoordinates.left;y=dd.DesktopCoordinates.top;
    ww=dd.DesktopCoordinates.right-dd.DesktopCoordinates.left;
    wh=dd.DesktopCoordinates.bottom-dd.DesktopCoordinates.top;o->Release();a->Release();f->Release();}

    WNDCLASSEX wc={sizeof(wc),CS_HREDRAW|CS_VREDRAW,WP,0,0,hi,0,0,0,0,"SCOverlay",0};RegisterClassEx(&wc);
    hw=CreateWindowEx(WS_EX_TOPMOST|WS_EX_NOACTIVATE|WS_EX_TRANSPARENT,"SCOverlay","SC",WS_POPUP,x,y,ww,wh,0,0,hi,0);
    if(!hw)return 1;

    Log("Init...");
    if(!Init(hw,ww,wh)){Log("Init FAILED");return 2;}Log("Init OK");
    if(!CS()){Log("Shaders FAILED");return 3;}Log("Shaders OK");
    IQ();
    // Retry duplication — may fail if another DDA client hasn't released yet
    for(int retry=0;retry<5;retry++){
        if(Dup(sx)){Log("Dup OK");break;}
        if(retry<4){Log("Dup retry...");Sleep(1000);}
        else Log("Dup FAILED");
    }
    ShowWindow(hw,SW_SHOW);UpdateWindow(hw);Log("Running");

    MSG msg;int lm=-1;
    while(true){
        while(PeekMessage(&msg,0,0,0,PM_REMOVE)){if(msg.message==WM_QUIT)goto exit;TranslateMessage(&msg);DispatchMessage(&msg);}
        int m=Mode();
        if(m!=lm){lm=m;if(m==0){ShowWindow(hw,SW_HIDE);Sleep(100);}else ShowWindow(hw,SW_SHOW);}
        if(m&&dup){
            if(Cap())Render(m);else sc->Present(1,0);
        }else Sleep(16);
    }
exit:
    SR(srv);SR(dupTex);SR(dup);SR(sm);SR(vb);SR(il);SR(vs);SR(psO);SR(psL);SR(sc);SR(c);SR(d);
    if(hw)DestroyWindow(hw);return 0;
}
