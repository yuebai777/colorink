// DComp Zero-Copy Grayscale Filter Engine
// High-performance, zero-latency desktop grayscale filter via D3D11, DXGI Desktop Duplication, and DirectComposition.
// Features a decoupled Producer-Consumer architecture:
// - Producer: Dedicated Capture Thread per screen pulling frames via DXGI Desktop Duplication and copying into GPU ping-pong texture in VRAM (~0.05ms)
// - Consumer: Dedicated Render Thread that owns the HWNDs, applies OKLCh / Luma HLSL shaders, pumps window messages, and presents via DirectComposition SwapChain
// - Input Transparency: Window procedure returns HTTRANSPARENT, and extended styles exclude the overlay from capture (WDA_EXCLUDEFROMCAPTURE)

#![allow(non_snake_case)]

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use windows::core::{s, Interface};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Direct3D::*;
use windows::Win32::Graphics::Direct3D11::*;
use windows::Win32::Graphics::Direct3D::Fxc::*;
use windows::Win32::Graphics::DirectComposition::*;
use windows::Win32::Graphics::Dxgi::Common::*;
use windows::Win32::Graphics::Dxgi::*;
use windows::Win32::Graphics::Gdi::{BeginPaint, EndPaint, PAINTSTRUCT};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::*;

pub const WDA_EXCLUDEFROMCAPTURE: u32 = 0x00000011;

#[repr(C)]
struct PROCESS_POWER_THROTTLING_STATE {
    Version: u32,
    ControlMask: u32,
    StateMask: u32,
}

extern "system" {
    fn OpenInputDesktop(dwflags: u32, finherit: i32, dwdesiredaccess: u32) -> isize;
    fn SetThreadDesktop(hdesktop: isize) -> i32;
    fn GetCurrentProcess() -> isize;
    fn GetCurrentThread() -> isize;
    fn SetProcessInformation(
        hprocess: isize,
        processinformationclass: i32,
        processinformation: *const c_void,
        processinformationsize: u32,
    ) -> i32;
    fn SetThreadPriority(hthread: isize, npriority: i32) -> i32;
    fn timeBeginPeriod(uperiod: u32) -> u32;
    fn timeEndPeriod(uperiod: u32) -> u32;
}

const SHADER_SRC: &str = r#"
struct VS_IN {
    float2 pos : POSITION;
    float2 uv  : TEXCOORD0;
};
struct VS_OUT {
    float4 pos : SV_POSITION;
    float2 uv  : TEXCOORD0;
};

Texture2D screenTex : register(t0);
SamplerState smp : register(s0);

