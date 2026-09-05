use std::time::{Duration, Instant};
use windows::core::{w, Interface};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::Graphics::Direct3D::*;
use windows::Win32::Graphics::Direct3D11::*;
use windows::Win32::Graphics::Dxgi::Common::*;
use windows::Win32::Graphics::Dxgi::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::*;

unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

fn main() -> windows::core::Result<()> {
    unsafe {
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
            fn SetProcessInformation(h: isize, c: i32, p: *const std::ffi::c_void, s: u32) -> i32;
            fn timeBeginPeriod(u: u32) -> u32;
        }
        let hdesk = OpenInputDesktop(0, 0, 0x01ff);
        let desk_ok = if hdesk != 0 { SetThreadDesktop(hdesk) } else { 0 };
        println!("SetThreadDesktop to Default: {}", desk_ok);
        let throttling = PROCESS_POWER_THROTTLING_STATE {
            Version: 1,
            ControlMask: 0x1,
            StateMask: 0,
        };
        let _ = SetProcessInformation(
            GetCurrentProcess(),
            4,
            &throttling as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<PROCESS_POWER_THROTTLING_STATE>() as u32,
        );
        let _ = timeBeginPeriod(1);
        let instance = GetModuleHandleW(None)?;
        let class_name = w!("TestPresentClass");
        let wc = WNDCLASSW {
            lpfnWndProc: Some(wnd_proc),
            hInstance: instance.into(),
            lpszClassName: class_name,
            ..Default::default()
        };
        let _ = RegisterClassW(&wc);

        let hwnd = CreateWindowExW(
            WS_EX_NOREDIRECTIONBITMAP | WS_EX_TOPMOST,
            class_name,
            w!("TestPresent"),
            WS_POPUP,
            0, 0, 3840, 2160,
            None, None, instance, None,
        )?;
        let _ = ShowWindow(hwnd, SW_SHOW);
        extern "system" {
            fn AttachThreadInput(idattach: u32, idattachto: u32, fattach: i32) -> i32;
        }
        let fg_hwnd = GetForegroundWindow();
        let fg_tid = GetWindowThreadProcessId(fg_hwnd, None);
        let cur_tid = windows::Win32::System::Threading::GetCurrentThreadId();
        let _ = AttachThreadInput(cur_tid, fg_tid, 1);
        let _ = SetForegroundWindow(hwnd);
        let _ = AttachThreadInput(cur_tid, fg_tid, 0);
        println!("Target HWND: {:?}, Actual Foreground HWND: {:?}", hwnd, GetForegroundWindow());

        let factory: IDXGIFactory2 = CreateDXGIFactory1()?;
        if let Ok(factory5) = factory.cast::<IDXGIFactory5>() {
            let mut allow_tearing: i32 = 0;
            let _ = factory5.CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING, &mut allow_tearing as *mut _ as *mut _, 4);
            println!("ALLOW_TEARING supported: {}", allow_tearing != 0);
        }
        let adapter = factory.EnumAdapters1(0)?;
        let mut device = None;
        let mut context = None;
        let feature_levels = [D3D_FEATURE_LEVEL_11_0];
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
        let context = context.unwrap();
        // dxgi_device.SetMaximumFrameLatency(1)?;
        println!("Skipping SetMaximumFrameLatency");

use windows::Win32::Graphics::DirectComposition::*;

        let dxgi_device: IDXGIDevice = device.cast()?;
        let dcomp_device: IDCompositionDevice = DCompositionCreateDevice(&dxgi_device)?;
        let target = dcomp_device.CreateTargetForHwnd(hwnd, true)?;
        let visual = dcomp_device.CreateVisual()?;

        let sc_desc = DXGI_SWAP_CHAIN_DESC1 {
            Width: 3840,
            Height: 2160,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
            BufferCount: 3,
            Scaling: DXGI_SCALING_STRETCH,
            SwapEffect: DXGI_SWAP_EFFECT_FLIP_DISCARD,
            AlphaMode: DXGI_ALPHA_MODE_PREMULTIPLIED,
            Flags: 0,
            ..Default::default()
        };

        let sc = factory.CreateSwapChainForComposition(&device, &sc_desc, None)?;
        visual.SetContent(&sc)?;
        target.SetRoot(&visual)?;
        dcomp_device.Commit()?;

        let back_buffer: ID3D11Texture2D = sc.GetBuffer(0)?;
        let mut rtv = None;
        device.CreateRenderTargetView(&back_buffer, None, Some(&mut rtv))?;
        let rtv = rtv.unwrap();

        println!("Starting 100 Present calls with CreateSwapChainForComposition...");
        let mut total = Duration::ZERO;
        for i in 0..100 {
            let mut msg = MSG::default();
            while PeekMessageW(&mut msg, hwnd, 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            let color = [0.1f32 * (i as f32 % 10.0), 0.2, 0.3, 1.0];
            context.ClearRenderTargetView(&rtv, &color);
            let t0 = Instant::now();
            let pres = sc.Present(1, DXGI_PRESENT(0));
            let _ = dcomp_device.Commit();
            let dt = t0.elapsed();
            total += dt;
            if i % 10 == 0 {
                println!("Frame {}: {:?} (hr={:?})", i, dt, pres);
            }
        }
        println!("Average Present1 time: {:?}", total / 100);
        let _ = DestroyWindow(hwnd);
        Ok(())
    }
}
