# -*- coding: utf-8 -*-
"""平台相关工具函数。"""

import sys
import subprocess
from typing import Tuple, List, Dict, Optional


def get_windows_version() -> Tuple[int, int, int]:
    """获取 Windows 版本号。
    
    Returns:
        (major, minor, build) 版本号元组，非 Windows 返回 (0, 0, 0)
    """
    if sys.platform != "win32":
        return (0, 0, 0)
    
    try:
        version = sys.getwindowsversion()
        return (version.major, version.minor, version.build)
    except Exception:
        return (0, 0, 0)


def is_windows() -> bool:
    """检查是否为 Windows 系统。"""
    return sys.platform == "win32"


def is_windows_10_or_later() -> bool:
    """检查是否为 Windows 10 或更高版本。
    
    Windows 10 和 Windows 11 的 major 版本号都是 10。
    """
    if not is_windows():
        return False
    
    major, _, _ = get_windows_version()
    return major >= 10


def is_windows_11() -> bool:
    """检查是否为 Windows 11。
    
    Windows 11 的版本号为 10.0.22000 及以上。
    """
    if not is_windows():
        return False
    
    major, _, build = get_windows_version()
    return major >= 10 and build >= 22000


def is_macos() -> bool:
    """检查是否为 macOS 系统。"""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """检查是否为 Linux 系统。"""
    return sys.platform.startswith("linux")


def supports_file_drop() -> bool:
    """检查当前系统是否支持文件拖放功能。
    
    支持 Windows 10/11 和 macOS。
    """
    return is_windows_10_or_later() or is_macos()


def get_gpu_devices() -> List[Dict[str, str]]:
    """获取系统中所有 GPU 设备信息。
    
    跨平台支持 Windows、macOS、Linux。
    
    Returns:
        GPU 设备列表，每个设备包含:
        - index: 设备索引
        - name: 设备名称
        - vendor: 厂商（NVIDIA/AMD/Intel/Apple 等）
    """
    gpus = []
    
    if is_windows():
        gpus = _get_gpu_devices_windows()
    elif is_macos():
        gpus = _get_gpu_devices_macos()
    elif is_linux():
        gpus = _get_gpu_devices_linux()
    
    return gpus if gpus else [{"index": 0, "name": "Unknown GPU", "vendor": "Unknown"}]


def get_cuda_devices() -> List[Dict[str, str]]:
    """获取 CUDA 可见的 GPU 设备列表（仅 NVIDIA GPU）。
    
    使用 nvidia-smi 获取设备信息，确保 device_id 与 CUDA 一致。
    
    Returns:
        CUDA 设备列表，每个设备包含:
        - index: CUDA device_id
        - name: GPU 名称
        - vendor: "NVIDIA"
    """
    gpus = []
    
    # 使用 nvidia-smi 获取 CUDA 设备列表
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                parts = line.split(', ')
                if len(parts) >= 2:
                    idx = int(parts[0].strip())
                    name = parts[1].strip()
                    gpus.append({
                        "index": idx,
                        "name": name,
                        "vendor": "NVIDIA",
                    })
            if gpus:
                return gpus
    except Exception:
        pass
    
    # 回退：从 WMI 过滤 NVIDIA GPU（顺序可能不准确）
    all_gpus = get_gpu_devices()
    nvidia_gpus = [g for g in all_gpus if g.get("vendor") == "NVIDIA"]
    for i, gpu in enumerate(nvidia_gpus):
        gpus.append({
            "index": i,
            "name": gpu["name"],
            "vendor": "NVIDIA",
        })
    
    return gpus


def _is_virtual_adapter(name: str) -> bool:
    """检查是否为虚拟显示适配器（应排除）。
    
    Args:
        name: GPU 名称
        
    Returns:
        True 表示是虚拟适配器，应排除
    """
    name_upper = name.upper()
    
    # 虚拟显示适配器关键词
    virtual_keywords = [
        "VIRTUAL",
        "REMOTE DESKTOP",
        "BASIC DISPLAY",
        "MICROSOFT BASIC",
        "VMWARE",
        "VIRTUALBOX",
        "PARSEC",
        "SUNSHINE",
        "GAMEVIEWER",
        "SPACEDESK",
        "DUET",
        "SPLASHTOP",
        "ANYDESK",
        "IDD",  # Indirect Display Driver
    ]
    
    for keyword in virtual_keywords:
        if keyword in name_upper:
            return True
    
    return False


