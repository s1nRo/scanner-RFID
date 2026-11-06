"""Тонкая обёртка над SetupAPI для driver.py. Только Windows.

Здесь нет решений — только вызовы системы. Что и куда ставить, решает
driver.py. Раскладки структур — из setupapi.h и shellapi.h: в 64-битном
процессе выравнивание 8, в 32-битном — 1 (pshpack1), иначе Windows
отвергает cbSize.
"""

from __future__ import annotations

import ctypes
import subprocess
from collections.abc import Iterator
from ctypes import wintypes as wt

_PACK = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1
_ULONG_PTR = ctypes.c_size_t
_INVALID = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x2
DIGCF_ALLCLASSES = 0x4
SPDRP_HARDWAREID = 0x1
SPDRP_SERVICE = 0x4
SPDRP_CLASSGUID = 0x8
SPDIT_CLASSDRIVER = 0x1
DI_ENUMSINGLEINF = 0x00010000
DI_FLAGSEX_ALLOWEXCLUDEDDRVS = 0x00000800
SPOST_PATH = 1
ERROR_NO_MORE_ITEMS = 259
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_CANCELLED = 1223
CR_SUCCESS = 0
DN_HAS_PROBLEM = 0x400
SEE_MASK_NOCLOSEPROCESS = 0x40
INFINITE = 0xFFFFFFFF


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def key(self) -> tuple:
        return (self.Data1, self.Data2, self.Data3, bytes(self.Data4))


class SP_DEVINFO_DATA(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wt.DWORD),
        ("Reserved", _ULONG_PTR),
    ]


class SP_DEVINSTALL_PARAMS_W(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("Flags", wt.DWORD),
        ("FlagsEx", wt.DWORD),
        ("hwndParent", wt.HWND),
        ("InstallMsgHandler", ctypes.c_void_p),
        ("InstallMsgHandlerContext", ctypes.c_void_p),
        ("FileQueue", ctypes.c_void_p),
        ("ClassInstallReserved", _ULONG_PTR),
        ("Reserved", wt.DWORD),
        ("DriverPath", wt.WCHAR * 260),
    ]


class SP_DRVINFO_DATA_V2_W(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("DriverType", wt.DWORD),
        ("Reserved", _ULONG_PTR),
        ("Description", wt.WCHAR * 256),
        ("MfgName", wt.WCHAR * 256),
        ("ProviderName", wt.WCHAR * 256),
        ("DriverDate", wt.FILETIME),
        ("DriverVersion", ctypes.c_ulonglong),
    ]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("fMask", wt.ULONG),
        ("hwnd", wt.HWND),
        ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR),
        ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wt.LPCWSTR),
        ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD),
        ("hIconOrMonitor", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


def _sized(struct_type):
    item = struct_type()
    item.cbSize = ctypes.sizeof(struct_type)
    return item


_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_newdev = ctypes.WinDLL("newdev", use_last_error=True)
_cfgmgr = ctypes.WinDLL("cfgmgr32")
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32")

_HDEVINFO = ctypes.c_void_p
_PDATA = ctypes.POINTER(SP_DEVINFO_DATA)
_PDRV = ctypes.POINTER(SP_DRVINFO_DATA_V2_W)


def _bind(dll, name, restype, *argtypes):
    func = getattr(dll, name)
    func.restype = restype
    func.argtypes = argtypes
    return func


GetClassDevs = _bind(_setupapi, "SetupDiGetClassDevsW", _HDEVINFO,
                     ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD)
CreateDeviceInfoList = _bind(_setupapi, "SetupDiCreateDeviceInfoList", _HDEVINFO,
                             ctypes.POINTER(GUID), wt.HWND)
DestroyDeviceInfoList = _bind(_setupapi, "SetupDiDestroyDeviceInfoList", wt.BOOL, _HDEVINFO)
EnumDeviceInfo = _bind(_setupapi, "SetupDiEnumDeviceInfo", wt.BOOL,
                       _HDEVINFO, wt.DWORD, _PDATA)
OpenDeviceInfo = _bind(_setupapi, "SetupDiOpenDeviceInfoW", wt.BOOL,
                       _HDEVINFO, wt.LPCWSTR, wt.HWND, wt.DWORD, _PDATA)
GetDeviceInstanceId = _bind(_setupapi, "SetupDiGetDeviceInstanceIdW", wt.BOOL,
                            _HDEVINFO, _PDATA, wt.LPWSTR, wt.DWORD,
                            ctypes.POINTER(wt.DWORD))
GetDeviceRegistryProperty = _bind(
    _setupapi, "SetupDiGetDeviceRegistryPropertyW", wt.BOOL,
    _HDEVINFO, _PDATA, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD))
SetDeviceRegistryProperty = _bind(
    _setupapi, "SetupDiSetDeviceRegistryPropertyW", wt.BOOL,
    _HDEVINFO, _PDATA, wt.DWORD, ctypes.c_void_p, wt.DWORD)
GetDeviceInstallParams = _bind(_setupapi, "SetupDiGetDeviceInstallParamsW", wt.BOOL,
                               _HDEVINFO, _PDATA, ctypes.POINTER(SP_DEVINSTALL_PARAMS_W))
