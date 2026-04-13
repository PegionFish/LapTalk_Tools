from __future__ import annotations

import atexit
import ctypes
import os
import struct
import threading
import uuid
from ctypes import wintypes


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GMEM_MOVEABLE = 0x0002
GDIPLUS_OK = 0
PIXEL_FORMAT_32BPP_ARGB = 0x26200A
INTERPOLATION_MODE_HIGH_QUALITY_BICUBIC = 7
PIXEL_OFFSET_MODE_HIGH_QUALITY = 2
COMPOSITING_QUALITY_HIGH_QUALITY = 2
PNG_ENCODER_UUID = uuid.UUID("{557CF406-1A04-11D3-9A73-0000F81EF32E}")

_gdiplus_lock = threading.Lock()
_gdiplus_token = ctypes.c_size_t(0)


def get_png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    if len(png_bytes) < 24 or not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("预览图片不是有效的 PNG 数据。")
    if png_bytes[12:16] != b"IHDR":
        raise ValueError("PNG 数据缺少 IHDR 头。")
    return struct.unpack(">II", png_bytes[16:24])


def resize_png_bytes(png_bytes: bytes, target_width: int, target_height: int) -> bytes:
    if target_width <= 0 or target_height <= 0:
        raise ValueError("预览缩放尺寸必须大于 0。")

    source_size = get_png_dimensions(png_bytes)
    if source_size == (target_width, target_height):
        return png_bytes

    if os.name != "nt":
        raise OSError("Viewer 缩放仅在 Windows 环境下可用。")

    _ensure_gdiplus_started()

    source_image = ctypes.c_void_p()
    target_image = ctypes.c_void_p()
    graphics = ctypes.c_void_p()
    source_stream = _MemoryStream.from_bytes(png_bytes)
    target_stream = _MemoryStream.empty()

    try:
        _check_gdip_status(
            _gdiplus.GdipCreateBitmapFromStream(source_stream.pointer, ctypes.byref(source_image)),
            "从 PNG 创建位图",
        )
        _check_gdip_status(
            _gdiplus.GdipCreateBitmapFromScan0(
                target_width,
                target_height,
                0,
                PIXEL_FORMAT_32BPP_ARGB,
                None,
                ctypes.byref(target_image),
            ),
            "创建目标位图",
        )
        _check_gdip_status(
            _gdiplus.GdipGetImageGraphicsContext(target_image, ctypes.byref(graphics)),
            "创建绘图上下文",
        )
        _check_gdip_status(
            _gdiplus.GdipSetInterpolationMode(graphics, INTERPOLATION_MODE_HIGH_QUALITY_BICUBIC),
            "设置插值模式",
        )
        _check_gdip_status(
            _gdiplus.GdipSetPixelOffsetMode(graphics, PIXEL_OFFSET_MODE_HIGH_QUALITY),
            "设置像素偏移模式",
        )
        _check_gdip_status(
            _gdiplus.GdipSetCompositingQuality(graphics, COMPOSITING_QUALITY_HIGH_QUALITY),
            "设置合成质量",
        )
        _check_gdip_status(
            _gdiplus.GdipGraphicsClear(graphics, 0x00000000),
            "清空目标画布",
        )
        _check_gdip_status(
            _gdiplus.GdipDrawImageRectI(graphics, source_image, 0, 0, target_width, target_height),
            "缩放绘制 PNG",
        )
        _check_gdip_status(
            _gdiplus.GdipSaveImageToStream(target_image, target_stream.pointer, ctypes.byref(PNG_ENCODER_CLSID), None),
            "写出缩放后的 PNG",
        )
        return target_stream.read_png_bytes()
    finally:
        if graphics:
            _gdiplus.GdipDeleteGraphics(graphics)
        if target_image:
            _gdiplus.GdipDisposeImage(target_image)
        if source_image:
            _gdiplus.GdipDisposeImage(source_image)
        target_stream.close()
        source_stream.close()


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> GUID:
        return cls(
            value.fields[0],
            value.fields[1],
            value.fields[2],
            (ctypes.c_ubyte * 8)(*value.bytes[8:16]),
        )


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", wintypes.BOOL),
        ("SuppressExternalCodecs", wintypes.BOOL),
    ]


class IUnknown(ctypes.Structure):
    pass


ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.POINTER(IUnknown))


class IUnknownVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.c_void_p),
        ("AddRef", ctypes.c_void_p),
        ("Release", ReleaseProto),
    ]


IUnknown._fields_ = [("lpVtbl", ctypes.POINTER(IUnknownVtbl))]


PNG_ENCODER_CLSID = GUID.from_uuid(PNG_ENCODER_UUID)