def _get_gpu_devices_windows() -> List[Dict[str, str]]:
    """Windows 平台获取 GPU 设备信息（排除虚拟适配器）。"""
    gpus = []
    all_adapters = []
    
    # 方法1: 使用 WMI (需要 wmi 库)
    try:
        import wmi
        c = wmi.WMI()
        for gpu in c.Win32_VideoController():
            name = gpu.Name or "Unknown GPU"
            all_adapters.append({
                "name": name,
                "vendor": _detect_vendor(name, gpu.AdapterCompatibility or ""),
            })
    except ImportError:
        pass
    except Exception:
        pass
    
    # 方法2: 使用 PowerShell 作为备用方案
    if not all_adapters:
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-WmiObject Win32_VideoController | Select-Object Name, AdapterCompatibility | ConvertTo-Json"
                ],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                data = json.loads(result.stdout)
                # 单个设备时返回的是 dict，多个设备时是 list
                if isinstance(data, dict):
                    data = [data]
                for gpu in data:
                    name = gpu.get("Name", "Unknown GPU")
                    vendor = _detect_vendor(name, gpu.get("AdapterCompatibility", ""))
                    all_adapters.append({
                        "name": name,
                        "vendor": vendor,
                    })
        except Exception:
            pass
    
    # 过滤掉虚拟适配器，重新分配索引
    for adapter in all_adapters:
        if not _is_virtual_adapter(adapter["name"]):
            gpus.append({
                "index": len(gpus),  # 使用过滤后的索引
                "name": adapter["name"],
                "vendor": adapter["vendor"],
            })
    
    return gpus


def _get_gpu_devices_macos() -> List[Dict[str, str]]:
    """macOS 平台获取 GPU 设备信息。"""
    gpus = []
    
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            data = json.loads(result.stdout)
            for i, display in enumerate(data.get("SPDisplaysDataType", [])):
                name = display.get("sppci_model", "Unknown GPU")
                vendor = display.get("spdisplays_vendor", "")
                if not vendor:
                    vendor = "Apple" if "Apple" in name or "M1" in name or "M2" in name or "M3" in name else "Unknown"
                gpus.append({
                    "index": i,
                    "name": name,
                    "vendor": vendor,
                })
    except Exception:
        pass
    
    return gpus


