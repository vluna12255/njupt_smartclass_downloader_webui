import os
import sys
import subprocess
import threading
import time
import requests
import logging
import shutil
import tarfile
import urllib.request
import winreg
import zipfile
import socket
from concurrent.futures import ThreadPoolExecutor

PIP_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"

try:
    from config_manager import ROOT_DIR, PLUGINS_DIR, RUNTIME_DIR, LOGS_DIR
except ImportError:
    ROOT_DIR = os.getcwd()
    PLUGINS_DIR = os.path.join(ROOT_DIR, "plugins")
    RUNTIME_DIR = os.path.join(ROOT_DIR, "runtime")
    LOGS_DIR = os.path.join(ROOT_DIR, "logs")


PYTHON_PORTABLE_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20260114/cpython-3.13.11+20260114-x86_64-pc-windows-msvc-install_only.tar.gz"
VC_REDIST_URL = "https://aka.ms/vc14/vc_redist.x64.exe"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

class PluginManager:
    def __init__(self):
        self.base_dir = ROOT_DIR
        self.plugins_dir = PLUGINS_DIR
        self.runtime_dir = RUNTIME_DIR
        self.logs_dir = LOGS_DIR
        self.venv_base_dir = os.path.join(ROOT_DIR, "plugins_env")
        self.bin_dir = os.path.join(self.base_dir, "bin")
        self.python_home = os.path.join(self.runtime_dir, "python")
        self.python_exe = os.path.join(self.python_home, "python.exe")

        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.venv_base_dir, exist_ok=True)

        # 跟踪卸载状态
        self.uninstalling_plugins = set()
        self.uninstall_lock = threading.Lock()

        self.configs = {
            "whisper": {
                "folder": "whisper",
                "venv": os.path.join(self.venv_base_dir, "whisper_env"),
                "entry": "main.py",
                "port": 8000, 
                "health_url": "http://127.0.0.1:8000/docs",
                "requirements": [
                    "fastapi==0.128.0", "uvicorn==0.40.0", "python-multipart==0.0.22", "faster-whisper==1.2.1", "numpy==2.4.1"
                ]
            },
            "funasr": {
                "folder": "funasr",
                "venv": os.path.join(self.venv_base_dir, "funasr_env"),
                "entry": "main.py",
                "port": 8001,
                "health_url": "http://127.0.0.1:8001/docs",
                "requirements": [
                    "fastapi==0.128.0", "uvicorn==0.40.0", "python-multipart", 
                    "funasr==1.3.1", "modelscope==1.34.0"
                    # torch 和 torchaudio 已在前面根据 GPU/CPU 单独安装，不要在这里重复
                ]
            },
            "slides_extractor": {
                "folder": "slides_extractor",
                "venv": os.path.join(self.venv_base_dir, "slides_env"),
                "entry": "main.py",
                "port": 8002,
                "health_url": "http://127.0.0.1:8002/docs", 
                "requirements": [
                    "fastapi==0.128.0", "uvicorn==0.40.0", "pydantic==2.12.5", "opencv-python-headless==4.13.0.90", 
                    "numpy==2.4.1", "Pillow==12.1.0", "reportlab==4.4.9", "scikit-image==0.26.0"
                ]
            }
        }
        
        self.processes = {} 
        self.lock = threading.Lock()
        self.env_init_lock = threading.Lock()
        self.running_ports = {} 
        self.on_uninstall_callback = None
        self.on_startup_task_callback = None  # (plugin_name) -> task_id
        self.main_server_url = ""  # 主服务地址，启动子进程时传入
    
    def set_uninstall_callback(self, callback):
        self.on_uninstall_callback = callback

    def set_startup_task_callback(self, callback):
        """注入启动任务回调：callback(plugin_name) 创建并返回 task_id"""
        self.on_startup_task_callback = callback

    def set_main_server_url(self, url: str):
        """注入主服务器地址，供子进程回报启动状态使用"""
        self.main_server_url = url

    def _get_venv_python(self, venv_path):
        return os.path.join(venv_path, "Scripts", "python.exe")

    def _get_venv_pip(self, venv_path):
        return os.path.join(venv_path, "Scripts", "pip.exe")

    def _check_gpu_hardware(self):
        """检查是否有 NVIDIA GPU"""
        try:
            subprocess.check_call(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except: 
            return False
    
    def _detect_cuda_version(self):
        """检测系统的 CUDA 版本，返回对应的 PyTorch 索引 URL"""
        try:
            result = subprocess.run(
                ["nvidia-smi"], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            
            if result.returncode == 0:
                # 从 nvidia-smi 输出中提取 CUDA 版本
                import re
                match = re.search(r'CUDA Version:\s*(\d+)\.(\d+)', result.stdout)
                if match:
                    major = int(match.group(1))
                    minor = int(match.group(2))
                    cuda_version = f"{major}.{minor}"
                    
                    print(f"检测到 CUDA 版本: {cuda_version}")
                    
                    # 根据 CUDA 版本选择对应的 PyTorch 版本
                    if major == 11:
                        return "cu118", "https://download.pytorch.org/whl/cu118"
                    elif major == 12:
                        if minor >= 4:
                            return "cu124", "https://download.pytorch.org/whl/cu124"
                        elif minor >= 1:
                            return "cu121", "https://download.pytorch.org/whl/cu121"
                        else:
                            return "cu118", "https://download.pytorch.org/whl/cu118"
                    else:
                        # 默认使用 CUDA 11.8（兼容性最好）
                        print(f"未知 CUDA 版本 {cuda_version}，使用默认 cu118")
                        return "cu118", "https://download.pytorch.org/whl/cu118"
        except Exception as e:
            print(f"检测 CUDA 版本失败: {e}")
        
        # 默认返回 CUDA 11.8
        return "cu118", "https://download.pytorch.org/whl/cu118"

    def _is_service_running(self, url):
        try:
            requests.get(url, timeout=1)
            return True
        except: return False

    def _get_free_port(self, default_port):
        def is_port_free(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(('127.0.0.1', port)) != 0

        if is_port_free(default_port):
            return default_port
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            _, port = s.getsockname()
            return port
        

    def get_service_url(self, plugin_name):
        port = self.running_ports.get(plugin_name)
        if not port:
            cfg = self.configs.get(plugin_name)
            if cfg:
                port = cfg["port"]
            else:
                return None
        return f"http://127.0.0.1:{port}"

    def _ensure_runtime(self, status_callback=None):
        if os.path.exists(self.python_exe):
            return True

        print(f"检测到缺失 Python 运行时，准备下载 Python 3.13...")
        if status_callback: status_callback("正在下载 Python 3.13 环境 (约50MB)...")
        
        try:
            os.makedirs(self.runtime_dir, exist_ok=True)
            tar_path = os.path.join(self.runtime_dir, "python_runtime.tar.gz")
            
            def report_progress(block_num, block_size, total_size):
                if not status_callback: return
                percent = int((block_num * block_size * 100) / total_size)
                if percent % 10 == 0:
                    status_callback(f"下载环境包: {percent}%")

            urllib.request.urlretrieve(PYTHON_PORTABLE_URL, tar_path, reporthook=report_progress)
            
            if status_callback: status_callback("正在解压运行环境...")
            
            try:
                with tarfile.open(tar_path, "r") as tar:
                    tar.extractall(path=self.runtime_dir)
            except Exception:
                pass
            
            try: os.remove(tar_path)
            except: pass

            if os.path.exists(self.python_exe):
                if status_callback: status_callback("运行环境准备就绪")
                return True
            else:
                possible_path = os.path.join(self.runtime_dir, "python", "install", "python.exe")
                if os.path.exists(possible_path):
                     pass 
                
                if os.path.exists(self.python_exe):
                    return True
                raise Exception("解压后未找到 python.exe")

        except Exception as e:
            err_msg = f"环境初始化失败: {str(e)}"
            print(err_msg)
            if status_callback: status_callback(err_msg)
            return False

    def _check_vc_redist_installed(self):
        try:
            key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
                installed, _ = winreg.QueryValueEx(key, "Installed")
                return installed == 1
        except OSError:
            return False

    def _install_vc_redist(self, status_callback=None):
        print("检测到缺失 VC++ 运行库，准备下载安装...")
        if status_callback: status_callback("正在安装 VC++ Runtime (可能弹出UAC，请注意任务栏)...")
        
        installer_path = os.path.join(self.runtime_dir, "vc_redist.x64.exe")
        os.makedirs(self.runtime_dir, exist_ok=True)

        try:
            if not os.path.exists(installer_path):
                urllib.request.urlretrieve(VC_REDIST_URL, installer_path)
            
            cmd = [installer_path, "/install", "/passive", "/norestart"]
            print(f"Executing: {' '.join(cmd)}")
            subprocess.check_call(cmd)
            
            if status_callback: status_callback("VC++ 运行库安装完成")
            return True
        except Exception as e:
            err_msg = f"VC++ 运行库安装失败: {e}"
            print(err_msg)
            if status_callback: status_callback(err_msg)
            return False
        finally:
            if os.path.exists(installer_path):
                try: os.remove(installer_path)
                except: pass

    def _ensure_ffmpeg(self, status_callback=None):
        os.makedirs(self.bin_dir, exist_ok=True)
        ffmpeg_exe_path = os.path.join(self.bin_dir, "ffmpeg.exe")

        if os.path.exists(ffmpeg_exe_path):
            return True

        print("检测到缺失 FFmpeg，准备下载...")
        if status_callback: status_callback("正在下载媒体组件 FFmpeg ...")

        zip_path = os.path.join(self.bin_dir, "ffmpeg_temp.zip")
        
        try:
            def report_progress(block_num, block_size, total_size):
                if not status_callback: return
                percent = int((block_num * block_size * 100) / total_size)
                if percent % 10 == 0:
                    status_callback(f"下载 FFmpeg: {percent}%")

            urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook=report_progress)

            if status_callback: status_callback("正在解压 FFmpeg...")

            found = False
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for file_info in zf.infolist():
                    if file_info.filename.endswith("bin/ffmpeg.exe"):
                        file_info.filename = os.path.basename(file_info.filename) 
                        zf.extract(file_info, self.bin_dir)
                        found = True
                        break
            
            os.remove(zip_path)

            if not found:
                raise Exception("下载包中未找到 ffmpeg.exe")

            if status_callback: status_callback("FFmpeg 组件安装完成")
            return True

        except Exception as e:
            err_msg = f"FFmpeg 安装失败: {str(e)}"
            print(err_msg)
            if status_callback: status_callback(err_msg)
            if os.path.exists(zip_path): os.remove(zip_path)
            return False

    def _clean_plugin_cache(self, plugin_name):
        """
        根据插件名称清理对应的模型缓存目录
        """
        user_home = os.path.expanduser("~")
        paths_to_remove = []

        if plugin_name == "whisper":
            paths_to_remove.append(os.path.join(user_home, ".cache", "huggingface"))

        elif plugin_name == "funasr":
            paths_to_remove.append(os.path.join(user_home, ".cache", "modelscope"))
            paths_to_remove.append(os.path.join(user_home, ".modelscope"))
            paths_to_remove.append(os.path.join(PLUGINS_DIR, "funasr", "paraformer-zh"))

        for path in paths_to_remove:
            if os.path.exists(path):
                print(f"[{plugin_name}] 正在清理缓存目录: {path}")
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except Exception as e:
                    print(f"[{plugin_name}] 缓存清理警告: 无法删除 {path} - {str(e)}")

    def _clean_plugin_directory(self, plugin_name, keep_files=None):
        """
        清理插件目录中的文件，保留指定的文件
        """
        if keep_files is None:
            keep_files = []
        
        plugin_dir = os.path.join(PLUGINS_DIR, self.configs[plugin_name]["folder"])
        if not os.path.exists(plugin_dir):
            return
        
        print(f"[{plugin_name}] 正在清理插件目录，保留文件: {keep_files}")
        
        try:
            for item in os.listdir(plugin_dir):
                item_path = os.path.join(plugin_dir, item)
                
                # 跳过需要保留的文件
                if item in keep_files:
                    continue
                
                # 删除文件或目录
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                        print(f"[{plugin_name}] 已删除文件: {item}")
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                        print(f"[{plugin_name}] 已删除目录: {item}")
                except Exception as e:
                    print(f"[{plugin_name}] 删除 {item} 时出错: {str(e)}")
        
        except Exception as e:
            print(f"[{plugin_name}] 清理插件目录时出错: {str(e)}")

    def uninstall_plugin(self, plugin_name):
        # 标记为卸载中
        with self.uninstall_lock:
            if plugin_name in self.uninstalling_plugins:
                return False, "该插件正在卸载中，请稍候"
            self.uninstalling_plugins.add(plugin_name)
        
        try:
            if self.on_uninstall_callback:
                try:
                    print(f"[{plugin_name}] 触发卸载回调，通知任务管理器中止相关任务...")
                    self.on_uninstall_callback(plugin_name)
                except Exception as e:
                    print(f"[{plugin_name}] 卸载回调执行出错: {e}")
            
            return self._do_uninstall(plugin_name)
        finally:
            # 无论成功失败，都要移除卸载标记
            with self.uninstall_lock:
                self.uninstalling_plugins.discard(plugin_name)
    
    def _do_uninstall(self, plugin_name):
        with self.lock:
            cfg = self.configs.get(plugin_name)
            if not cfg:
                return False, "插件配置不存在"
            
            if plugin_name in self.processes:
                proc = self.processes[plugin_name]
                try:
                    print(f"[{plugin_name}] 正在终止进程 PID: {proc.pid}...")
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    print(f"[{plugin_name}] 进程无响应，强制 Kill...")
                    try: proc.kill()
                    except: pass
                
                del self.processes[plugin_name]

            if plugin_name in self.running_ports:
                del self.running_ports[plugin_name]

            self._clean_plugin_cache(plugin_name)

            venv_path = os.path.abspath(cfg["venv"])
            if not venv_path.startswith(os.path.abspath(self.venv_base_dir)):
                 return False, "安全警告：禁止删除非环境目录文件"

            if not os.path.exists(venv_path):
                return True, "环境已不存在 (缓存已尝试清理)"
            
            if "plugins_env" not in venv_path:
                 return False, "非法路径，拒绝删除"

            print(f"[{plugin_name}] 正在删除文件: {venv_path}")

            success = False
            last_error = ""
            
            for i in range(5):
                try:
                    if sys.platform == "win32":
                        subprocess.run(f'rmdir /s /q "{venv_path}"', shell=True, check=False)

                    if os.path.exists(venv_path):
                        shutil.rmtree(venv_path)

                    if not os.path.exists(venv_path):
                        success = True
                        break 
                        
                except Exception as e:
                    last_error = str(e)
                    print(f"[{plugin_name}] 删除重试 ({i+1}/5): 文件被占用...")
                    time.sleep(1)

            if success:
                print(f"[{plugin_name}] 虚拟环境删除成功，开始清理插件目录...")
                
                # 虚拟环境删除成功后，再清理插件目录
                if plugin_name in ["whisper", "funasr"]:
                    self._clean_plugin_directory(plugin_name, keep_files=["main.py", "async_task_manager.py"])
                elif plugin_name == "slides_extractor":
                    pycache_path = os.path.join(PLUGINS_DIR, self.configs[plugin_name]["folder"], "__pycache__")
                    if os.path.exists(pycache_path):
                        print(f"[{plugin_name}] 正在清理 __pycache__ 目录")
                        try:
                            shutil.rmtree(pycache_path, ignore_errors=True)
                        except Exception as e:
                            print(f"[{plugin_name}] __pycache__ 清理警告: {str(e)}")
                
                print(f"[{plugin_name}] 卸载完成")
                return True, "卸载成功"
            else:
                if os.path.exists(os.path.join(venv_path, "Scripts", "python.exe")):
                    return False, f"卸载失败 (进程可能卡死，请重启软件): {last_error}"
                else:
                    return True, "卸载完成 (可能有残留空文件夹)"

    def install_plugin(self, plugin_name, status_callback=None):
        cfg = self.configs.get(plugin_name)
        if not cfg: return
        with self.env_init_lock:
            if not self._check_vc_redist_installed():
                try:
                    self._install_vc_redist(status_callback)
                except Exception as e:
                    print(f"Warning: VC Redist install issue: {e}")

            if plugin_name in ["whisper", "funasr"]:
                if not self._ensure_ffmpeg(status_callback):
                    raise Exception("FFmpeg 安装失败，无法继续安装插件")

            if not self._ensure_runtime(status_callback):
                return

        install_log_path = os.path.join(self.logs_dir, f"{plugin_name}_install.log")
        venv_path = cfg["venv"]
        
        with open(install_log_path, "w", encoding="utf-8") as log_file:
            try:
                if status_callback: status_callback("正在创建独立虚拟环境...")
                create_venv_cmd = [self.python_exe, "-m", "venv", venv_path]
                self._run_cmd_realtime(create_venv_cmd, log_file, None)

                venv_python = self._get_venv_python(venv_path)
                venv_pip = self._get_venv_pip(venv_path)

                if status_callback: status_callback("升级构建工具...")

                upgrade_cmd = [venv_python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "-i", PIP_INDEX_URL]
                self._run_cmd_realtime(upgrade_cmd, log_file, None)

                local_whl_name = "editdistance-0.8.1-cp313-cp313-win_amd64.whl"
                local_whl_path = os.path.join(self.base_dir, "bin", local_whl_name)

                if os.path.exists(local_whl_path) and plugin_name == "funasr":
                    if status_callback: status_callback(f"[{plugin_name}] 安装本地预编译组件: {local_whl_name}")
                    local_install_cmd = [venv_python, "-m", "pip", "install", local_whl_path]
                    self._run_cmd_realtime(local_install_cmd, log_file, lambda msg: status_callback(f"{msg}" if status_callback else None))

                torch_plugins = ["whisper", "funasr"]
                
                if plugin_name in torch_plugins:
                    has_gpu = self._check_gpu_hardware()
                    
                    if has_gpu:
                        cuda_tag, torch_index_url = self._detect_cuda_version()
                        if status_callback: status_callback(f"正在配置 GPU (CUDA {cuda_tag}) 计算库...")
                        print(f"[{plugin_name}] 安装 GPU 版本 PyTorch (CUDA {cuda_tag})")
                        torch_cmd = [venv_python, "-m", "pip", "install", "torch", "torchaudio", "--index-url", torch_index_url]
                    else:
                        if status_callback: status_callback(f"正在配置 CPU 计算库...")
                        print(f"[{plugin_name}] 安装 CPU 版本 PyTorch")
                        torch_cmd = [venv_python, "-m", "pip", "install", "torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu"]
                    
                    self._run_cmd_realtime(torch_cmd, log_file, lambda msg: status_callback(f"{msg}" if status_callback else None))
                else:
                    if status_callback: status_callback(f"[{plugin_name}] 跳过 PyTorch 安装 (无需 GPU 支持)")

                if status_callback: status_callback("安装插件依赖组件...")
                req_cmd = [venv_python, "-m", "pip", "install", "--prefer-binary", "-i", PIP_INDEX_URL] + cfg["requirements"]
                self._run_cmd_realtime(req_cmd, log_file, status_callback)
                
                marker = os.path.join(venv_path, ".install_success")
                with open(marker, "w") as f: f.write("ok")
                
                if status_callback: status_callback(f"{plugin_name} 安装成功！")

            except Exception as e:
                if status_callback: status_callback(f"安装失败: {str(e)}")
                shutil.rmtree(venv_path, ignore_errors=True)
                raise e

    def _run_cmd_realtime(self, cmd, log_file_handle, status_callback=None):
        print(f"Exec: {' '.join(cmd)}")
        
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
            
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace',
            startupinfo=startupinfo
        )

        for line in iter(process.stdout.readline, ''):
            if not line: break
            line = line.strip()
            if line:
                log_file_handle.write(line + "\n")
                log_file_handle.flush()
                if status_callback and ("Downloading" in line or "Installing" in line):
                    short_msg = line[:40] + "..." if len(line) > 40 else line
                    status_callback(short_msg)

        ret = process.wait()
        if ret != 0:
            raise subprocess.CalledProcessError(ret, cmd)

    def is_first_run(self, plugin_name):
        """检查插件是否首次运行（模型未下载）"""
        if plugin_name == "whisper":
            # 检查 Whisper 模型目录
            model_dir = os.path.join(PLUGINS_DIR, "whisper", "large-v3")
            return not os.path.exists(model_dir) or not os.listdir(model_dir)
        elif plugin_name == "funasr":
            # 检查 FunASR 模型目录
            model_dir = os.path.join(PLUGINS_DIR, "funasr", "paraformer-zh")
            return not os.path.exists(model_dir) or not os.listdir(model_dir)
        return False

    def get_plugin_status(self, plugin_name, check_running=True):
        cfg = self.configs.get(plugin_name)
        if not cfg: return {"installed": False, "running": False, "uninstalling": False}

        # 检查是否正在卸载
        with self.uninstall_lock:
            is_uninstalling = plugin_name in self.uninstalling_plugins
        
        if is_uninstalling:
            return {"installed": True, "running": False, "uninstalling": True}

        marker = os.path.join(cfg["venv"], ".install_success")
        is_installed = os.path.exists(marker)
        
        is_running = False
        if is_installed and check_running:
            base_url = self.get_service_url(plugin_name)
            if base_url:
                health_url = f"{base_url}/docs"
                is_running = self._is_service_running(health_url)
            
            if not is_running and plugin_name in self.running_ports:
                proc = self.processes.get(plugin_name)
                if not proc or proc.poll() is not None:
                    del self.running_ports[plugin_name]
                    if plugin_name in self.processes:
                         del self.processes[plugin_name]

        return {"installed": is_installed, "running": is_running, "uninstalling": False}

    def start_service(self, plugin_name):
        with self.lock:
            # 先检查进程记录（避免重复启动）
            if plugin_name in self.processes:
                proc = self.processes[plugin_name]
                # 检查进程是否还活着
                if proc.poll() is None:
                    print(f"[{plugin_name}] 进程已存在且运行中 (PID: {proc.pid})，跳过启动")
                    return False  # 返回 False 表示没有启动新进程
                else:
                    # 进程已死，清理记录
                    print(f"[{plugin_name}] 检测到旧进程已退出，清理记录")
                    del self.processes[plugin_name]
                    if plugin_name in self.running_ports:
                        del self.running_ports[plugin_name]

            # 检查是否已安装
            status = self.get_plugin_status(plugin_name, check_running=False)
            if not status["installed"]:
                print(f"[{plugin_name}] 未安装，无法启动")
                return False

            cfg = self.configs[plugin_name]
            venv_python = self._get_venv_python(cfg["venv"])
            script_path = os.path.join(self.plugins_dir, cfg["folder"], cfg["entry"])
            
            if not os.path.exists(script_path):
                print(f"找不到插件入口: {script_path}")
                return False

            runtime_port = self._get_free_port(cfg["port"])
            print(f"[{plugin_name}] 计划启动端口: {runtime_port}")
            cmd = [venv_python, script_path, "--port", str(runtime_port)]
            log_path = os.path.join(self.logs_dir, f"{plugin_name}_run.log")

            try:
                startupinfo = None
                creationflags = 0
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    creationflags = subprocess.CREATE_NO_WINDOW

                log_file = open(log_path, "a", encoding="utf-8")
                cwd_path = os.path.join(self.plugins_dir, cfg["folder"])

                # 将主服务地址注入子进程环境变量
                proc_env = os.environ.copy()
                if self.main_server_url:
                    proc_env["MAIN_SERVER_URL"] = self.main_server_url

                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd_path, 
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    env=proc_env
                )
                log_file.close()
                self.processes[plugin_name] = proc
                self.running_ports[plugin_name] = runtime_port
                print(f"[{plugin_name}] 服务已启动 (PID: {proc.pid}, Port: {runtime_port})")

                # 创建启动任务卡片（whisper/funasr 才有模型加载阶段）
                if plugin_name in ("whisper", "funasr") and self.on_startup_task_callback:
                    try:
                        self.on_startup_task_callback(plugin_name)
                    except Exception as _e:
                        print(f"[{plugin_name}] 创建启动任务卡片失败: {_e}")

                return True  # 返回 True 表示成功启动了新进程
            except Exception as e:
                print(f"启动失败: {e}")
                return False

    def stop_service(self, plugin_name):
        if plugin_name in self.processes:
            proc = self.processes[plugin_name]
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except:
                proc.kill()
            del self.processes[plugin_name]

            if plugin_name in self.running_ports:
                del self.running_ports[plugin_name]
            print(f"[{plugin_name}] 已停止")

    def stop_all_services(self):
        with self.lock:
            for name, proc in self.processes.items():
                if proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except:
                        proc.kill()
            self.processes.clear()
            self.running_ports.clear()

plugin_manager = PluginManager()