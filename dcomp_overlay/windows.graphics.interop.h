// windows.graphics.interop.h — vendored minimal WGC interop declarations.
//
// The Windows SDK's cppwinrt projection headers (winrt/Windows.Graphics.Capture.h
// etc.) cover the WinRT runtime classes, but this SDK install does NOT ship the
// companion interop headers (windows.graphics.capture.interop.h /
// windows.graphics.directx.direct3d11.interop.h). Those headers only declare
// stable, public-ABI COM interfaces and free functions, so we vendor the exact
// declarations here (IIDs are frozen by Microsoft and documented in the SDK).
//
// Contents:
//   * IGraphicsCaptureItemInterop      — HWND/HMONITOR -> GraphicsCaptureItem
//   * IDirect3DDxgiInterfaceAccess     — IDirect3DSurface  -> DXGI/D3D11 resource
//   * CreateDirect3D11DeviceFromDXGIDevice / GetDXGIInterfaceFromObject
//                                       — WinRT <-> D3D11 device bridge (exported
//                                         by windows.graphics.dll)
#pragma once

#include <windows.h>
#include <inspectable.h>
#include <d3d11.h>
#include <dxgi1_2.h>

// IGraphicsCaptureItemInterop — exposed by the GraphicsCaptureItem activation
// factory; creates a capture item from a window handle or monitor handle.
MIDL_INTERFACE("3628e81b-3cac-4c60-b7f4-23ce0e0c3356")
IGraphicsCaptureItemInterop : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE CreateForWindow(
        _In_ HWND hwnd, _In_ REFIID riid, _COM_Outptr_ void** result) = 0;
    virtual HRESULT STDMETHODCALLTYPE CreateForMonitor(
        _In_ HMONITOR hmonitor, _In_ REFIID riid, _COM_Outptr_ void** result) = 0;
};

// IDirect3DDxgiInterfaceAccess — implemented by WGC surfaces; resolves the
// Windows.Graphics.DirectX.Direct3D11.IDirect3DSurface handed out per frame to
// its underlying ID3D11Texture2D (zero-copy GPU access).
MIDL_INTERFACE("a9b3d012-3df2-4ee3-b8d1-8695f457d3c1")
IDirect3DDxgiInterfaceAccess : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE GetInterface(
        _In_ REFIID iid, _COM_Outptr_ void** object) = 0;
};

// IGraphicsCaptureSession5 — Win11 24H2+ (Build 26100+). Adds the
// MinUpdateInterval property: setting it to 0 makes WGC deliver frames at the
// compositor cadence REGARDLESS of content changes — the "constant refresh"
// the overlay needs on a static desktop. Declared here because the 22621 SDK
// projection predates it. TimeSpan ABI is a plain int64 (100ns units).
// NOTE: this is a WinRT interface — it must inherit IInspectable (not IUnknown)
// so the vtable slots line up (IInspectable adds 3 methods before the property).
// (Verified against microsoft/win32metadata windows.graphics.capture.h:
//  Session3 = IsBorderRequired, Session4 = DirtyRegionMode, Session5 = MinUpdateInterval.)
MIDL_INTERFACE("67c0ea62-1f85-5061-925a-239be0ac09cb")
IGraphicsCaptureSession5 : public ::IInspectable {
public:
    virtual HRESULT STDMETHODCALLTYPE get_MinUpdateInterval(int64_t* value) = 0;
    virtual HRESULT STDMETHODCALLTYPE put_MinUpdateInterval(int64_t value) = 0;
};

// Bridges an IDXGIDevice to the WinRT IDirect3DDevice used to create the WGC
// frame pool. Exported by d3d11.dll; declared here because the SDK's
// direct3d11.interop.h is not installed. (The sibling free function
// GetDXGIInterfaceFromObject no longer exists on modern Windows — use
// IDirect3DDxgiInterfaceAccess::GetInterface on the frame surface instead.)
EXTERN_C HRESULT WINAPI CreateDirect3D11DeviceFromDXGIDevice(
    _In_ IDXGIDevice* dxgiDevice, _COM_Outptr_ ::IInspectable** graphicsSurface);