VS_OUT VS(VS_IN v) {
    VS_OUT o;
    o.pos = float4(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    return o;
}

float3 srgb_to_linear(float3 c) {
    float3 lo = c / 12.92;
    float3 hi = pow((c + 0.055) / 1.055, float3(2.4, 2.4, 2.4));
    return lerp(lo, hi, step(0.04045, c));
}

float linear_to_srgb(float c) {
    if (c <= 0.0031308) return 12.92 * c;
    return 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

float4 PS_OKLCh(VS_OUT input) : SV_TARGET {
    float3 col = screenTex.Sample(smp, input.uv).rgb;
    float3 lin = srgb_to_linear(col);
    float3 lms = float3(
        dot(lin, float3(0.4122214708, 0.5363325363, 0.0514459929)),
        dot(lin, float3(0.2119034982, 0.6806995451, 0.1073969566)),
        dot(lin, float3(0.0883024619, 0.2817188376, 0.6299787005))
    );
    lms = sign(lms) * pow(abs(lms), 1.0 / 3.0);
    float L = dot(lms, float3(0.2104542553, 0.7936177850, -0.0040720468));
    float lin_gray = clamp(L * L * L, 0.0, 1.0);
    float gray = linear_to_srgb(lin_gray);
    return float4(gray, gray, gray, 1.0);
}

float4 PS_Luma(VS_OUT input) : SV_TARGET {
    float3 col = screenTex.Sample(smp, input.uv).rgb;
    float gray = dot(col, float3(0.2126, 0.7152, 0.0722));
    return float4(gray, gray, gray, 1.0);
}
"#;

#[repr(C)]
struct Vertex {
    x: f32,
    y: f32,
    u: f32,
    v: f32,
}

pub struct ContextWrapper(ID3D11DeviceContext);
unsafe impl Send for ContextWrapper {}
unsafe impl Sync for ContextWrapper {}

#[derive(Clone)]
struct D3DResources {
    device: ID3D11Device,
    context: Arc<Mutex<ContextWrapper>>,
    vs: ID3D11VertexShader,
    ps_oklch: ID3D11PixelShader,
    ps_luma: ID3D11PixelShader,
    input_layout: ID3D11InputLayout,
    vbo: ID3D11Buffer,
    sampler: ID3D11SamplerState,
    dcomp_device: IDCompositionDevice,
}

unsafe impl Send for D3DResources {}
unsafe impl Sync for D3DResources {}

struct ScreenChannels {
    output_idx: usize,
    rect: RECT,
    hwnd: HWND,
    _dcomp_target: IDCompositionTarget,
    _dcomp_visual: IDCompositionVisual,
    swapchain: IDXGISwapChain1,
    rtv: ID3D11RenderTargetView,
    ping_pong_srv: ID3D11ShaderResourceView,
    has_new_frame: Arc<AtomicBool>,
    notify: Arc<Condvar>,
    lock: Arc<Mutex<()>>,
}

struct ActiveSession {
    running: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    mode: Arc<AtomicI32>,
    target_screen: Arc<AtomicI32>,
    cv: Arc<Condvar>,
    _lock: Arc<Mutex<()>>,
    render_handle: Option<JoinHandle<()>>,
}

static RESOURCES: Mutex<Option<D3DResources>> = Mutex::new(None);
static SESSION: Mutex<Option<ActiveSession>> = Mutex::new(None);
static IS_HEALTHY: AtomicBool = AtomicBool::new(true);
static TARGET_SCREEN: AtomicI32 = AtomicI32::new(-1);
static FILTER_MODE: AtomicI32 = AtomicI32::new(0);
static FRAME_COUNT: AtomicU64 = AtomicU64::new(0);
static CNT_ACQ_OK: AtomicU64 = AtomicU64::new(0);
static CNT_ACQ_TIMEOUT: AtomicU64 = AtomicU64::new(0);
static CNT_RES_SOME: AtomicU64 = AtomicU64::new(0);
static CNT_RES_NONE: AtomicU64 = AtomicU64::new(0);
static CNT_PRESENT_OK: AtomicU64 = AtomicU64::new(0);
static CNT_PRESENT_ERR: AtomicU64 = AtomicU64::new(0);

unsafe extern "system" fn overlay_wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match msg {
        WM_NCHITTEST => LRESULT(-1), // HTTRANSPARENT: completely click-through to underlying windows/apps
        WM_ERASEBKGND => LRESULT(1),
        WM_SETCURSOR => LRESULT(0),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let _ = BeginPaint(hwnd, &mut ps);
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

pub fn create_screen_overlay_window(x: i32, y: i32, w: i32, h: i32) -> windows::core::Result<HWND> {
    unsafe {
        let instance = GetModuleHandleW(None)?;
        let class_name = windows::core::w!("ColorinkDCompOverlayClass");
        let cursor = LoadCursorW(None, IDC_ARROW).unwrap_or_default();

        let wc = WNDCLASSW {
            lpfnWndProc: Some(overlay_wnd_proc),
            hInstance: instance.into(),
            lpszClassName: class_name,
            hCursor: cursor,
            ..Default::default()
        };
        let _ = RegisterClassW(&wc);

        let ex_style = WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_TRANSPARENT
            | WS_EX_LAYERED
            | WS_EX_NOACTIVATE
            | WS_EX_NOREDIRECTIONBITMAP;

        let hwnd = CreateWindowExW(
            ex_style,
            class_name,
            windows::core::w!("ColorinkDCompOverlay"),
            WS_POPUP,
            x,
            y,
            w,
            h,
            None,
            None,
            instance,
            None,
        )?;

        let _ = SetLayeredWindowAttributes(hwnd, windows::Win32::Foundation::COLORREF(0), 255, LWA_ALPHA);
        let _ = SetWindowDisplayAffinity(hwnd, WINDOW_DISPLAY_AFFINITY(WDA_EXCLUDEFROMCAPTURE));
        let _ = ShowWindow(hwnd, SW_HIDE);

        Ok(hwnd)
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_init() -> bool {
    let mut guard = RESOURCES.lock().unwrap();
    if guard.is_some() {
        return true;
    }

    match init_resources_internal() {
        Ok(res) => {
            *guard = Some(res);
            IS_HEALTHY.store(true, Ordering::SeqCst);
            true
        }
        Err(_) => {
            IS_HEALTHY.store(false, Ordering::SeqCst);
            false
        }
    }
}

fn init_resources_internal() -> windows::core::Result<D3DResources> {
    unsafe {
        let factory: IDXGIFactory1 = CreateDXGIFactory1()?;
        let mut chosen_adapter = None;
        let mut idx = 0;
        while let Ok(adapter) = factory.EnumAdapters1(idx) {
            if let Ok(desc) = adapter.GetDesc1() {
                let name = String::from_utf16_lossy(&desc.Description);
                let mut out_count = 0;
                while adapter.EnumOutputs(out_count).is_ok() {
                    out_count += 1;
                }
                println!("[DComp] Found adapter {}: {} (outputs: {})", idx, name.trim_matches(' '), out_count);
                if out_count > 0 && chosen_adapter.is_none() {
                    chosen_adapter = Some(adapter);
                }
            }
            idx += 1;
        }
        let adapter = match chosen_adapter {
            Some(a) => a,
            None => factory.EnumAdapters1(0)?,
        };
        if let Ok(desc) = adapter.GetDesc1() {
            let name = String::from_utf16_lossy(&desc.Description);
            println!("[DComp] Selected adapter: {}", name.trim_matches(' '));
        }

        let feature_levels = [D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0];
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;

        D3D11CreateDevice(
            &adapter,
            D3D_DRIVER_TYPE_UNKNOWN,
            None,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )?;

        let device = device.unwrap();
        let raw_context = context.unwrap();

        if let Ok(mt) = raw_context.cast::<ID3D11Multithread>() {
            unsafe {
                let _ = mt.SetMultithreadProtected(true);
            }
            println!("[DComp] Multithread protection enabled on D3D11 device context.");
        }

        let context = Arc::new(Mutex::new(ContextWrapper(raw_context)));

        // Compile shaders
        let vs_blob = compile_shader(SHADER_SRC, "VS", "vs_5_0")?;
        let mut vs = None;
        device.CreateVertexShader(
            std::slice::from_raw_parts(vs_blob.GetBufferPointer() as *const u8, vs_blob.GetBufferSize()),
            None,
            Some(&mut vs),
        )?;
        let vs = vs.unwrap();

        let ps_oklch_blob = compile_shader(SHADER_SRC, "PS_OKLCh", "ps_5_0")?;
        let mut ps_oklch = None;
        device.CreatePixelShader(
            std::slice::from_raw_parts(ps_oklch_blob.GetBufferPointer() as *const u8, ps_oklch_blob.GetBufferSize()),
            None,
            Some(&mut ps_oklch),
        )?;
        let ps_oklch = ps_oklch.unwrap();

        let ps_luma_blob = compile_shader(SHADER_SRC, "PS_Luma", "ps_5_0")?;
        let mut ps_luma = None;
        device.CreatePixelShader(
            std::slice::from_raw_parts(ps_luma_blob.GetBufferPointer() as *const u8, ps_luma_blob.GetBufferSize()),
            None,
            Some(&mut ps_luma),
        )?;
        let ps_luma = ps_luma.unwrap();

        // Input layout
        let layout_desc = [
            D3D11_INPUT_ELEMENT_DESC {
                SemanticName: s!("POSITION"),
                SemanticIndex: 0,
                Format: DXGI_FORMAT_R32G32_FLOAT,
                InputSlot: 0,
                AlignedByteOffset: 0,
                InputSlotClass: D3D11_INPUT_PER_VERTEX_DATA,
                InstanceDataStepRate: 0,
            },
            D3D11_INPUT_ELEMENT_DESC {
                SemanticName: s!("TEXCOORD"),
                SemanticIndex: 0,
                Format: DXGI_FORMAT_R32G32_FLOAT,
                InputSlot: 0,
                AlignedByteOffset: 8,
                InputSlotClass: D3D11_INPUT_PER_VERTEX_DATA,
                InstanceDataStepRate: 0,
            },
        ];
        let mut input_layout = None;
        device.CreateInputLayout(
            &layout_desc,
            std::slice::from_raw_parts(vs_blob.GetBufferPointer() as *const u8, vs_blob.GetBufferSize()),
            Some(&mut input_layout),
        )?;
        let input_layout = input_layout.unwrap();

        // Quad VBO
        let vertices = [
            Vertex { x: -1.0, y:  1.0, u: 0.0, v: 0.0 },
            Vertex { x:  1.0, y:  1.0, u: 1.0, v: 0.0 },
            Vertex { x: -1.0, y: -1.0, u: 0.0, v: 1.0 },
            Vertex { x:  1.0, y: -1.0, u: 1.0, v: 1.0 },
        ];
        let vbo_desc = D3D11_BUFFER_DESC {
            ByteWidth: (vertices.len() * std::mem::size_of::<Vertex>()) as u32,
            Usage: D3D11_USAGE_IMMUTABLE,
            BindFlags: D3D11_BIND_VERTEX_BUFFER.0 as u32,
            ..Default::default()
        };
        let init_data = D3D11_SUBRESOURCE_DATA {
            pSysMem: vertices.as_ptr() as *const c_void,
            SysMemPitch: 0,
            SysMemSlicePitch: 0,
        };
        let mut vbo = None;
        device.CreateBuffer(&vbo_desc, Some(&init_data), Some(&mut vbo))?;
        let vbo = vbo.unwrap();

        // Sampler
        let sampler_desc = D3D11_SAMPLER_DESC {
            Filter: D3D11_FILTER_MIN_MAG_MIP_LINEAR,
            AddressU: D3D11_TEXTURE_ADDRESS_CLAMP,
            AddressV: D3D11_TEXTURE_ADDRESS_CLAMP,
            AddressW: D3D11_TEXTURE_ADDRESS_CLAMP,
            ComparisonFunc: D3D11_COMPARISON_NEVER,
            MaxLOD: D3D11_FLOAT32_MAX,
            ..Default::default()
        };
        let mut sampler = None;
        device.CreateSamplerState(&sampler_desc, Some(&mut sampler))?;
        let sampler = sampler.unwrap();

        // DirectComposition Device
        let dxgi_device: IDXGIDevice = device.cast()?;
        let dcomp_device: IDCompositionDevice = DCompositionCreateDevice(&dxgi_device)?;

        Ok(D3DResources {
            device,
            context,
            vs,
            ps_oklch,
            ps_luma,
            input_layout,
            vbo,
            sampler,
            dcomp_device,
        })
    }
}

fn compile_shader(src: &str, entry: &str, target: &str) -> windows::core::Result<ID3DBlob> {
    unsafe {
        let mut blob = None;
        let mut error = None;
        let entry_c = std::ffi::CString::new(entry).unwrap();
        let target_c = std::ffi::CString::new(target).unwrap();
        let hr = D3DCompile(
            src.as_ptr() as *const c_void,
            src.len(),
            None,
            None,
            None,
            windows::core::PCSTR(entry_c.as_ptr() as *const u8),
            windows::core::PCSTR(target_c.as_ptr() as *const u8),
            D3DCOMPILE_OPTIMIZATION_LEVEL3,
            0,
            &mut blob,
            Some(&mut error),
        );
        if let Err(e) = hr {
            return Err(e);
        }
        Ok(blob.unwrap())
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_set_active(active: bool) -> bool {
    if !dcomp_filter_init() {
        return false;
    }

    let mut session_guard = SESSION.lock().unwrap();
    if active {
        if let Some(session) = session_guard.as_ref() {
            session.active.store(true, Ordering::SeqCst);
            session.cv.notify_all();
            return true;
        }

        let res = match RESOURCES.lock().unwrap().as_ref() {
            Some(r) => r.clone(),
            None => return false,
        };

        let running = Arc::new(AtomicBool::new(true));
        let is_active = Arc::new(AtomicBool::new(true));
        let mode = Arc::new(AtomicI32::new(FILTER_MODE.load(Ordering::SeqCst)));
        let target_screen = Arc::new(AtomicI32::new(TARGET_SCREEN.load(Ordering::SeqCst)));
        let cv = Arc::new(Condvar::new());
        let lock = Arc::new(Mutex::new(()));

        let r_clone = Arc::clone(&running);
        let a_clone = Arc::clone(&is_active);
        let m_clone = Arc::clone(&mode);
        let t_clone = Arc::clone(&target_screen);
        let c_clone = Arc::clone(&cv);
        let l_clone = Arc::clone(&lock);

        let render_handle = thread::spawn(move || {
            render_worker_thread(
                res,
                r_clone,
                a_clone,
                m_clone,
                t_clone,
                c_clone,
                l_clone,
            );
        });

        *session_guard = Some(ActiveSession {
            running,
            active: is_active,
            mode,
            target_screen,
            cv,
            _lock: lock,
            render_handle: Some(render_handle),
        });
        true
    } else {
        if let Some(session) = session_guard.as_ref() {
            session.active.store(false, Ordering::SeqCst);
            session.cv.notify_all();
        }
        true
    }
}

fn capture_worker_thread(
    device: ID3D11Device,
    context: Arc<Mutex<ContextWrapper>>,
    output1: IDXGIOutput1,
    ping_pong_tex: ID3D11Texture2D,
    has_new_frame: Arc<AtomicBool>,
    notify: Arc<Condvar>,
    running: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    target_screen: Arc<AtomicI32>,
    output_idx: usize,
) {
    unsafe {
        let hdesk = OpenInputDesktop(0, 0, 0x01ff);
        if hdesk != 0 {
            let _ = SetThreadDesktop(hdesk);
        }
        let _ = SetThreadPriority(GetCurrentThread(), 2); // THREAD_PRIORITY_HIGHEST
    }

    let mut dup: Option<IDXGIOutputDuplication> = None;

    while running.load(Ordering::Relaxed) {
        if !active.load(Ordering::Relaxed) {
            if dup.is_some() {
                dup = None;
            }
            thread::sleep(Duration::from_millis(20));
            continue;
        }

        let req_target = target_screen.load(Ordering::Relaxed);
        if req_target >= 0 && req_target as usize != output_idx {
            if dup.is_some() {
                dup = None;
            }
            thread::sleep(Duration::from_millis(50));
            continue;
        }

        if dup.is_none() {
            unsafe {
                match output1.DuplicateOutput(&device) {
                    Ok(d) => {
                        dup = Some(d);
                    }
                    Err(e) => {
                        println!("[Capture {}] DuplicateOutput failed: {:?}", output_idx, e);
                        thread::sleep(Duration::from_millis(50));
                        continue;
                    }
                }
            }
        }

        let duplication = dup.as_ref().unwrap();
        let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut desktop_resource: Option<IDXGIResource> = None;

        let acq_res = unsafe { duplication.AcquireNextFrame(16, &mut frame_info, &mut desktop_resource) };

        match acq_res {
            Ok(_) => {
                CNT_ACQ_OK.fetch_add(1, Ordering::Relaxed);
                if let Some(res) = desktop_resource.take() {
                    CNT_RES_SOME.fetch_add(1, Ordering::Relaxed);
                    if let Ok(tex) = res.cast::<ID3D11Texture2D>() {
                        {
                            let ctx = context.lock().unwrap();
                            unsafe {
                                ctx.0.CopyResource(&ping_pong_tex, &tex);
                            }
                        }
                        drop(tex);
                        drop(res);
                        let _ = unsafe { duplication.ReleaseFrame() };
                        has_new_frame.store(true, Ordering::Release);
                        notify.notify_one();
                    } else {
                        drop(res);
                        let _ = unsafe { duplication.ReleaseFrame() };
                    }
                } else {
                    let _ = unsafe { duplication.ReleaseFrame() };
                }
            }
            Err(e) => {
                if e.code() == DXGI_ERROR_WAIT_TIMEOUT {
                    CNT_ACQ_TIMEOUT.fetch_add(1, Ordering::Relaxed);
                } else {
                    println!("[Capture {}] Acquire error: {:?}", output_idx, e);
                    dup = None;
                    thread::sleep(Duration::from_millis(50));
                }
            }
        }
    }
    dup = None;
    println!("[Capture {}] Exiting cleanly.", output_idx);
}

fn render_worker_thread(
    res: D3DResources,
    running: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    mode: Arc<AtomicI32>,
    target_screen: Arc<AtomicI32>,
    main_cv: Arc<Condvar>,
    main_lock: Arc<Mutex<()>>,
) {
    unsafe {
        let hdesk = OpenInputDesktop(0, 0, 0x01ff);
        if hdesk != 0 {
            let _ = SetThreadDesktop(hdesk);
        }
        let _ = timeBeginPeriod(1);
        let _ = SetThreadPriority(GetCurrentThread(), 2);
    }

    let device = res.device;
    let context = res.context;
    let vs = res.vs;
    let ps_oklch = res.ps_oklch;
    let ps_luma = res.ps_luma;
    let input_layout = res.input_layout;
    let vbo = res.vbo;
    let sampler = res.sampler;
    let dcomp_device = res.dcomp_device;

    let mut screens: Vec<ScreenChannels> = Vec::new();
    let mut capture_threads: Vec<JoinHandle<()>> = Vec::new();

    unsafe {
        if let Ok(dxgi_device) = device.cast::<IDXGIDevice>() {
            if let Ok(adapter) = dxgi_device.GetAdapter() {
                if let Ok(factory2) = adapter.GetParent::<IDXGIFactory2>() {
                    let mut out_idx = 0;
                    while let Ok(output) = adapter.EnumOutputs(out_idx) {
                        if let Ok(desc) = output.GetDesc() {
                            let x = desc.DesktopCoordinates.left;
                            let y = desc.DesktopCoordinates.top;
                            let w = (desc.DesktopCoordinates.right - desc.DesktopCoordinates.left).max(1) as u32;
                            let h = (desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top).max(1) as u32;

                            if let Ok(output1) = output.cast::<IDXGIOutput1>() {
                                if let Ok(hwnd) = create_screen_overlay_window(x, y, w as i32, h as i32) {
                                    if let Ok(target) = dcomp_device.CreateTargetForHwnd(hwnd, true) {
                                        if let Ok(visual) = dcomp_device.CreateVisual() {
                                            let sc_desc = DXGI_SWAP_CHAIN_DESC1 {
                                                Width: w,
                                                Height: h,
                                                Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                                                SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
                                                BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
                                                BufferCount: 2,
                                                Scaling: DXGI_SCALING_STRETCH,
                                                SwapEffect: DXGI_SWAP_EFFECT_FLIP_DISCARD,
                                                AlphaMode: DXGI_ALPHA_MODE_PREMULTIPLIED,
                                                Flags: 0,
                                                ..Default::default()
                                            };

                                            if let Ok(sc) = factory2.CreateSwapChainForComposition(&device, &sc_desc, None) {
                                                let _ = visual.SetContent(&sc);
                                                let _ = target.SetRoot(&visual);
                                                let _ = dcomp_device.Commit();

                                                if let Ok(back_buffer) = sc.GetBuffer::<ID3D11Texture2D>(0) {
                                                    let mut rtv_opt = None;
                                                    if device.CreateRenderTargetView(&back_buffer, None, Some(&mut rtv_opt)).is_ok() {
                                                        if let Some(rtv) = rtv_opt {
                                                            let ping_pong_desc = D3D11_TEXTURE2D_DESC {
                                                                Width: w,
                                                                Height: h,
                                                                MipLevels: 1,
                                                                ArraySize: 1,
                                                                Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                                                                SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
                                                                Usage: D3D11_USAGE_DEFAULT,
                                                                BindFlags: D3D11_BIND_SHADER_RESOURCE.0 as u32,
                                                                CPUAccessFlags: 0,
                                                                MiscFlags: 0,
                                                            };

                                                            let mut ping_pong_tex = None;
                                                            if device.CreateTexture2D(&ping_pong_desc, None, Some(&mut ping_pong_tex)).is_ok() {
                                                                let ping_pong_tex = ping_pong_tex.unwrap();
                                                                let mut ping_pong_srv = None;
                                                                if device.CreateShaderResourceView(&ping_pong_tex, None, Some(&mut ping_pong_srv)).is_ok() {
                                                                    let ping_pong_srv = ping_pong_srv.unwrap();
                                                                    let has_new_frame = Arc::new(AtomicBool::new(false));
                                                                    let notify = Arc::new(Condvar::new());
                                                                    let lock = Arc::new(Mutex::new(()));

                                                                    // Spawn dedicated capture thread for this output
                                                                    let dev_c = device.clone();
                                                                    let ctx_c = Arc::clone(&context);
                                                                    let out1_c = output1.clone();
                                                                    let tex_c = ping_pong_tex.clone();
                                                                    let has_frame_c = Arc::clone(&has_new_frame);
                                                                    let notify_c = Arc::clone(&notify);
                                                                    let r_c = Arc::clone(&running);
                                                                    let a_c = Arc::clone(&active);
                                                                    let t_c = Arc::clone(&target_screen);

                                                                    let cap_handle = thread::spawn(move || {
                                                                        capture_worker_thread(
                                                                            dev_c,
                                                                            ctx_c,
                                                                            out1_c,
                                                                            tex_c,
                                                                            has_frame_c,
                                                                            notify_c,
                                                                            r_c,
                                                                            a_c,
                                                                            t_c,
                                                                            out_idx as usize,
                                                                        );
                                                                    });
                                                                    capture_threads.push(cap_handle);

                                                                    screens.push(ScreenChannels {
                                                                        output_idx: out_idx as usize,
                                                                        rect: desc.DesktopCoordinates,
                                                                        hwnd,
                                                                        _dcomp_target: target,
                                                                        _dcomp_visual: visual,
                                                                        swapchain: sc,
                                                                        rtv,
                                                                        ping_pong_srv,
                                                                        has_new_frame,
                                                                        notify,
                                                                        lock,
                                                                    });
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        out_idx += 1;
                    }
                }
            }
        }
    }

    if screens.is_empty() {
        IS_HEALTHY.store(false, Ordering::SeqCst);
        return;
    }

    let mut screen_visibilities = vec![false; screens.len()];

    while running.load(Ordering::Relaxed) {
        // Window message pump for all windows and thread messages on this thread
        unsafe {
            let mut msg = MSG::default();
            while PeekMessageW(&mut msg, HWND::default(), 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }

        let is_act = active.load(Ordering::Relaxed);
        let req_target = target_screen.load(Ordering::Relaxed);

        if !is_act {
            for (idx, screen) in screens.iter().enumerate() {
                if screen_visibilities[idx] {
                    unsafe {
                        let _ = ShowWindow(screen.hwnd, SW_HIDE);
                    }
                    screen_visibilities[idx] = false;
                }
            }

            let mut lk = main_lock.lock().unwrap();
            while !active.load(Ordering::Relaxed) && running.load(Ordering::Relaxed) {
                lk = main_cv.wait_timeout(lk, Duration::from_millis(50)).unwrap().0;
            }
            if !running.load(Ordering::Relaxed) {
                break;
            }
            continue;
        }

        // Update window visibility based on target_screen
        for (idx, screen) in screens.iter().enumerate() {
            let should_show = req_target < 0 || req_target as usize == screen.output_idx;
            if should_show && !screen_visibilities[idx] {
                unsafe {
                    let x = screen.rect.left;
                    let y = screen.rect.top;
                    let w = (screen.rect.right - screen.rect.left).max(1);
                    let h = (screen.rect.bottom - screen.rect.top).max(1);
                    let _ = SetWindowPos(
                        screen.hwnd,
                        HWND_TOPMOST,
                        x,
                        y,
                        w,
                        h,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW,
                    );
                }
                screen_visibilities[idx] = true;
            } else if !should_show && screen_visibilities[idx] {
                unsafe {
                    let _ = ShowWindow(screen.hwnd, SW_HIDE);
                }
                screen_visibilities[idx] = false;
            }
        }

        // For each visible screen, check if a new frame is ready and render it
        let mut any_rendered = false;

        for (idx, screen) in screens.iter().enumerate() {
            if !screen_visibilities[idx] {
                continue;
            }

            let has_frame = screen.has_new_frame.swap(false, Ordering::AcqRel);
            if has_frame {
                any_rendered = true;
                let w = (screen.rect.right - screen.rect.left).max(1) as f32;
                let h = (screen.rect.bottom - screen.rect.top).max(1) as f32;

                {
                    let ctx = context.lock().unwrap();
                    unsafe {
                        let vp = D3D11_VIEWPORT {
                            TopLeftX: 0.0,
                            TopLeftY: 0.0,
                            Width: w,
                            Height: h,
                            MinDepth: 0.0,
                            MaxDepth: 1.0,
                        };
                        ctx.0.RSSetViewports(Some(&[vp]));
                        ctx.0.OMSetRenderTargets(Some(&[Some(screen.rtv.clone())]), None);
                        ctx.0.IASetInputLayout(&input_layout);
                        ctx.0.IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
                        let stride = std::mem::size_of::<Vertex>() as u32;
                        let offset = 0u32;
                        ctx.0.IASetVertexBuffers(0, 1, Some(&Some(vbo.clone())), Some(&stride), Some(&offset));
                        ctx.0.VSSetShader(&vs, None);
                        ctx.0.PSSetSamplers(0, Some(&[Some(sampler.clone())]));

                        let cur_mode = mode.load(Ordering::Relaxed);
                        if cur_mode == 1 {
                            ctx.0.PSSetShader(&ps_luma, None);
                        } else {
                            ctx.0.PSSetShader(&ps_oklch, None);
                        }

                        ctx.0.PSSetShaderResources(0, Some(&[Some(screen.ping_pong_srv.clone())]));
                        ctx.0.Draw(4, 0);
                        ctx.0.PSSetShaderResources(0, Some(&[None]));
                    }
                }

                unsafe {
                    let pres_params = DXGI_PRESENT_PARAMETERS::default();
                    let pres = screen.swapchain.Present1(0, DXGI_PRESENT(0), &pres_params);
                    let _ = dcomp_device.Commit();
                    if pres.is_ok() {
                        FRAME_COUNT.fetch_add(1, Ordering::Relaxed);
                        CNT_PRESENT_OK.fetch_add(1, Ordering::Relaxed);
                    } else {
                        CNT_PRESENT_ERR.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
        }

        if !any_rendered {
            if let Some(first_vis) = screens.iter().find(|s| {
                let idx = s.output_idx;
                req_target < 0 || req_target as usize == idx
            }) {
                let lk = first_vis.lock.lock().unwrap();
                if !first_vis.has_new_frame.load(Ordering::Acquire) {
                    let _ = first_vis.notify.wait_timeout(lk, Duration::from_millis(8));
                }
            } else {
                thread::sleep(Duration::from_millis(8));
            }
        }
    }

    println!("[Render] Loop exited. Waiting for capture threads...");
    for (i, handle) in capture_threads.into_iter().enumerate() {
        println!("[Render] Joining capture thread {}...", i);
        let _ = handle.join();
        println!("[Render] Joined capture thread {}.", i);
    }

    println!("[Render] Destroying overlay windows...");
    unsafe {
        let _ = timeEndPeriod(1);
        for screen in screens {
            let _ = ShowWindow(screen.hwnd, SW_HIDE);
            let _ = DestroyWindow(screen.hwnd);
        }
    }
    println!("[Render] Exited cleanly.");
}

#[no_mangle]
pub extern "C" fn dcomp_filter_set_mode(mode: i32) {
    FILTER_MODE.store(mode, Ordering::SeqCst);
    let session_guard = SESSION.lock().unwrap();
    if let Some(session) = session_guard.as_ref() {
        session.mode.store(mode, Ordering::SeqCst);
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_set_target(screen_index: i32) -> bool {
    TARGET_SCREEN.store(screen_index, Ordering::SeqCst);
    let session_guard = SESSION.lock().unwrap();
    if let Some(session) = session_guard.as_ref() {
        session.target_screen.store(screen_index, Ordering::SeqCst);
        session.cv.notify_all();
    }
    true
}

#[no_mangle]
pub extern "C" fn dcomp_filter_is_active() -> bool {
    let session_guard = SESSION.lock().unwrap();
    if let Some(session) = session_guard.as_ref() {
        session.active.load(Ordering::SeqCst)
    } else {
        false
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_is_healthy() -> bool {
    IS_HEALTHY.load(Ordering::SeqCst)
}

#[no_mangle]
pub extern "C" fn dcomp_filter_get_screen_count() -> i32 {
    let res_guard = RESOURCES.lock().unwrap();
    if let Some(res) = res_guard.as_ref() {
        unsafe {
            if let Ok(dxgi_device) = res.device.cast::<IDXGIDevice>() {
                if let Ok(adapter) = dxgi_device.GetAdapter() {
                    let mut count = 0;
                    while adapter.EnumOutputs(count).is_ok() {
                        count += 1;
                    }
                    return count as i32;
                }
            }
        }
    }
    1
}

#[no_mangle]
pub extern "C" fn dcomp_filter_get_frame_count() -> u64 {
    FRAME_COUNT.load(Ordering::Relaxed)
}

#[no_mangle]
pub extern "C" fn dcomp_filter_get_stats(out: *mut u64) {
    if out.is_null() { return; }
    unsafe {
        *out.add(0) = CNT_ACQ_OK.load(Ordering::Relaxed);
        *out.add(1) = CNT_ACQ_TIMEOUT.load(Ordering::Relaxed);
        *out.add(2) = CNT_RES_SOME.load(Ordering::Relaxed);
        *out.add(3) = CNT_RES_NONE.load(Ordering::Relaxed);
        *out.add(4) = CNT_PRESENT_OK.load(Ordering::Relaxed);
        *out.add(5) = CNT_PRESENT_ERR.load(Ordering::Relaxed);
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_cleanup() {
    println!("[Cleanup] Starting cleanup...");
    let mut session_guard = SESSION.lock().unwrap();
    if let Some(mut session) = session_guard.take() {
        session.running.store(false, Ordering::SeqCst);
        session.active.store(false, Ordering::SeqCst);
        session.cv.notify_all();
        if let Some(h) = session.render_handle.take() {
            println!("[Cleanup] Joining render handle...");
            let _ = h.join();
            println!("[Cleanup] Render handle joined.");
        }
    }
    println!("[Cleanup] Completed.");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pipeline() {
        println!("=== Starting test_pipeline ===");
        unsafe {
            let hdesk = OpenInputDesktop(0, 0, 0x01ff);
            if hdesk != 0 {
                let _ = SetThreadDesktop(hdesk);
            }

            let factory: IDXGIFactory6 = CreateDXGIFactory2(DXGI_CREATE_FACTORY_FLAGS(0)).expect("CreateDXGIFactory2 failed");
            let adapter = factory.EnumAdapters1(0).expect("adapter 0 failed");

            let feature_levels = [D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0];
            let mut dev = None;
            let mut ctx = None;
            let hr = D3D11CreateDevice(
                &adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                None,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                Some(&feature_levels),
                D3D11_SDK_VERSION,
                Some(&mut dev),
                None,
                Some(&mut ctx),
            );
            assert!(hr.is_ok());
            let dev = dev.unwrap();

            let mut out_idx = 0;
            while let Ok(output) = adapter.EnumOutputs(out_idx) {
                let out_desc = output.GetDesc().unwrap();
                let out_name = String::from_utf16_lossy(&out_desc.DeviceName);
                let out_name = out_name.trim_matches(' ');
                println!("Testing Output [{}]: {} ({:?})", out_idx, out_name, out_desc.DesktopCoordinates);

                let output1: IDXGIOutput1 = output.cast().unwrap();
                match output1.DuplicateOutput(&dev) {
                    Ok(dup) => {
                        println!("  DuplicateOutput: SUCCESS on Output [{}]!", out_idx);
                        let mut frame_count = 0;
                        let start = std::time::Instant::now();
                        while start.elapsed() < Duration::from_secs(2) {
                            let mut f_info = DXGI_OUTDUPL_FRAME_INFO::default();
                            let mut d_res = None;
                            match dup.AcquireNextFrame(20, &mut f_info, &mut d_res) {
                                Ok(_) => {
                                    frame_count += 1;
                                    let _ = dup.ReleaseFrame();
                                }
                                Err(e) => {
                                    if e.code() != DXGI_ERROR_WAIT_TIMEOUT {
                                        println!("Acquire error: {:?}", e);
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        println!("DuplicateOutput failed: {:?}", e);
                    }
                }
                out_idx += 1;
            }

            let dxgi_dev: IDXGIDevice = dev.cast().unwrap();
            let dcomp_dev: IDCompositionDevice = DCompositionCreateDevice(&dxgi_dev).unwrap();

            let hwnd = create_screen_overlay_window(0, 0, 800, 600).unwrap();
            let dcomp_target = dcomp_dev.CreateTargetForHwnd(hwnd, true).unwrap();
            let dcomp_visual = dcomp_dev.CreateVisual().unwrap();
            dcomp_target.SetRoot(&dcomp_visual).unwrap();

            let sc_desc = DXGI_SWAP_CHAIN_DESC1 {
                Width: 800,
                Height: 600,
                Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
                BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
                BufferCount: 2,
                Scaling: DXGI_SCALING_STRETCH,
                SwapEffect: DXGI_SWAP_EFFECT_FLIP_DISCARD,
                AlphaMode: DXGI_ALPHA_MODE_PREMULTIPLIED,
                Flags: 0,
                ..Default::default()
            };
            let factory2: IDXGIFactory2 = adapter.GetParent().unwrap();
            let sc = factory2.CreateSwapChainForComposition(&dev, &sc_desc, None).unwrap();
            dcomp_visual.SetContent(&sc).unwrap();
            dcomp_dev.Commit().unwrap();

            let _ = DestroyWindow(hwnd);
        }
        println!("=== Finished test_pipeline ===");
    }
}
