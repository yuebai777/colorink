#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAI2 颜色同步工具
通过内存读写与 SAI2 同步画笔颜色
支持所有版本 - 使用特征码扫描自动查找颜色地址
"""

import ctypes
from ctypes import wintypes
import os
import sys
import json
import time
import struct

# Windows API 常量
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10

# 版本化特征码
# pre-2024:   B9 03 00 00 00 88 05 ?? ?? ?? ??
# after-2024: E8 ?? ?? ?? ?? B9 01 00 00 00 88 05 ?? ?? ?? ??
DEFAULT_SYNC_VERSION = "pre-2024-sai2"

SIGNATURES = {
    "pre-2024-sai2": {
        "name": "pre-2024-sai2",
        "pattern": [0xB9, 0x03, 0x00, 0x00, 0x00, 0x88, 0x05, None, None, None, None],
        "disp_offset": 7,        # [rip+disp32] starts here
        "next_rip_offset": 11,   # RIP right after instruction
    },
    "after-2024-sai2": {
        "name": "after-2024-sai2",
        "pattern": [0xE8, None, None, None, None, 0xB9, 0x01, 0x00, 0x00, 0x00, 0x88, 0x05, None, None, None, None],
        "disp_offset": 12,       # [rip+disp32] starts here
        "next_rip_offset": 16,   # RIP right after instruction
    },
}

# 备用：已知版本的固定偏移（如果特征码扫描失败）
KNOWN_OFFSETS = {
    # 版本: 偏移
    'default': 0x303DC0,  # 2021.5.28 版本
}

# Windows API 结构
class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260)
    ]

class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260)
    ]

kernel32 = ctypes.windll.kernel32

# 全局缓存
_cached_handle = None
_cached_pid = None
_cached_color_addr = None
_cached_base = None
_cached_size = None
_sync_version = None

DEBUG = False

def log(msg, file=sys.stderr):
    if DEBUG:
        print(f"[SAI2Sync] {msg}", file=file, flush=True)

def normalize_sync_version(version):
    s = str(version or "").strip().lower()
    if s in (
        "after-2024-sai2",
        "after2024sai2",
        "after-2024",
        "after2024",
        "sfter2024sai2",
    ):
        return "after-2024-sai2"
    return "pre-2024-sai2"

def get_sync_version():
    global _sync_version
    if not _sync_version:
        _sync_version = normalize_sync_version(os.environ.get("SAI2_SYNC_VERSION", DEFAULT_SYNC_VERSION))
    return _sync_version

def reset_cache(close_handle=True):
    global _cached_handle, _cached_pid, _cached_color_addr, _cached_base, _cached_size
    if close_handle and _cached_handle:
        try:
            kernel32.CloseHandle(_cached_handle)
        except Exception:
            pass
    _cached_handle = None
    _cached_pid = None
    _cached_color_addr = None
    _cached_base = None
    _cached_size = None

def set_sync_version(version):
    global _sync_version
    normalized = normalize_sync_version(version)
    changed = normalized != get_sync_version()
    _sync_version = normalized
    if changed:
        reset_cache(close_handle=True)
    log(f"Using signature mode: {normalized}")
    return normalized

def find_process(name):
    """查找进程 PID"""
    name_lower = name.lower().encode('utf-8')
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    result = None
    if kernel32.Process32First(snapshot, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == name_lower:
                result = pe.th32ProcessID
                break
            if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(snapshot)
    return result

def get_module_info(pid, module_name):
    """获取模块基址和大小"""
    name_lower = module_name.lower().encode('utf-8')
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snapshot == -1 or snapshot == 0xFFFFFFFF:
        return None, None

    me = MODULEENTRY32()
    me.dwSize = ctypes.sizeof(MODULEENTRY32)

    base_addr = None
    mod_size = None
    if kernel32.Module32First(snapshot, ctypes.byref(me)):
        while True:
            if me.szModule.lower() == name_lower:
                base_addr = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value
                mod_size = me.modBaseSize
                break
            if not kernel32.Module32Next(snapshot, ctypes.byref(me)):
                break

    kernel32.CloseHandle(snapshot)
    return base_addr, mod_size

def read_memory(handle, address, size):
    """读取进程内存"""
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t()

    if kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read)):
        return buffer.raw[:bytes_read.value]
    return None

def write_memory(handle, address, data):
    """写入进程内存"""
    buffer = ctypes.create_string_buffer(bytes(data))
    bytes_written = ctypes.c_size_t()
    result = kernel32.WriteProcessMemory(handle, ctypes.c_void_p(address), buffer, len(data), ctypes.byref(bytes_written))
    return result != 0

def find_pattern_masked(handle, base, size, pattern):
    """在内存中搜索带通配符的特征码（None = wildcard）"""
    CHUNK_SIZE = 0x100000  # 1MB
    pattern_len = len(pattern)
    if pattern_len <= 0:
        return None

    fixed_indices = [i for i, b in enumerate(pattern) if b is not None]
    if not fixed_indices:
        return None
    anchor_idx = fixed_indices[0]
    anchor_byte = pattern[anchor_idx]
    step = max(1, CHUNK_SIZE - pattern_len)

    for offset in range(0, size, step):
        read_size = min(CHUNK_SIZE, size - offset)
        if read_size < pattern_len:
            continue
        data = read_memory(handle, base + offset, read_size)
        if not data:
            continue

        start = 0
        while True:
            pos = data.find(bytes([anchor_byte]), start)
            if pos == -1:
                break

            cand = pos - anchor_idx
            if cand < 0 or cand + pattern_len > len(data):
                start = pos + 1
                continue

            ok = True
            for i in fixed_indices:
                if data[cand + i] != pattern[i]:
                    ok = False
                    break
            if ok:
                return base + offset + cand

            start = pos + 1

    return None

def find_color_address_by_signature(handle, base, size, signature):
    """通过版本对应的特征码扫描查找颜色地址"""
    pattern_addr = find_pattern_masked(handle, base, size, signature["pattern"])
    if not pattern_addr:
        return None

    rel_addr_pos = pattern_addr + signature["disp_offset"]
    rel_addr_data = read_memory(handle, rel_addr_pos, 4)
    if not rel_addr_data or len(rel_addr_data) != 4:
        return None

    rel_addr = struct.unpack('<i', rel_addr_data)[0]
    color_addr = pattern_addr + signature["next_rip_offset"] + rel_addr
    log(
        f"Pattern matched ({signature['name']}) at 0x{pattern_addr:X}, "
        f"color address: 0x{color_addr:X}"
    )
    return color_addr

def connect_sai2():
    """连接到 SAI2 进程"""
    global _cached_handle, _cached_pid, _cached_color_addr, _cached_base, _cached_size

    # 检查缓存是否有效
    if _cached_handle and _cached_color_addr:
        try:
            color = read_memory(_cached_handle, _cached_color_addr, 3)
            if color and len(color) == 3:
                return True
        except Exception:
            pass
        reset_cache(close_handle=True)

    # 查找 SAI2 进程
    pid = find_process('sai2.exe')
    if not pid:
        return False

    # 打开进程
    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
        False, pid
    )
    if not handle:
        return False

    # 获取模块信息
    base, size = get_module_info(pid, 'sai2.exe')
    if not base:
        kernel32.CloseHandle(handle)
        return False

    version = get_sync_version()
    signature = SIGNATURES.get(version, SIGNATURES[DEFAULT_SYNC_VERSION])
    log(f"Scanning with signature mode: {signature['name']}")

    # 方法1: 按版本特征码扫描
    color_addr = find_color_address_by_signature(handle, base, size, signature)

    # 方法2: 如果特征码扫描失败，使用默认偏移
    if not color_addr:
        log(f"Pattern scan failed for {signature['name']}, using default offset")
        color_addr = base + KNOWN_OFFSETS['default']

    # 验证地址可读
    test = read_memory(handle, color_addr, 3)
    if not test or len(test) != 3:
        kernel32.CloseHandle(handle)
        return False

    _cached_handle = handle
    _cached_pid = pid
    _cached_color_addr = color_addr
    _cached_base = base
    _cached_size = size

    log(f"Connected to SAI2 (PID: {pid}, Version: {version}, Color: 0x{color_addr:X})")
    return True

def get_color():
    """获取 SAI2 当前颜色"""
    global _cached_handle, _cached_color_addr

    if not _cached_handle or not _cached_color_addr:
        if not connect_sai2():
            return None

    try:
        data = read_memory(_cached_handle, _cached_color_addr, 3)
        if data and len(data) == 3:
            # 内存顺序: B, G, R
            return {'r': data[2], 'g': data[1], 'b': data[0]}
    except:
        _cached_handle = None
        _cached_color_addr = None

    return None

def set_color(r, g, b):
    """设置 SAI2 颜色"""
    global _cached_handle, _cached_color_addr

    if not _cached_handle or not _cached_color_addr:
        if not connect_sai2():
            return False

    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))

    try:
        # 内存顺序: B, G, R
        return write_memory(_cached_handle, _cached_color_addr, [b, g, r])
    except:
        _cached_handle = None
        _cached_color_addr = None
        return False


def get_color():
    """获取 SAI2 当前颜色"""
    global _cached_handle, _cached_color_addr

    if not _cached_handle or not _cached_color_addr:
        if not connect_sai2():
            return None

    try:
        data = read_memory(_cached_handle, _cached_color_addr, 3)
        if data and len(data) == 3:
            # 内存顺序: B, G, R
            return {'r': data[2], 'g': data[1], 'b': data[0]}
    except:
        _cached_handle = None
        _cached_color_addr = None

    return None

def set_color(r, g, b):
    """设置 SAI2 颜色"""
    global _cached_handle, _cached_color_addr

    if not _cached_handle or not _cached_color_addr:
        if not connect_sai2():
            return False

    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))

    try:
        # 内存顺序: B, G, R
        return write_memory(_cached_handle, _cached_color_addr, [b, g, r])
    except:
        _cached_handle = None
        _cached_color_addr = None
        return False

def get_status():
    """获取连接状态"""
    global _cached_handle, _cached_pid, _cached_color_addr

    # 如果还没连接，先尝试连接
    if not _cached_handle or not _cached_color_addr:
        connect_sai2()

    connected = _cached_handle is not None and _cached_color_addr is not None
    if connected:
        # 验证连接仍然有效
        color = get_color()
        if color is None:
            connected = False

    return {
        'connected': connected,
        'pid': _cached_pid if connected else None,
        'colorAddr': f'0x{_cached_color_addr:X}' if connected and _cached_color_addr else None,
        'version': get_sync_version()
    }


