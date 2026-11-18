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
use windows::Win32::Graphics::Gdi::{
    BeginPaint, EndPaint, GetDC, ReleaseDC, SetDIBitsToDevice, BITMAPINFO, BITMAPINFOHEADER,
    BI_RGB, DIB_RGB_COLORS, PAINTSTRUCT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::*;

pub const WDA_EXCLUDEFROMCAPTURE: u32 = 0x00000011;
const DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: isize = -4;

extern "system" {
    fn OpenInputDesktop(dwflags: u32, finherit: i32, dwdesiredaccess: u32) -> isize;
    fn SetThreadDesktop(hdesktop: isize) -> i32;
    fn SetThreadDpiAwarenessContext(dpi_context: isize) -> isize;
    fn GetCurrentThread() -> isize;
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
    float3 col = screenTex.Load(int3(int2(input.pos.xy), 0)).rgb;
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
    float3 col = screenTex.Load(int3(int2(input.pos.xy), 0)).rgb;
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
    raster_state: ID3D11RasterizerState,
    _dcomp_device: IDCompositionDevice,
}

unsafe impl Send for D3DResources {}
unsafe impl Sync for D3DResources {}

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RegionRect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

const MAX_REGIONS: usize = 64;
const MAX_DIRTY_RECTS: usize = 16;

#[derive(Clone, Debug, Default)]
struct PendingDirty {
    full_dirty: bool,
    rects: Vec<RECT>,
}

impl PendingDirty {
    fn push_full(&mut self) {
        self.full_dirty = true;
        self.rects.clear();
    }

    fn push_rect(&mut self, r: RECT) {
        if self.full_dirty || r.right <= r.left || r.bottom <= r.top {
            return;
        }
        if self.rects.len() < MAX_DIRTY_RECTS {
            self.rects.push(r);
        } else {
            // Merge into a bounding rect of the overflow slot to bound scissor/BitBlt calls.
            let last = self.rects.last_mut().unwrap();
            last.left = last.left.min(r.left);
            last.top = last.top.min(r.top);
            last.right = last.right.max(r.right);
            last.bottom = last.bottom.max(r.bottom);
        }
    }

    fn take(&mut self) -> (bool, Vec<RECT>) {
        let full = self.full_dirty;
        self.full_dirty = false;
        let rects = std::mem::take(&mut self.rects);
        (full, rects)
    }
}

struct ScreenChannels {
    output_idx: usize,
    rect: RECT,
    hwnds: Vec<HWND>,
    rt_tex: ID3D11Texture2D,
    stg_tex: ID3D11Texture2D,
    rtv: ID3D11RenderTargetView,
    ping_pong_srv: ID3D11ShaderResourceView,
    has_new_frame: Arc<AtomicBool>,
    pending_dirty: Arc<Mutex<PendingDirty>>,
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
static REGION_ENABLED: AtomicBool = AtomicBool::new(false);
static REGION_GEN: AtomicU64 = AtomicU64::new(0);
static REGIONS: Mutex<Vec<RegionRect>> = Mutex::new(Vec::new());
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
            | WS_EX_NOACTIVATE;

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
    if let Some(res) = guard.as_ref() {
        let dev_ok = unsafe { res.device.GetDeviceRemovedReason().is_ok() };
        if dev_ok {
            return IS_HEALTHY.load(Ordering::SeqCst);
        }
        *guard = None;
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
                println!("[DComp] Found adapter {}: {} (outputs: {})", idx, name.trim_matches('\0'), out_count);
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
            println!("[DComp] Selected adapter: {}", name.trim_matches('\0'));
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
            let _ = mt.SetMultithreadProtected(true);
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

        // Scissor-enabled rasterizer state so regional grayscale only shades the active region
        let rast_desc = D3D11_RASTERIZER_DESC {
            FillMode: D3D11_FILL_SOLID,
            CullMode: D3D11_CULL_NONE,
            ScissorEnable: windows::Win32::Foundation::TRUE,
            DepthClipEnable: windows::Win32::Foundation::TRUE,
            ..Default::default()
        };
        let mut raster_state = None;
        device.CreateRasterizerState(&rast_desc, Some(&mut raster_state))?;
        let raster_state = raster_state.unwrap();

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
            raster_state,
            _dcomp_device: dcomp_device,
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
    if active {
        let mut session_guard = SESSION.lock().unwrap();
        if let Some(session) = session_guard.as_ref() {
            if session.running.load(Ordering::SeqCst) && IS_HEALTHY.load(Ordering::SeqCst) {
                session.active.store(true, Ordering::SeqCst);
                session.cv.notify_all();
                return true;
            }
        }
        // Clean up any stopped/failed session before a fresh activation attempt so
        // transient failures (UAC, monitor sleep, mode change) recover cleanly.
        if let Some(mut dead_session) = session_guard.take() {
            dead_session.running.store(false, Ordering::SeqCst);
            dead_session.active.store(false, Ordering::SeqCst);
            dead_session.cv.notify_all();
            if let Some(h) = dead_session.render_handle.take() {
                let _ = h.join();
            }
        }
        IS_HEALTHY.store(true, Ordering::SeqCst);

        if !dcomp_filter_init() {
            return false;
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
        let session_guard = SESSION.lock().unwrap();
        if let Some(session) = session_guard.as_ref() {
            session.active.store(false, Ordering::SeqCst);
            session.cv.notify_all();
        }
        true
    }
}

fn attach_input_desktop() {
    unsafe {
        let hdesk = OpenInputDesktop(0, 0, 0x01ff);
        if hdesk != 0 {
            let _ = SetThreadDesktop(hdesk);
        }
    }
}

fn intersect_rect(a: &RECT, b: &RECT) -> Option<RECT> {
    let left = a.left.max(b.left);
    let top = a.top.max(b.top);
    let right = a.right.min(b.right);
    let bottom = a.bottom.min(b.bottom);
    if right > left && bottom > top {
        Some(RECT { left, top, right, bottom })
    } else {
        None
    }
}

fn append_unique_rect(dst: &mut Vec<RECT>, candidate: RECT) {
    if candidate.right <= candidate.left || candidate.bottom <= candidate.top {
        return;
    }
    if dst.len() < MAX_DIRTY_RECTS {
        dst.push(candidate);
    } else {
        let last = dst.last_mut().unwrap();
        last.left = last.left.min(candidate.left);
        last.top = last.top.min(candidate.top);
        last.right = last.right.max(candidate.right);
        last.bottom = last.bottom.max(candidate.bottom);
    }
}

#[allow(clippy::too_many_arguments)]
fn capture_worker_thread(
    device: ID3D11Device,
    context: Arc<Mutex<ContextWrapper>>,
    output1: IDXGIOutput1,
    screen_rect: RECT,
    ping_pong_tex: ID3D11Texture2D,
    has_new_frame: Arc<AtomicBool>,
    pending_dirty: Arc<Mutex<PendingDirty>>,
    shared_notify: Arc<(Mutex<()>, Condvar)>,
    running: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    target_screen: Arc<AtomicI32>,
    topology_dirty: Arc<AtomicBool>,
    output_idx: usize,
) {
    unsafe {
        let _ = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        attach_input_desktop();
        let _ = SetThreadPriority(GetCurrentThread(), 2); // THREAD_PRIORITY_HIGHEST
    }

    let screen_w = (screen_rect.right - screen_rect.left).max(1);
    let screen_h = (screen_rect.bottom - screen_rect.top).max(1);
    let local_bounds = RECT { left: 0, top: 0, right: screen_w, bottom: screen_h };

    let mut dup: Option<IDXGIOutputDuplication> = None;
    let mut dup_has_initial_frame = false;
    let mut ever_succeeded = false;
    let mut dup_fail_count = 0u32;
    let mut cached_region_gen = u64::MAX;
    let mut cached_regions: Vec<RegionRect> = Vec::new();
    let mut move_buf: Vec<DXGI_OUTDUPL_MOVE_RECT> = Vec::with_capacity(16);
    let mut dirty_buf: Vec<RECT> = Vec::with_capacity(64);
    let mut copy_boxes: Vec<RECT> = Vec::with_capacity(MAX_DIRTY_RECTS);

    while running.load(Ordering::Relaxed) {
        if !active.load(Ordering::Relaxed) {
            if dup.is_some() {
                dup = None;
                dup_has_initial_frame = false;
            }
            thread::sleep(Duration::from_millis(20));
            continue;
        }

        let cur_region_gen = REGION_GEN.load(Ordering::Acquire);
        if cur_region_gen != cached_region_gen {
            if let Ok(guard) = REGIONS.lock() {
                cached_regions.clear();
                cached_regions.extend_from_slice(&guard);
            }
            if cached_region_gen != u64::MAX && dup.is_some() {
                // Re-acquire duplication on region change so static desktops immediately provide a fresh full texture.
                dup = None;
                dup_has_initial_frame = false;
            }
            cached_region_gen = cur_region_gen;
        }

        let reg_en = REGION_ENABLED.load(Ordering::Relaxed);
        if reg_en {
            let intersects_screen = cached_regions.iter().any(|r| {
                r.w > 0
                    && r.h > 0
                    && (r.x + r.w) > screen_rect.left
                    && r.x < screen_rect.right
                    && (r.y + r.h) > screen_rect.top
                    && r.y < screen_rect.bottom
            });
            if !intersects_screen {
                if dup.is_some() {
                    dup = None;
                    dup_has_initial_frame = false;
                }
                thread::sleep(Duration::from_millis(50));
                continue;
            }
        } else {
            let req_target = target_screen.load(Ordering::Relaxed);
            if req_target >= 0 && req_target as usize != output_idx {
                if dup.is_some() {
                    dup = None;
                    dup_has_initial_frame = false;
                }
                thread::sleep(Duration::from_millis(50));
                continue;
            }
        }

        if dup.is_none() {
            unsafe {
                let mut d_res: Option<windows::core::Result<IDXGIOutputDuplication>> = None;
                if let Ok(output5) = output1.cast::<IDXGIOutput5>() {
                    let formats = [DXGI_FORMAT_B8G8R8A8_UNORM];
                    let res = output5.DuplicateOutput1(&device, 0, &formats);
                    if res.is_ok() {
                        d_res = Some(res);
                    }
                }
                if d_res.is_none() {
                    d_res = Some(output1.DuplicateOutput(&device));
                }

                match d_res.unwrap() {
                    Ok(d) => {
                        dup = Some(d);
                        dup_has_initial_frame = false;
                        ever_succeeded = true;
                        dup_fail_count = 0;
                    }
                    Err(e) => {
                        dup_fail_count += 1;
                        attach_input_desktop();
                        if e.code() == DXGI_ERROR_ACCESS_LOST || e.code() == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE {
                            topology_dirty.store(true, Ordering::Release);
                            shared_notify.1.notify_one();
                        }
                        let max_attempts = if ever_succeeded { 25 } else { 3 };
                        println!(
                            "[Capture {}] DuplicateOutput failed (attempt {}/{}): {:?}",
                            output_idx, dup_fail_count, max_attempts, e
                        );
                        if dup_fail_count >= max_attempts {
                            IS_HEALTHY.store(false, Ordering::SeqCst);
                            running.store(false, Ordering::SeqCst);
                            shared_notify.1.notify_one();
                            println!("[Capture {}] DuplicateOutput failed repeatedly, marked DComp as unhealthy.", output_idx);
                            break;
                        }
                        thread::sleep(Duration::from_millis(20));
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
                    // Cursor-only updates set LastPresentTime == 0 and AccumulatedFrames == 0;
                    // hardware cursor is drawn above WDA_EXCLUDEFROMCAPTURE windows, so avoid
                    // copying/shading/GDI-blitting the whole desktop at stylus/mouse polling rates.
                    if dup_has_initial_frame
                        && frame_info.LastPresentTime == 0
                        && frame_info.AccumulatedFrames == 0
                    {
                        drop(res);
                        let _ = unsafe { duplication.ReleaseFrame() };
                        continue;
                    }

                    CNT_RES_SOME.fetch_add(1, Ordering::Relaxed);
                    if let Ok(tex) = res.cast::<ID3D11Texture2D>() {
                        let mut frame_full_dirty =
                            !dup_has_initial_frame || frame_info.TotalMetadataBufferSize == 0;
                        copy_boxes.clear();

                        if !frame_full_dirty {
                            unsafe {
                                let meta_bytes = frame_info.TotalMetadataBufferSize as usize;
                                let max_moves = (meta_bytes / std::mem::size_of::<DXGI_OUTDUPL_MOVE_RECT>()).max(1);
                                if move_buf.len() < max_moves {
                                    move_buf.resize(max_moves, DXGI_OUTDUPL_MOVE_RECT::default());
                                }
                                let mut move_bytes = 0u32;
                                let mv_res = duplication.GetFrameMoveRects(
                                    (move_buf.len() * std::mem::size_of::<DXGI_OUTDUPL_MOVE_RECT>()) as u32,
                                    move_buf.as_mut_ptr(),
                                    &mut move_bytes,
                                );
                                let move_cnt = (move_bytes as usize) / std::mem::size_of::<DXGI_OUTDUPL_MOVE_RECT>();
                                if mv_res.is_err() || move_cnt > 0 {
                                    frame_full_dirty = true;
                                } else {
                                    let max_dirty = (meta_bytes / std::mem::size_of::<RECT>()).max(1);
                                    if dirty_buf.len() < max_dirty {
                                        dirty_buf.resize(max_dirty, RECT::default());
                                    }
                                    let mut dirty_bytes = 0u32;
                                    let dr_res = duplication.GetFrameDirtyRects(
                                        (dirty_buf.len() * std::mem::size_of::<RECT>()) as u32,
                                        dirty_buf.as_mut_ptr(),
                                        &mut dirty_bytes,
                                    );
                                    let dirty_cnt = (dirty_bytes as usize) / std::mem::size_of::<RECT>();
                                    if dr_res.is_err() || dirty_cnt == 0 {
                                        frame_full_dirty = true;
                                    } else {
                                        for raw_r in &dirty_buf[..dirty_cnt] {
                                            if let Some(clamped) = intersect_rect(raw_r, &local_bounds) {
                                                append_unique_rect(&mut copy_boxes, clamped);
                                            }
                                        }
                                        if copy_boxes.is_empty() {
                                            // Frame update outside monitor bounds (or empty dirty rects): nothing visible changed.
                                            drop(tex);
                                            drop(res);
                                            let _ = duplication.ReleaseFrame();
                                            continue;
                                        }
                                    }
                                }
                            }
                        }

                        if frame_full_dirty {
                            copy_boxes.clear();
                            copy_boxes.push(local_bounds);
                        }

                        // When region grayscale is active, filter copy_boxes against enabled regions
                        // so updates outside grayscaled boxes skip both GPU copy and GDI blitting.
                        if reg_en {
                            let mut region_clipped: Vec<RECT> = Vec::with_capacity(copy_boxes.len().max(1));
                            for r in &cached_regions {
                                if r.w <= 0 || r.h <= 0 {
                                    continue;
                                }
                                let ix1 = (r.x - screen_rect.left).clamp(0, screen_w);
                                let iy1 = (r.y - screen_rect.top).clamp(0, screen_h);
                                let ix2 = (r.x + r.w - screen_rect.left).clamp(0, screen_w);
                                let iy2 = (r.y + r.h - screen_rect.top).clamp(0, screen_h);
                                if ix2 <= ix1 || iy2 <= iy1 {
                                    continue;
                                }
                                let reg_local = RECT { left: ix1, top: iy1, right: ix2, bottom: iy2 };
                                for db in &copy_boxes {
                                    if let Some(ov) = intersect_rect(db, &reg_local) {
                                        append_unique_rect(&mut region_clipped, ov);
                                    }
                                }
                            }
                            if region_clipped.is_empty() {
                                dup_has_initial_frame = true;
                                drop(tex);
                                drop(res);
                                let _ = unsafe { duplication.ReleaseFrame() };
                                continue;
                            }
                            {
                                let ctx = context.lock().unwrap();
                                unsafe {
                                    for b in &region_clipped {
                                        let src_box = D3D11_BOX {
                                            left: b.left as u32,
                                            top: b.top as u32,
                                            front: 0,
                                            right: b.right as u32,
                                            bottom: b.bottom as u32,
                                            back: 1,
                                        };
                                        ctx.0.CopySubresourceRegion(
                                            &ping_pong_tex,
                                            0,
                                            b.left as u32,
                                            b.top as u32,
                                            0,
                                            &tex,
                                            0,
                                            Some(&src_box),
                                        );
                                    }
                                }
                            }
                            copy_boxes = region_clipped;
                        } else {
                            let ctx = context.lock().unwrap();
                            unsafe {
                                if frame_full_dirty {
                                    ctx.0.CopyResource(&ping_pong_tex, &tex);
                                } else {
                                    for b in &copy_boxes {
                                        let src_box = D3D11_BOX {
                                            left: b.left as u32,
                                            top: b.top as u32,
                                            front: 0,
                                            right: b.right as u32,
                                            bottom: b.bottom as u32,
                                            back: 1,
                                        };
                                        ctx.0.CopySubresourceRegion(
                                            &ping_pong_tex,
                                            0,
                                            b.left as u32,
                                            b.top as u32,
                                            0,
                                            &tex,
                                            0,
                                            Some(&src_box),
                                        );
                                    }
                                }
                            }
                        }

                        dup_has_initial_frame = true;
                        drop(tex);
                        drop(res);
                        let _ = unsafe { duplication.ReleaseFrame() };

                        {
                            let mut pd = pending_dirty.lock().unwrap();
                            if frame_full_dirty && !reg_en {
                                pd.push_full();
                            } else {
                                for b in &copy_boxes {
                                    pd.push_rect(*b);
                                }
                            }
                            has_new_frame.store(true, Ordering::Release);
                        }
                        let _lk = shared_notify.0.lock().unwrap();
                        shared_notify.1.notify_one();
                    } else {
                        drop(res);
                        let _ = unsafe { duplication.ReleaseFrame() };
                    }
                } else {
                    CNT_RES_NONE.fetch_add(1, Ordering::Relaxed);
                    let _ = unsafe { duplication.ReleaseFrame() };
                }
            }
            Err(e) => {
                if e.code() == DXGI_ERROR_WAIT_TIMEOUT {
                    CNT_ACQ_TIMEOUT.fetch_add(1, Ordering::Relaxed);
                } else {
                    println!("[Capture {}] Acquire error: {:?}", output_idx, e);
                    dup = None;
                    dup_has_initial_frame = false;
                    if e.code() == DXGI_ERROR_ACCESS_LOST || e.code() == DXGI_ERROR_SESSION_DISCONNECTED {
                        topology_dirty.store(true, Ordering::Release);
                        shared_notify.1.notify_one();
                    }
                    thread::sleep(Duration::from_millis(30));
                }
            }
        }
    }
    println!("[Capture {}] Exiting cleanly.", output_idx);
}

fn compute_screen_target_rects(
    screen_rect: &RECT,
    output_idx: usize,
    req_target: i32,
    reg_en: bool,
    regions: &[RegionRect],
    out: &mut Vec<(i32, i32, i32, i32, i32, i32)>,
) {
    out.clear();
    if reg_en {
        for r in regions {
            if r.w <= 0 || r.h <= 0 {
                continue;
            }
            let ix1 = r.x.max(screen_rect.left);
            let iy1 = r.y.max(screen_rect.top);
            let ix2 = (r.x + r.w).min(screen_rect.right);
            let iy2 = (r.y + r.h).min(screen_rect.bottom);
            if ix2 > ix1 && iy2 > iy1 {
                let win_w = ix2 - ix1;
                let win_h = iy2 - iy1;
                let local_left = ix1 - screen_rect.left;
                let local_top = iy1 - screen_rect.top;
                out.push((ix1, iy1, win_w, win_h, local_left, local_top));
            }
        }
    } else {
        if req_target >= 0 && req_target as usize != output_idx {
            return;
        }
        let x = screen_rect.left;
        let y = screen_rect.top;
        let win_w = (screen_rect.right - screen_rect.left).max(1);
        let raw_h = (screen_rect.bottom - screen_rect.top).max(1);
        // 伪全屏：每个显示器底部保留 2~4 物理像素不覆盖滤镜，避免触发 Windows DWM 全屏独占/DirectFlip 引起的卡顿
        let margin_h = if raw_h > 8 { (raw_h / 500).clamp(2, 4) } else { 0 };
        let win_h = raw_h - margin_h;
        out.push((x, y, win_w, win_h, 0, 0));
    }
}

struct TopologyState {
    factory: IDXGIFactory1,
    screens: Vec<ScreenChannels>,
    capture_threads: Vec<JoinHandle<()>>,
    cap_running: Arc<AtomicBool>,
    screen_applied_rects: Vec<Vec<Option<(i32, i32, i32, i32)>>>,
    screen_applied_clipped: Vec<bool>,
    screen_applied_modes: Vec<i32>,
    screen_has_presented: Vec<bool>,
}

fn find_matching_adapter(device: &ID3D11Device, factory: &IDXGIFactory1) -> windows::core::Result<IDXGIAdapter1> {
    unsafe {
        if let Ok(dxgi_dev) = device.cast::<IDXGIDevice>() {
            if let Ok(cur_adapter) = dxgi_dev.GetAdapter() {
                if let Ok(target_desc) = cur_adapter.GetDesc() {
                    let mut idx = 0;
                    while let Ok(cand) = factory.EnumAdapters1(idx) {
                        if let Ok(cand_desc) = cand.GetDesc1() {
                            if cand_desc.AdapterLuid == target_desc.AdapterLuid {
                                return Ok(cand);
                            }
                        }
                        idx += 1;
                    }
                }
            }
        }
        factory.EnumAdapters1(0)
    }
}

#[allow(clippy::too_many_arguments)]
fn build_topology_state(
    device: &ID3D11Device,
    context: &Arc<Mutex<ContextWrapper>>,
    shared_notify: &Arc<(Mutex<()>, Condvar)>,
    active: &Arc<AtomicBool>,
    target_screen: &Arc<AtomicI32>,
    topology_dirty: &Arc<AtomicBool>,
) -> windows::core::Result<TopologyState> {
    unsafe {
        let factory: IDXGIFactory1 = CreateDXGIFactory1()?;
        let adapter = find_matching_adapter(device, &factory)?;
        let cap_running = Arc::new(AtomicBool::new(true));
        let mut screens: Vec<ScreenChannels> = Vec::new();
        let mut capture_threads: Vec<JoinHandle<()>> = Vec::new();

        let mut out_idx = 0;
        while let Ok(output) = adapter.EnumOutputs(out_idx) {
            if let Ok(desc) = output.GetDesc() {
                let x = desc.DesktopCoordinates.left;
                let y = desc.DesktopCoordinates.top;
                let w = (desc.DesktopCoordinates.right - desc.DesktopCoordinates.left).max(1) as u32;
                let h = (desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top).max(1) as u32;

                if let Ok(output1) = output.cast::<IDXGIOutput1>() {
                    if let Ok(hwnd) = create_screen_overlay_window(x, y, w as i32, h as i32) {
                        let rt_desc = D3D11_TEXTURE2D_DESC {
                            Width: w,
                            Height: h,
                            MipLevels: 1,
                            ArraySize: 1,
                            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
                            Usage: D3D11_USAGE_DEFAULT,
                            BindFlags: D3D11_BIND_RENDER_TARGET.0 as u32,
                            CPUAccessFlags: 0,
                            MiscFlags: 0,
                        };
                        let stg_desc = D3D11_TEXTURE2D_DESC {
                            Width: w,
                            Height: h,
                            MipLevels: 1,
                            ArraySize: 1,
                            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
                            Usage: D3D11_USAGE_STAGING,
                            BindFlags: 0,
                            CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
                            MiscFlags: 0,
                        };

                        let mut rt_tex_opt = None;
                        let mut stg_tex_opt = None;
                        if device.CreateTexture2D(&rt_desc, None, Some(&mut rt_tex_opt)).is_ok()
                            && device.CreateTexture2D(&stg_desc, None, Some(&mut stg_tex_opt)).is_ok()
                        {
                            let rt_tex = rt_tex_opt.unwrap();
                            let stg_tex = stg_tex_opt.unwrap();
                            let mut rtv_opt = None;
                            if device.CreateRenderTargetView(&rt_tex, None, Some(&mut rtv_opt)).is_ok() {
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
                                            let pending_dirty = Arc::new(Mutex::new(PendingDirty::default()));

                                            let dev_c = device.clone();
                                            let ctx_c = Arc::clone(context);
                                            let out1_c = output1.clone();
                                            let rect_c = desc.DesktopCoordinates;
                                            let tex_c = ping_pong_tex.clone();
                                            let has_frame_c = Arc::clone(&has_new_frame);
                                            let dirty_c = Arc::clone(&pending_dirty);
                                            let notify_c = Arc::clone(shared_notify);
                                            let r_c = Arc::clone(&cap_running);
                                            let a_c = Arc::clone(active);
                                            let t_c = Arc::clone(target_screen);
                                            let topo_c = Arc::clone(topology_dirty);

                                            let cap_handle = thread::spawn(move || {
                                                capture_worker_thread(
                                                    dev_c,
                                                    ctx_c,
                                                    out1_c,
                                                    rect_c,
                                                    tex_c,
                                                    has_frame_c,
                                                    dirty_c,
                                                    notify_c,
                                                    r_c,
                                                    a_c,
                                                    t_c,
                                                    topo_c,
                                                    out_idx as usize,
                                                );
                                            });
                                            capture_threads.push(cap_handle);

                                            screens.push(ScreenChannels {
                                                output_idx: out_idx as usize,
                                                rect: desc.DesktopCoordinates,
                                                hwnds: vec![hwnd],
                                                rt_tex,
                                                stg_tex,
                                                rtv,
                                                ping_pong_srv,
                                                has_new_frame,
                                                pending_dirty,
                                            });
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

        let n_screens = screens.len();
        let screen_applied_rects = screens.iter().map(|s| vec![None; s.hwnds.len()]).collect();
        Ok(TopologyState {
            factory,
            screens,
            capture_threads,
            cap_running,
            screen_applied_rects,
            screen_applied_clipped: vec![false; n_screens],
            screen_applied_modes: vec![-1; n_screens],
            screen_has_presented: vec![false; n_screens],
        })
    }
}

fn teardown_topology_state(state: TopologyState) {
    state.cap_running.store(false, Ordering::SeqCst);
    for (i, handle) in state.capture_threads.into_iter().enumerate() {
        println!("[Render] Joining capture thread {}...", i);
        let _ = handle.join();
    }
    unsafe {
        for screen in state.screens {
            for hwnd in screen.hwnds {
                let _ = ShowWindow(hwnd, SW_HIDE);
                let _ = DestroyWindow(hwnd);
            }
        }
    }
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
        let _ = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        attach_input_desktop();
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
    let raster_state = res.raster_state;
    let _dcomp_device = res._dcomp_device;

    let shared_notify = Arc::new((Mutex::new(()), Condvar::new()));
    let topology_dirty = Arc::new(AtomicBool::new(false));
    let mut topo = match build_topology_state(
        &device,
        &context,
        &shared_notify,
        &active,
        &target_screen,
        &topology_dirty,
    ) {
        Ok(t) => t,
        Err(_) => {
            IS_HEALTHY.store(false, Ordering::SeqCst);
            running.store(false, Ordering::SeqCst);
            return;
        }
    };

    if topo.screens.is_empty() {
        IS_HEALTHY.store(false, Ordering::SeqCst);
        running.store(false, Ordering::SeqCst);
        teardown_topology_state(topo);
        return;
    }

    let mut timer_period_active = false;
    let mut was_active = false;
    let mut cached_region_gen = u64::MAX;
    let mut cached_regions: Vec<RegionRect> = Vec::new();
    let mut target_rects: Vec<(i32, i32, i32, i32, i32, i32)> = Vec::new();
    let mut draw_scissors: Vec<RECT> = Vec::with_capacity(MAX_DIRTY_RECTS);

    while running.load(Ordering::Relaxed) {
        unsafe {
            let mut msg = MSG::default();
            while PeekMessageW(&mut msg, HWND::default(), 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }

        let is_act = active.load(Ordering::Relaxed);
        let req_target = target_screen.load(Ordering::Relaxed);
        let cur_region_gen = REGION_GEN.load(Ordering::Acquire);
        if cur_region_gen != cached_region_gen {
            if let Ok(guard) = REGIONS.lock() {
                cached_regions.clear();
                cached_regions.extend_from_slice(&guard);
            }
            cached_region_gen = cur_region_gen;
        }
        let reg_en = REGION_ENABLED.load(Ordering::Relaxed);

        if !is_act {
            was_active = false;
            for (idx, screen) in topo.screens.iter().enumerate() {
                for (j, &hwnd) in screen.hwnds.iter().enumerate() {
                    if topo.screen_applied_rects[idx][j].is_some() {
                        unsafe {
                            let _ = ShowWindow(hwnd, SW_HIDE);
                        }
                        topo.screen_applied_rects[idx][j] = None;
                    }
                }
                topo.screen_has_presented[idx] = false;
            }
            if timer_period_active {
                unsafe {
                    let _ = timeEndPeriod(1);
                }
                timer_period_active = false;
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

        if !timer_period_active {
            unsafe {
                let _ = timeBeginPeriod(1);
            }
            timer_period_active = true;
        }

        if !IS_HEALTHY.load(Ordering::Relaxed) {
            println!("[Render] Backend became unhealthy, exiting render loop.");
            running.store(false, Ordering::SeqCst);
            break;
        }

        // Rebuild outputs when monitors were plugged/unplugged/resized (only poll IsCurrent on activation, not every active frame).
        let need_rebuild = topology_dirty.swap(false, Ordering::AcqRel)
            || (!was_active && !unsafe { topo.factory.IsCurrent().as_bool() });
        was_active = true;

        if need_rebuild {
            teardown_topology_state(topo);
            match build_topology_state(
                &device,
                &context,
                &shared_notify,
                &active,
                &target_screen,
                &topology_dirty,
            ) {
                Ok(new_topo) if !new_topo.screens.is_empty() => {
                    topo = new_topo;
                    continue;
                }
                _ => {
                    IS_HEALTHY.store(false, Ordering::SeqCst);
                    running.store(false, Ordering::SeqCst);
                    return;
                }
            }
        }

        let mut any_rendered = false;
        let mut any_visible_target = false;
        let cur_mode = mode.load(Ordering::Relaxed);

        for (idx, screen) in topo.screens.iter_mut().enumerate() {
            compute_screen_target_rects(
                &screen.rect,
                screen.output_idx,
                req_target,
                reg_en,
                &cached_regions,
                &mut target_rects,
            );
            if target_rects.is_empty() {
                for (j, &hwnd) in screen.hwnds.iter().enumerate() {
                    if topo.screen_applied_rects[idx][j].is_some() {
                        unsafe {
                            let _ = ShowWindow(hwnd, SW_HIDE);
                        }
                        topo.screen_applied_rects[idx][j] = None;
                    }
                }
                continue;
            }
            any_visible_target = true;

            while screen.hwnds.len() < target_rects.len() {
                if let Ok(extra_hwnd) = create_screen_overlay_window(
                    screen.rect.left,
                    screen.rect.top,
                    1,
                    1,
                ) {
                    screen.hwnds.push(extra_hwnd);
                    topo.screen_applied_rects[idx].push(None);
                } else {
                    break;
                }
            }
            let active_hwnd_count = target_rects.len().min(screen.hwnds.len());

            let mut surplus_hidden = false;
            for j in active_hwnd_count..screen.hwnds.len() {
                if topo.screen_applied_rects[idx][j].is_some() {
                    unsafe {
                        let _ = ShowWindow(screen.hwnds[j], SW_HIDE);
                    }
                    topo.screen_applied_rects[idx][j] = None;
                    surplus_hidden = true;
                }
            }

            let geom_changed = surplus_hidden
                || topo.screen_applied_clipped[idx] != reg_en
                || (0..active_hwnd_count).any(|j| {
                    let (wx, wy, ww, wh, _, _) = target_rects[j];
                    topo.screen_applied_rects[idx][j] != Some((wx, wy, ww, wh))
                });
            let mode_changed = topo.screen_applied_modes[idx] != cur_mode;

            let (mut frame_full, frame_rects) = if screen.has_new_frame.load(Ordering::Acquire) {
                let mut pd = screen.pending_dirty.lock().unwrap();
                screen.has_new_frame.store(false, Ordering::Release);
                pd.take()
            } else {
                (false, Vec::new())
            };

            let force_full = !topo.screen_has_presented[idx] || geom_changed || mode_changed;
            if force_full {
                frame_full = true;
            }

            let has_pending_work = frame_full || !frame_rects.is_empty();
            let should_draw = has_pending_work && (topo.screen_has_presented[idx] || !frame_rects.is_empty() || frame_full);
            if !should_draw {
                continue;
            }

            draw_scissors.clear();
            if frame_full {
                for &( _, _, win_w, win_h, local_left, local_top) in &target_rects[..active_hwnd_count] {
                    draw_scissors.push(RECT {
                        left: local_left,
                        top: local_top,
                        right: local_left + win_w,
                        bottom: local_top + win_h,
                    });
                }
            } else {
                for &( _, _, win_w, win_h, local_left, local_top) in &target_rects[..active_hwnd_count] {
                    let target_box = RECT {
                        left: local_left,
                        top: local_top,
                        right: local_left + win_w,
                        bottom: local_top + win_h,
                    };
                    for dr in &frame_rects {
                        if let Some(ov) = intersect_rect(dr, &target_box) {
                            append_unique_rect(&mut draw_scissors, ov);
                        }
                    }
                }
            }

            if draw_scissors.is_empty() {
                continue;
            }

            any_rendered = true;
            let raw_w = (screen.rect.right - screen.rect.left).max(1);
            let raw_h = (screen.rect.bottom - screen.rect.top).max(1);
            let w = raw_w as f32;
            let h = raw_h as f32;

            let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
            let map_ok = {
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
                    ctx.0.RSSetState(&raster_state);
                    ctx.0.RSSetViewports(Some(&[vp]));
                    ctx.0.OMSetRenderTargets(Some(&[Some(screen.rtv.clone())]), None);
                    ctx.0.IASetInputLayout(&input_layout);
                    ctx.0.IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
                    let stride = std::mem::size_of::<Vertex>() as u32;
                    let offset = 0u32;
                    ctx.0.IASetVertexBuffers(0, 1, Some(&Some(vbo.clone())), Some(&stride), Some(&offset));
                    ctx.0.VSSetShader(&vs, None);
                    ctx.0.PSSetSamplers(0, Some(&[Some(sampler.clone())]));

                    if cur_mode == 1 {
                        ctx.0.PSSetShader(&ps_luma, None);
                    } else {
                        ctx.0.PSSetShader(&ps_oklch, None);
                    }

                    ctx.0.PSSetShaderResources(0, Some(&[Some(screen.ping_pong_srv.clone())]));
                    ctx.0.RSSetScissorRects(Some(&draw_scissors));
                    ctx.0.Draw(4, 0);
                    ctx.0.PSSetShaderResources(0, Some(&[None]));

                    if frame_full {
                        for &(_, _, win_w, win_h, local_left, local_top) in &target_rects[..active_hwnd_count] {
                            let bx = D3D11_BOX {
                                left: local_left as u32,
                                top: local_top as u32,
                                front: 0,
                                right: (local_left + win_w).min(raw_w) as u32,
                                bottom: (local_top + win_h).min(raw_h) as u32,
                                back: 1,
                            };
                            ctx.0.CopySubresourceRegion(
                                &screen.stg_tex,
                                0,
                                local_left as u32,
                                local_top as u32,
                                0,
                                &screen.rt_tex,
                                0,
                                Some(&bx),
                            );
                        }
                    } else {
                        for sc_r in &draw_scissors {
                            let cl = sc_r.left.clamp(0, raw_w);
                            let ct = sc_r.top.clamp(0, raw_h);
                            let cr = sc_r.right.clamp(0, raw_w);
                            let cb = sc_r.bottom.clamp(0, raw_h);
                            if cr > cl && cb > ct {
                                let bx = D3D11_BOX {
                                    left: cl as u32,
                                    top: ct as u32,
                                    front: 0,
                                    right: cr as u32,
                                    bottom: cb as u32,
                                    back: 1,
                                };
                                ctx.0.CopySubresourceRegion(
                                    &screen.stg_tex,
                                    0,
                                    cl as u32,
                                    ct as u32,
                                    0,
                                    &screen.rt_tex,
                                    0,
                                    Some(&bx),
                                );
                            }
                        }
                    }
                    ctx.0.Map(&screen.stg_tex, 0, D3D11_MAP_READ, 0, Some(&mut mapped)).is_ok()
                }
            };

            // Ensure window bounds match before blitting into their redirection surfaces
            for j in 0..active_hwnd_count {
                let (win_x, win_y, win_w, win_h, _, _) = target_rects[j];
                if topo.screen_applied_rects[idx][j] != Some((win_x, win_y, win_w, win_h)) {
                    unsafe {
                        let _ = SetWindowPos(
                            screen.hwnds[j],
                            HWND_TOPMOST,
                            win_x,
                            win_y,
                            win_w,
                            win_h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW,
                        );
                    }
                    topo.screen_applied_rects[idx][j] = Some((win_x, win_y, win_w, win_h));
                }
            }
            topo.screen_applied_clipped[idx] = reg_en;

            if map_ok && !mapped.pData.is_null() {
                let pitch_bytes = mapped.RowPitch as usize;
                let pitch_px = (pitch_bytes / 4) as i32;
                let base_ptr = mapped.pData as *const u8;
                let mut all_ok = true;

                unsafe {
                    for j in 0..active_hwnd_count {
                        let (_, _, win_w, win_h, local_left, local_top) = target_rects[j];
                        let hwnd = screen.hwnds[j];
                        let win_hdc = GetDC(hwnd);
                        let blit_slice = |dl: i32, dt: i32, dr: i32, db: i32| {
                            let bw = dr - dl;
                            let bh = db - dt;
                            if bw <= 0 || bh <= 0 {
                                return true;
                            }
                            let bmi = BITMAPINFO {
                                bmiHeader: BITMAPINFOHEADER {
                                    biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                                    biWidth: pitch_px,
                                    biHeight: -bh,
                                    biPlanes: 1,
                                    biBitCount: 32,
                                    biCompression: BI_RGB.0,
                                    ..Default::default()
                                },
                                ..Default::default()
                            };
                            let row_ptr = base_ptr.add((dt.max(0) as usize) * pitch_bytes) as *const c_void;
                            let res = SetDIBitsToDevice(
                                win_hdc,
                                dl - local_left,
                                dt - local_top,
                                bw as u32,
                                bh as u32,
                                dl,
                                0,
                                0,
                                bh as u32,
                                row_ptr,
                                &bmi,
                                DIB_RGB_COLORS,
                            );
                            res > 0
                        };

                        if frame_full {
                            if !blit_slice(local_left, local_top, local_left + win_w, local_top + win_h) {
                                all_ok = false;
                            }
                        } else {
                            let target_box = RECT {
                                left: local_left,
                                top: local_top,
                                right: local_left + win_w,
                                bottom: local_top + win_h,
                            };
                            for sc_r in &draw_scissors {
                                if let Some(ov) = intersect_rect(sc_r, &target_box) {
                                    if !blit_slice(ov.left, ov.top, ov.right, ov.bottom) {
                                        all_ok = false;
                                    }
                                }
                            }
                        }
                        let _ = ReleaseDC(hwnd, win_hdc);
                    }
                    {
                        let ctx = context.lock().unwrap();
                        ctx.0.Unmap(&screen.stg_tex, 0);
                    }
                }

                if all_ok {
                    FRAME_COUNT.fetch_add(1, Ordering::Relaxed);
                    CNT_PRESENT_OK.fetch_add(1, Ordering::Relaxed);
                    topo.screen_has_presented[idx] = true;
                    topo.screen_applied_modes[idx] = cur_mode;
                } else {
                    CNT_PRESENT_ERR.fetch_add(1, Ordering::Relaxed);
                }
            } else {
                CNT_PRESENT_ERR.fetch_add(1, Ordering::Relaxed);
            }
        }

        if !any_rendered {
            if !any_visible_target {
                let lk = shared_notify.0.lock().unwrap();
                if running.load(Ordering::Relaxed) && active.load(Ordering::Relaxed) {
                    let _ = shared_notify.1.wait_timeout(lk, Duration::from_millis(8));
                }
            } else {
                let any_pending_frame = topo.screens.iter().any(|s| s.has_new_frame.load(Ordering::Acquire));
                if !any_pending_frame {
                    let lk = shared_notify.0.lock().unwrap();
                    let still_empty = !topo.screens.iter().any(|s| s.has_new_frame.load(Ordering::Acquire));
                    if still_empty && running.load(Ordering::Relaxed) && active.load(Ordering::Relaxed) {
                        let _ = shared_notify.1.wait_timeout(lk, Duration::from_millis(4));
                    }
                }
            }
        }
    }

    running.store(false, Ordering::SeqCst);
    active.store(false, Ordering::SeqCst);
    println!("[Render] Loop exited. Waiting for capture threads...");
    teardown_topology_state(topo);
    if timer_period_active {
        unsafe {
            let _ = timeEndPeriod(1);
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

fn update_regions_vec(new_regions: Vec<RegionRect>) {
    let has_any = !new_regions.is_empty();
    if let Ok(mut guard) = REGIONS.lock() {
        *guard = new_regions;
    }
    REGION_ENABLED.store(has_any, Ordering::SeqCst);
    REGION_GEN.fetch_add(1, Ordering::Release);
    let session_guard = SESSION.lock().unwrap();
    if let Some(session) = session_guard.as_ref() {
        session.cv.notify_all();
    }
}

#[no_mangle]
pub extern "C" fn dcomp_filter_set_region(x: i32, y: i32, w: i32, h: i32) -> bool {
    if w > 0 && h > 0 {
        update_regions_vec(vec![RegionRect { x, y, w, h }]);
    } else {
        update_regions_vec(Vec::new());
    }
    true
}

#[no_mangle]
pub extern "C" fn dcomp_filter_set_regions(rects: *const RegionRect, count: i32) -> bool {
    if rects.is_null() || count <= 0 {
        update_regions_vec(Vec::new());
        return true;
    }
    let n = (count as usize).min(MAX_REGIONS);
    let slice = unsafe { std::slice::from_raw_parts(rects, n) };
    let valid: Vec<RegionRect> = slice
        .iter()
        .copied()
        .filter(|r| r.w > 0 && r.h > 0)
        .collect();
    update_regions_vec(valid);
    true
}

#[no_mangle]
pub extern "C" fn dcomp_filter_is_active() -> bool {
    let session_guard = SESSION.lock().unwrap();
    if let Some(session) = session_guard.as_ref() {
        session.active.load(Ordering::SeqCst)
            && session.running.load(Ordering::SeqCst)
            && IS_HEALTHY.load(Ordering::SeqCst)
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
    unsafe {
        if let Ok(factory) = CreateDXGIFactory1::<IDXGIFactory1>() {
            let mut idx = 0;
            while let Ok(adapter) = factory.EnumAdapters1(idx) {
                let mut count = 0;
                while adapter.EnumOutputs(count).is_ok() {
                    count += 1;
                }
                if count > 0 {
                    return count as i32;
                }
                idx += 1;
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
    IS_HEALTHY.store(true, Ordering::SeqCst);
    println!("[Cleanup] Completed.");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compute_screen_target_rects_pseudo_fullscreen_multi_monitor() {
        let mut out = Vec::new();
        let mon0 = RECT { left: 0, top: 0, right: 2560, bottom: 1600 };
        let mon1 = RECT { left: 2560, top: 0, right: 4480, bottom: 1080 };
        let mon2 = RECT { left: -1920, top: -1080, right: 0, bottom: 0 };

        // Target all (-1): each monitor gets its own pseudo-fullscreen rect with a small bottom strip left uncovered
        compute_screen_target_rects(&mon0, 0, -1, false, &[], &mut out);
        assert_eq!(out, vec![(0, 0, 2560, 1597, 0, 0)]);

        compute_screen_target_rects(&mon1, 1, -1, false, &[], &mut out);
        assert_eq!(out, vec![(2560, 0, 1920, 1078, 0, 0)]);

        compute_screen_target_rects(&mon2, 2, -1, false, &[], &mut out);
        assert_eq!(out, vec![(-1920, -1080, 1920, 1078, 0, 0)]);

        // Targeted single screen (1) hides screen 0
        compute_screen_target_rects(&mon0, 0, 1, false, &[], &mut out);
        assert!(out.is_empty());
        compute_screen_target_rects(&mon1, 1, 1, false, &[], &mut out);
        assert_eq!(out, vec![(2560, 0, 1920, 1078, 0, 0)]);
    }

    #[test]
    fn test_compute_screen_target_rects_multi_region() {
        let mut out = Vec::new();
        let mon0 = RECT { left: 0, top: 0, right: 1920, bottom: 1080 };
        let mon1 = RECT { left: 1920, top: 0, right: 3840, bottom: 1080 };
        let regions = vec![
            RegionRect { x: 100, y: 100, w: 500, h: 400 },
            RegionRect { x: 1700, y: 200, w: 600, h: 300 }, // spans mon0 & mon1
        ];
        compute_screen_target_rects(&mon0, 0, -1, true, &regions, &mut out);
        assert_eq!(out, vec![
            (100, 100, 500, 400, 100, 100),
            (1700, 200, 220, 300, 1700, 200),
        ]);
        compute_screen_target_rects(&mon1, 1, -1, true, &regions, &mut out);
        assert_eq!(out, vec![
            (1920, 200, 380, 300, 0, 200),
        ]);
    }

    #[test]
    fn test_dirty_rect_intersection_and_pending_accumulation() {
        let a = RECT { left: 100, top: 100, right: 400, bottom: 300 };
        let b = RECT { left: 250, top: 200, right: 600, bottom: 500 };
        assert_eq!(intersect_rect(&a, &b), Some(RECT { left: 250, top: 200, right: 400, bottom: 300 }));
        let disjoint = RECT { left: 500, top: 100, right: 700, bottom: 200 };
        assert_eq!(intersect_rect(&a, &disjoint), None);

        let mut pd = PendingDirty::default();
        for i in 0..20 {
            pd.push_rect(RECT { left: i * 10, top: 0, right: i * 10 + 5, bottom: 20 });
        }
        let (full, rects) = pd.take();
        assert!(!full);
        assert_eq!(rects.len(), MAX_DIRTY_RECTS);
        assert_eq!(rects.last().unwrap().right, 19 * 10 + 5);

        pd.push_rect(a);
        pd.push_full();
        pd.push_rect(b);
        let (full2, rects2) = pd.take();
        assert!(full2);
        assert!(rects2.is_empty());
    }

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
                let out_name = out_name.trim_matches('\0');
                println!("Testing Output [{}]: {} ({:?})", out_idx, out_name, out_desc.DesktopCoordinates);

                let output1: IDXGIOutput1 = output.cast().unwrap();
                match output1.DuplicateOutput(&dev) {
                    Ok(dup) => {
                        println!("  DuplicateOutput: SUCCESS on Output [{}]!", out_idx);
                        let mut _frame_count = 0;
                        let start = std::time::Instant::now();
                        while start.elapsed() < Duration::from_secs(2) {
                            let mut f_info = DXGI_OUTDUPL_FRAME_INFO::default();
                            let mut d_res = None;
                            match dup.AcquireNextFrame(20, &mut f_info, &mut d_res) {
                                Ok(_) => {
                                    _frame_count += 1;
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