SetDeviceInstallParams = _bind(_setupapi, "SetupDiSetDeviceInstallParamsW", wt.BOOL,
                               _HDEVINFO, _PDATA, ctypes.POINTER(SP_DEVINSTALL_PARAMS_W))
BuildDriverInfoList = _bind(_setupapi, "SetupDiBuildDriverInfoList", wt.BOOL,
                            _HDEVINFO, _PDATA, wt.DWORD)
EnumDriverInfo = _bind(_setupapi, "SetupDiEnumDriverInfoW", wt.BOOL,
                       _HDEVINFO, _PDATA, wt.DWORD, wt.DWORD, _PDRV)
SetSelectedDriver = _bind(_setupapi, "SetupDiSetSelectedDriverW", wt.BOOL,
                          _HDEVINFO, _PDATA, _PDRV)
GetINFClass = _bind(_setupapi, "SetupDiGetINFClassW", wt.BOOL,
                    wt.LPCWSTR, ctypes.POINTER(GUID), wt.LPWSTR, wt.DWORD,
                    ctypes.POINTER(wt.DWORD))
CopyOEMInf = _bind(_setupapi, "SetupCopyOEMInfW", wt.BOOL,
                   wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPWSTR, wt.DWORD,
                   ctypes.POINTER(wt.DWORD), ctypes.c_void_p)
DiInstallDevice = _bind(_newdev, "DiInstallDevice", wt.BOOL,
                        wt.HWND, _HDEVINFO, _PDATA, _PDRV, wt.DWORD,
                        ctypes.POINTER(wt.BOOL))
GetDevNodeStatus = _bind(_cfgmgr, "CM_Get_DevNode_Status", wt.DWORD,
                         ctypes.POINTER(wt.ULONG), ctypes.POINTER(wt.ULONG), wt.DWORD,
                         wt.ULONG)
IsUserAnAdmin = _bind(_shell32, "IsUserAnAdmin", wt.BOOL)
ShellExecuteEx = _bind(_shell32, "ShellExecuteExW", wt.BOOL,
                       ctypes.POINTER(SHELLEXECUTEINFOW))
WaitForSingleObject = _bind(_kernel32, "WaitForSingleObject", wt.DWORD,
                            wt.HANDLE, wt.DWORD)
GetExitCodeProcess = _bind(_kernel32, "GetExitCodeProcess", wt.BOOL,
                           wt.HANDLE, ctypes.POINTER(wt.DWORD))
CloseHandle = _bind(_kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)
StringFromGUID2 = _bind(_ole32, "StringFromGUID2", ctypes.c_int,
                        ctypes.POINTER(GUID), wt.LPWSTR, ctypes.c_int)