if os.name == "nt":
    _gdiplus = ctypes.windll.gdiplus
    _kernel32 = ctypes.windll.kernel32
    _ole32 = ctypes.windll.ole32

    _gdiplus.GdiplusStartup.argtypes = [
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(GdiplusStartupInput),
        ctypes.c_void_p,
    ]
    _gdiplus.GdiplusStartup.restype = ctypes.c_int
    _gdiplus.GdiplusShutdown.argtypes = [ctypes.c_size_t]
    _gdiplus.GdiplusShutdown.restype = None
    _gdiplus.GdipCreateBitmapFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _gdiplus.GdipCreateBitmapFromStream.restype = ctypes.c_int
    _gdiplus.GdipCreateBitmapFromScan0.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _gdiplus.GdipCreateBitmapFromScan0.restype = ctypes.c_int
    _gdiplus.GdipGetImageGraphicsContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _gdiplus.GdipGetImageGraphicsContext.restype = ctypes.c_int
    _gdiplus.GdipSetInterpolationMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _gdiplus.GdipSetInterpolationMode.restype = ctypes.c_int
    _gdiplus.GdipSetPixelOffsetMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _gdiplus.GdipSetPixelOffsetMode.restype = ctypes.c_int
    _gdiplus.GdipSetCompositingQuality.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _gdiplus.GdipSetCompositingQuality.restype = ctypes.c_int
    _gdiplus.GdipGraphicsClear.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _gdiplus.GdipGraphicsClear.restype = ctypes.c_int
    _gdiplus.GdipDrawImageRectI.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    _gdiplus.GdipDrawImageRectI.restype = ctypes.c_int
    _gdiplus.GdipSaveImageToStream.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
    ]
    _gdiplus.GdipSaveImageToStream.restype = ctypes.c_int
    _gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
    _gdiplus.GdipDeleteGraphics.restype = ctypes.c_int
    _gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]
    _gdiplus.GdipDisposeImage.restype = ctypes.c_int

    _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalFree.restype = ctypes.c_void_p

    _ole32.CreateStreamOnHGlobal.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.POINTER(ctypes.c_void_p)]
    _ole32.CreateStreamOnHGlobal.restype = ctypes.c_long
    _ole32.GetHGlobalFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _ole32.GetHGlobalFromStream.restype = ctypes.c_long


class _MemoryStream:
    def __init__(self, pointer: ctypes.c_void_p) -> None:
        self.pointer = pointer

    @classmethod
    def empty(cls) -> _MemoryStream:
        stream_pointer = ctypes.c_void_p()
        _check_hresult(
            _ole32.CreateStreamOnHGlobal(None, True, ctypes.byref(stream_pointer)),
            "创建内存流",
        )
        return cls(stream_pointer)

    @classmethod
    def from_bytes(cls, raw_bytes: bytes) -> _MemoryStream:
        byte_count = max(len(raw_bytes), 1)
        memory_handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_count)
        if not memory_handle:
            raise MemoryError("无法为预览图片分配 Windows 内存。")

        memory_pointer = _kernel32.GlobalLock(memory_handle)
        if not memory_pointer:
            _kernel32.GlobalFree(memory_handle)
            raise MemoryError("无法锁定 Windows 内存块。")

        try:
            ctypes.memmove(memory_pointer, raw_bytes, len(raw_bytes))
        finally:
            _kernel32.GlobalUnlock(memory_handle)

        stream_pointer = ctypes.c_void_p()
        try:
            _check_hresult(
                _ole32.CreateStreamOnHGlobal(memory_handle, True, ctypes.byref(stream_pointer)),
                "从 PNG 创建内存流",
            )
        except Exception:
            _kernel32.GlobalFree(memory_handle)
            raise
        return cls(stream_pointer)

    def read_png_bytes(self) -> bytes:
        memory_handle = ctypes.c_void_p()
        _check_hresult(
            _ole32.GetHGlobalFromStream(self.pointer, ctypes.byref(memory_handle)),
            "读取 PNG 内存流",
        )
        byte_count = _kernel32.GlobalSize(memory_handle)
        memory_pointer = _kernel32.GlobalLock(memory_handle)
        if not memory_pointer:
            raise MemoryError("无法访问缩放后的 PNG 内存。")
        try:
            raw_bytes = ctypes.string_at(memory_pointer, byte_count)
        finally:
            _kernel32.GlobalUnlock(memory_handle)
        return _trim_png_bytes(raw_bytes)

    def close(self) -> None:
        if not self.pointer:
            return
        _release_com_object(self.pointer)
        self.pointer = ctypes.c_void_p()


def _check_hresult(result: int, action: str) -> None:
    if result != 0:
        raise OSError(f"{action}失败，HRESULT=0x{result & 0xFFFFFFFF:08X}。")


def _check_gdip_status(result: int, action: str) -> None:
    if result != GDIPLUS_OK:
        raise OSError(f"{action}失败，GDI+ 状态码={result}。")


def _release_com_object(pointer: ctypes.c_void_p) -> None:
    unknown = ctypes.cast(pointer, ctypes.POINTER(IUnknown))
    unknown.contents.lpVtbl.contents.Release(unknown)


def _ensure_gdiplus_started() -> None:
    with _gdiplus_lock:
        if _gdiplus_token.value:
            return
        startup_input = GdiplusStartupInput(1, None, False, False)
        _check_gdip_status(
            _gdiplus.GdiplusStartup(ctypes.byref(_gdiplus_token), ctypes.byref(startup_input), None),
            "启动 GDI+",
        )
        atexit.register(_shutdown_gdiplus)


def _shutdown_gdiplus() -> None:
    with _gdiplus_lock:
        if not _gdiplus_token.value:
            return
        _gdiplus.GdiplusShutdown(_gdiplus_token)
        _gdiplus_token.value = 0


def _trim_png_bytes(raw_bytes: bytes) -> bytes:
    if not raw_bytes.startswith(PNG_SIGNATURE):
        return raw_bytes

    cursor = len(PNG_SIGNATURE)
    while cursor + 8 <= len(raw_bytes):
        chunk_length = int.from_bytes(raw_bytes[cursor:cursor + 4], "big")
        chunk_end = cursor + 12 + chunk_length
        if chunk_end > len(raw_bytes):
            return raw_bytes
        if raw_bytes[cursor + 4:cursor + 8] == b"IEND":
            return raw_bytes[:chunk_end]
        cursor = chunk_end

    return raw_bytes