def _get_gpu_devices_linux() -> List[Dict[str, str]]:
    """Linux 平台获取 GPU 设备信息。"""
    gpus = []
    
    try:
        result = subprocess.run(
            ["lspci", "-mm"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                # 过滤 VGA/Display/3D 控制器
                if "VGA" in line or "Display" in line or "3D" in line:
                    # lspci -mm 格式: slot "class" "vendor" "device" ...
                    parts = line.split('"')
                    if len(parts) >= 6:
                        vendor_str = parts[3] if len(parts) > 3 else ""
                        name = parts[5] if len(parts) > 5 else "Unknown GPU"
                        vendor = _detect_vendor(name, vendor_str)
                        gpus.append({
                            "index": len(gpus),
                            "name": name,
                            "vendor": vendor,
                        })
    except Exception:
        pass
    
    return gpus


def _detect_vendor(name: str, adapter_compatibility: str = "") -> str:
    """检测 GPU 厂商。"""
    name_upper = name.upper()
    compat_upper = adapter_compatibility.upper()
    
    if "NVIDIA" in name_upper or "NVIDIA" in compat_upper or "GEFORCE" in name_upper or "RTX" in name_upper or "GTX" in name_upper:
        return "NVIDIA"
    elif "AMD" in name_upper or "AMD" in compat_upper or "RADEON" in name_upper or "RX " in name_upper:
        return "AMD"
    elif "INTEL" in name_upper or "INTEL" in compat_upper or "UHD" in name_upper or "IRIS" in name_upper or "ARC" in name_upper:
        return "Intel"
    elif "APPLE" in name_upper or "M1" in name_upper or "M2" in name_upper or "M3" in name_upper:
        return "Apple"
    else:
        return adapter_compatibility if adapter_compatibility else "Unknown"


def get_available_compute_devices() -> Dict[str, List[Dict]]:
    """获取可用的计算设备信息（结合 ONNX Runtime Provider 和实际硬件）。
    
    Returns:
        包含以下键的字典:
        - cpu: CPU 信息
        - gpus: GPU 设备列表（带有可用的加速方式）
        - available_providers: ONNX Runtime 可用的 Provider 列表
        - recommended_provider: 推荐使用的 Provider
    """
    result = {
        "cpu": {"name": "CPU", "available": True},
        "gpus": [],
        "available_providers": [],
        "recommended_provider": "CPUExecutionProvider",
    }
    
    # 获取 ONNX Runtime 可用的 Providers
    try:
        import onnxruntime as ort
        result["available_providers"] = ort.get_available_providers()
    except ImportError:
        result["available_providers"] = ["CPUExecutionProvider"]
    
    # 获取实际的 GPU 设备
    gpus = get_gpu_devices()
    
    # 确定每个 GPU 可用的加速方式
    providers = result["available_providers"]
    
    for gpu in gpus:
        vendor = gpu["vendor"]
        gpu_info = {
            "index": gpu["index"],
            "name": gpu["name"],
            "vendor": vendor,
            "acceleration": [],
        }
        
        # 根据厂商和可用的 Provider 确定加速方式
        if vendor == "NVIDIA":
            if "CUDAExecutionProvider" in providers:
                gpu_info["acceleration"].append("CUDA")
            if "TensorrtExecutionProvider" in providers:
                gpu_info["acceleration"].append("TensorRT")
            if "DmlExecutionProvider" in providers:
                gpu_info["acceleration"].append("DirectML")
        elif vendor == "AMD":
            if "ROCMExecutionProvider" in providers:
                gpu_info["acceleration"].append("ROCm")
            if "DmlExecutionProvider" in providers:
                gpu_info["acceleration"].append("DirectML")
            if "MIGraphXExecutionProvider" in providers:
                gpu_info["acceleration"].append("MIGraphX")
        elif vendor == "Intel":
            if "OpenVINOExecutionProvider" in providers:
                gpu_info["acceleration"].append("OpenVINO")
            if "DmlExecutionProvider" in providers:
                gpu_info["acceleration"].append("DirectML")
        elif vendor == "Apple":
            if "CoreMLExecutionProvider" in providers:
                gpu_info["acceleration"].append("CoreML")
        else:
            # 未知厂商，尝试通用加速
            if "DmlExecutionProvider" in providers:
                gpu_info["acceleration"].append("DirectML")
        
        result["gpus"].append(gpu_info)
    
    # 确定推荐的 Provider
    if "CUDAExecutionProvider" in providers:
        result["recommended_provider"] = "CUDAExecutionProvider"
    elif "CoreMLExecutionProvider" in providers:
        result["recommended_provider"] = "CoreMLExecutionProvider"
    elif "DmlExecutionProvider" in providers:
        result["recommended_provider"] = "DmlExecutionProvider"
    elif "ROCMExecutionProvider" in providers:
        result["recommended_provider"] = "ROCMExecutionProvider"
    elif "OpenVINOExecutionProvider" in providers:
        result["recommended_provider"] = "OpenVINOExecutionProvider"
    
    return result


def get_real_executable_path():
    """获取真实的主程序可执行文件路径。

    在 ``flet build`` 打包的应用里，``sys.executable`` 指向的是
    serious_python 嵌入的 Python 解释器（例如 ``python312.exe``），而
    不是用户看到的 ``MTools.exe``。``sys.argv[0]`` 同理，指向内部
    入口脚本 ``.../flet/app/src/main.py``，也不可用。

    本函数返回当前进程真正的启动可执行文件路径，优先顺序：

    1. Windows: ``GetModuleFileNameW(NULL)`` 直接向内核取当前进程 exe
    2. macOS: ``NSBundle.mainBundle().executablePath``
    3. ``SERIOUS_PYTHON_SITE_PACKAGES`` 的父目录下第一个非 python/flet 的 ``.exe``
    4. 兜底返回 ``Path(sys.executable)``

    Returns:
        Path: 真实的 exe 路径（在打包/开发环境下都尽量返回合理值）
    """
    from pathlib import Path
    import os

    # 方式 1: Windows API 直接取当前进程 exe
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            GetModuleFileNameW = kernel32.GetModuleFileNameW
            GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
            GetModuleFileNameW.restype = wintypes.DWORD

            # 使用 32K 缓冲以支持长路径，避免 MAX_PATH 限制
            buf = ctypes.create_unicode_buffer(32768)
            length = GetModuleFileNameW(None, buf, len(buf))
            if length > 0:
                p = Path(buf.value)
                if p.exists():
                    return p
        except Exception:
            pass

    # 方式 2: macOS NSBundle
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle  # type: ignore
            exe = NSBundle.mainBundle().executablePath()
            if exe:
                p = Path(str(exe))
                if p.exists():
                    return p
        except Exception:
            pass

    # 方式 3: serious_python 环境变量兜底
    sp = os.environ.get("SERIOUS_PYTHON_SITE_PACKAGES")
    if sp:
        app_root = Path(sp).parent
        if app_root.is_dir():
            # 排除 python 解释器、flet runtime 等已知非主程序可执行文件
            excluded = {
                "python.exe", "pythonw.exe", "python3.exe", "python312.exe",
                "python311.exe", "python310.exe",
                "flet.exe", "fletd.exe",
            }
            try:
                for candidate in sorted(app_root.glob("*.exe")):
                    if candidate.name.lower() not in excluded:
                        return candidate
            except Exception:
                pass

    # 方式 4: 兜底
    return Path(sys.executable)


def is_admin() -> bool:
    """检测当前程序是否以管理员/root 身份运行。
    
    Returns:
        True 如果是管理员/root 权限，False 否则
    """
    import os
    
    try:
        if sys.platform == "win32":
            # Windows: 使用 ctypes 调用 Windows API
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            # Linux/macOS: 检测是否是 root 用户
            return os.getuid() == 0
    except Exception:
        return False


def request_admin_restart() -> bool:
    """请求以管理员身份重新启动程序。

    Windows: 通过 UAC 提权重启。
    macOS: 通过 osascript 以管理员权限重新打开 .app。

    Returns:
        True 如果成功请求重启（程序将退出），False 如果失败或用户取消
    """
    if sys.platform == "darwin":
        return _request_admin_restart_macos()
    if sys.platform != "win32":
        return False
    
    try:
        import ctypes
        import os
        from pathlib import Path

        # 在 flet build 环境下 sys.executable 指向嵌入的 python.exe，不是
        # 用户看到的 MTools.exe。必须拿到真实的主程序 exe，UAC 重启才会
        # 启动 MTools 本身而不是一个裸的 python 解释器。
        exe_path = get_real_executable_path()
        executable = str(exe_path)

        # 打包环境下直接以可执行文件重启，不传任何参数（flet build 下
        # sys.argv 是内部 .py 入口路径，传给 exe 反而会让它尝试把脚本
        # 路径当作参数解析，失败）。
        is_packaged = (
            getattr(sys, 'frozen', False)
            or os.environ.get("FLET_ASSETS_DIR")
            or os.environ.get("FLET_APP_CONSOLE")
            or os.environ.get("SERIOUS_PYTHON_SITE_PACKAGES")
            or exe_path.suffix.lower() == '.exe'
        )
        if is_packaged:
            params = ''
        else:
            # 开发环境：保留原有行为
            params = ' '.join(sys.argv) if getattr(sys, 'argv', None) else ''

        # 工作目录指向 exe 所在目录，避免 cwd 继承到奇怪路径
        working_dir = str(exe_path.parent) if exe_path.parent.exists() else None

        # 使用 ShellExecuteW 请求提权运行
        # 参数: hwnd, operation, file, params, directory, show
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,           # 父窗口句柄
            "runas",        # 请求管理员权限
            executable,     # 可执行文件
            params,         # 参数
            working_dir,    # 工作目录
            1               # SW_SHOWNORMAL
        )

        # ShellExecuteW 返回值 > 32 表示成功
        if ret > 32:
            # 成功启动新进程，退出当前进程
            os._exit(0)
            return True
        else:
            return False

    except Exception:
        return False


def _request_admin_restart_macos() -> bool:
    """macOS: 通过 osascript 以管理员权限重新打开 .app bundle。"""
    import subprocess
    from pathlib import Path

    try:
        # 找到 .app bundle 路径
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle().bundlePath()
            if bundle and bundle.endswith(".app"):
                app_path = bundle
            else:
                raise ValueError("NSBundle 未返回有效 .app 路径")
        except Exception:
            app_path = None
            for parent in Path(sys.executable).resolve().parents:
                if parent.suffix == ".app":
                    app_path = str(parent)
                    break

        if not app_path:
            return False

        subprocess.Popen([
            'osascript', '-e',
            f'do shell script "open \\"{app_path}\\"" '
            f'with administrator privileges',
        ], start_new_session=True)

        import os
        os._exit(0)
        return True
    except Exception:
        return False