def _fail(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{what}: {ctypes.FormatError(code).strip()} (код {code})")


def _check(ok, what: str) -> None:
    if not ok:
        raise _fail(what)


class _DeviceSet:
    def __init__(self, handle) -> None:
        if handle in (None, _INVALID):
            raise _fail("SetupDi: не открылся список устройств")
        self.handle = handle

    def __enter__(self):
        return self.handle

    def __exit__(self, *_exc) -> None:
        DestroyDeviceInfoList(self.handle)


def is_admin() -> bool:
    return bool(IsUserAnAdmin())


def _property(devs, data, prop: int) -> list[str]:
    """Строковое или мульти-строковое свойство узла. Нет свойства — []."""
    size = wt.DWORD(0)
    GetDeviceRegistryProperty(devs, ctypes.byref(data), prop, None, None, 0,
                              ctypes.byref(size))
    if not size.value:
        return []
    buffer = ctypes.create_unicode_buffer(size.value // 2 + 1)
    if not GetDeviceRegistryProperty(devs, ctypes.byref(data), prop, None, buffer,
                                     size.value, None):
        return []
    raw = ctypes.wstring_at(ctypes.addressof(buffer), size.value // 2)
    return [part for part in raw.split("\0") if part]


def _instance_id(devs, data) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    _check(GetDeviceInstanceId(devs, ctypes.byref(data), buffer, len(buffer), None),
           "SetupDiGetDeviceInstanceId")
    return buffer.value


def present_devices() -> Iterator[tuple[str, list[str], bool, int]]:
    """(instance id, hardware ids, есть ли драйвер, код проблемы) всех подключённых."""
    with _DeviceSet(GetClassDevs(None, None, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)) as devs:
        index = 0
        while True:
            data = _sized(SP_DEVINFO_DATA)
            if not EnumDeviceInfo(devs, index, ctypes.byref(data)):
                break
            index += 1
            hwids = _property(devs, data, SPDRP_HARDWAREID)
            if not any("VID_0403" in h.upper() for h in hwids):
                continue  # не FTDI — нам неинтересно
            status, problem = wt.ULONG(0), wt.ULONG(0)
            if GetDevNodeStatus(ctypes.byref(status), ctypes.byref(problem),
                                data.DevInst, 0) != CR_SUCCESS:
                problem.value = 0
            elif not status.value & DN_HAS_PROBLEM:
                problem.value = 0
            has_driver = bool(_property(devs, data, SPDRP_SERVICE))
            yield _instance_id(devs, data), hwids, has_driver, problem.value


def _inf_class(inf: str) -> GUID:
    guid = GUID()
    name = ctypes.create_unicode_buffer(64)
    _check(GetINFClass(inf, ctypes.byref(guid), name, len(name), None),
           f"не читается класс из {inf}")
    return guid


def _guid_text(guid: GUID) -> str:
    buffer = ctypes.create_unicode_buffer(64)
    StringFromGUID2(ctypes.byref(guid), buffer, len(buffer))
    return buffer.value


def _single_inf(devs, data, inf: str) -> None:
    """Список драйверов строить только из этого INF, «несовместимые» разрешить."""
    params = _sized(SP_DEVINSTALL_PARAMS_W)
    _check(GetDeviceInstallParams(devs, data, ctypes.byref(params)),
           "SetupDiGetDeviceInstallParams")
    params.Flags |= DI_ENUMSINGLEINF
    params.FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS
    params.DriverPath = inf
    _check(SetDeviceInstallParams(devs, data, ctypes.byref(params)),
           "SetupDiSetDeviceInstallParams")
    _check(BuildDriverInfoList(devs, data, SPDIT_CLASSDRIVER),
           "SetupDiBuildDriverInfoList")


def _drivers(devs, data) -> Iterator[SP_DRVINFO_DATA_V2_W]:
    index = 0
    while True:
        drv = _sized(SP_DRVINFO_DATA_V2_W)
        if not EnumDriverInfo(devs, data, SPDIT_CLASSDRIVER, index, ctypes.byref(drv)):
            if ctypes.get_last_error() != ERROR_NO_MORE_ITEMS:
                raise _fail("SetupDiEnumDriverInfo")
            return
        index += 1
        yield drv


def models_in(inf: str) -> list[str]:
    """Названия моделей, которые Windows видит в INF. Прав администратора не нужно."""
    guid = _inf_class(inf)
    with _DeviceSet(CreateDeviceInfoList(ctypes.byref(guid), None)) as devs:
        _single_inf(devs, None, inf)
        return sorted({drv.Description for drv in _drivers(devs, None)})


def _stage(inf: str) -> str:
    """Положить пакет в хранилище драйверов. Вернуть путь к его копии (oemNN.inf).

    DiInstallDevice ставит только то, что уже лежит в хранилище; узел драйвера
    поэтому строится из копии, а не из файла на флешке.
    """
    buffer = ctypes.create_unicode_buffer(260)
    _check(CopyOEMInf(inf, None, SPOST_PATH, 0, buffer, len(buffer), None, None),
           f"не удалось добавить {inf} в хранилище драйверов")
    return buffer.value


def force_install(instance_id: str, inf: str, model: str) -> None:
    """Навязать узлу драйвер модели model из inf — как «Установить с диска»."""
    stored = _stage(inf)
    guid = _inf_class(stored)
    with _DeviceSet(CreateDeviceInfoList(None, None)) as devs:
        data = _sized(SP_DEVINFO_DATA)
        _check(OpenDeviceInfo(devs, instance_id, None, 0, ctypes.byref(data)),
               f"устройство {instance_id} не найдено")
        # Неизвестное устройство без класса берёт любой; с чужим классом
        # (остаток прошлой неудачной установки) список был бы пуст.
        if data.ClassGuid.key() not in (GUID().key(), guid.key()):
            text = _guid_text(guid)
            raw = ctypes.create_unicode_buffer(text)
            _check(SetDeviceRegistryProperty(devs, ctypes.byref(data), SPDRP_CLASSGUID,
                                             raw, ctypes.sizeof(raw)),
                   "не удалось сменить класс устройства")
        _single_inf(devs, ctypes.byref(data), stored)
        chosen = None
        found = []
        for drv in _drivers(devs, ctypes.byref(data)):
            found.append(drv.Description)
            if drv.Description == model:
                chosen = drv
                break
        if chosen is None:
            raise OSError(0, f"в {inf} нет модели «{model}» (есть: {', '.join(found)})")
        _check(SetSelectedDriver(devs, ctypes.byref(data), ctypes.byref(chosen)),
               "SetupDiSetSelectedDriver")
        reboot = wt.BOOL(0)
        _check(DiInstallDevice(None, devs, ctypes.byref(data), ctypes.byref(chosen), 0,
                               ctypes.byref(reboot)),
               "DiInstallDevice")


def run_elevated(executable: str, args: list[str], *, wait: bool = True) -> int | None:
    """ShellExecuteEx «runas». None — человек нажал «Нет» в окне UAC."""
    info = _sized(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(args)
    info.nShow = 1  # SW_SHOWNORMAL
    if not ShellExecuteEx(ctypes.byref(info)):
        if ctypes.get_last_error() == ERROR_CANCELLED:
            return None
        raise _fail("не удалось запустить с правами администратора")
    if not info.hProcess:
        return 0
    try:
        if not wait:
            return 0
        WaitForSingleObject(info.hProcess, INFINITE)
        code = wt.DWORD(0)
        GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        CloseHandle(info.hProcess)
