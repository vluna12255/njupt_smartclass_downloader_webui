"""Download plugin model snapshots through the main process aria2 RPC."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import atexit
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


class Aria2ModelDownloadError(RuntimeError):
    """Raised when a plugin model file cannot be downloaded."""


def system_proxy_url() -> str:
    proxies = urllib.request.getproxies()
    if not any(proxies.get(key) for key in ("https", "http", "all")):
        registry_reader = getattr(urllib.request, "getproxies_registry", None)
        if registry_reader:
            proxies = registry_reader()
    proxy = proxies.get("https") or proxies.get("http") or proxies.get("all")
    if not proxy:
        raise Aria2ModelDownloadError(
            "未检测到可用的 HTTP 系统代理，Whisper 模型下载需要启用系统代理"
        )
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    if not proxy.lower().startswith(("http://", "https://")):
        raise Aria2ModelDownloadError(
            f"系统代理类型不受支持: {proxy}，请使用 HTTP 或 Mixed 代理端口"
        )
    return proxy


def _direct_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check_model_source_connectivity(
    source_name: str,
    url: str,
    network_mode: Optional[str] = None,
    attempts: int = 3,
    timeout: float = 8.0,
) -> None:
    mode = (network_mode or os.environ.get("MODEL_NETWORK_MODE", "direct")).strip().lower()
    opener = _direct_opener() if mode != "system_proxy" else None
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            if mode == "system_proxy":
                proxy = system_proxy_url()
                import httpx

                with httpx.Client(proxy=proxy, follow_redirects=True, timeout=timeout) as client:
                    response = client.get(
                        url,
                        headers={"User-Agent": "SmartClassDownloader/1.0", "Range": "bytes=0-0"},
                    )
                    if response.status_code < 500:
                        return
                    last_error = RuntimeError(f"HTTP {response.status_code}")
            else:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "SmartClassDownloader/1.0", "Range": "bytes=0-0"},
                    method="GET",
                )
                with opener.open(request, timeout=timeout) as response:
                    if response.status < 500:
                        return
                    last_error = RuntimeError(f"HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last_error = exc
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    hint = "请尝试更换网络或启用系统代理解决" if mode == "system_proxy" else "请尝试更换网络后重试"
    raise Aria2ModelDownloadError(f"{source_name} 网络不可达，{hint}: {last_error}")


@dataclass(frozen=True)
class RemoteModelFile:
    url: str
    relative_path: str
    size: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class TransferSnapshot:
    total_size: int
    downloaded_size: int
    speed: float
    file_name: str
    file_index: int
    file_count: int
    phase: str = "downloading"

    @property
    def progress(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return min(100.0, self.downloaded_size * 100.0 / self.total_size)


class Aria2RpcClient:
    def __init__(self, rpc_url: str, secret: str = "", timeout: float = 5.0):
        self.rpc_url = rpc_url
        self.secret = secret
        self.timeout = timeout
        self._request_id = 0
        self._lock = threading.Lock()
        self._opener = _direct_opener()

    @classmethod
    def from_environment(cls):
        rpc_url = os.environ.get("ARIA2_RPC_URL", "").strip()
        if rpc_url:
            return cls(rpc_url, secret=os.environ.get("ARIA2_RPC_SECRET", "").strip())
        return get_plugin_aria2_process_manager().ensure_running()

    def call(self, method: str, params=None):
        with self._lock:
            self._request_id += 1
            request_id = self._request_id

        rpc_params = list(params or [])
        if self.secret:
            rpc_params.insert(0, f"token:{self.secret}")
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": str(request_id), "method": method, "params": rpc_params}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise Aria2ModelDownloadError(f"aria2 RPC 请求失败: {exc}") from exc
        if "error" in result:
            raise Aria2ModelDownloadError(result["error"].get("message", str(result["error"])))
        return result.get("result")

    def add_uri(self, url: str, options: dict) -> str:
        return self.call("aria2.addUri", [[url], options])

    def tell_status(self, gid: str) -> dict:
        return self.call(
            "aria2.tellStatus",
            [gid, ["status", "totalLength", "completedLength", "downloadSpeed", "errorCode", "errorMessage"]],
        )

    def force_remove(self, gid: str) -> None:
        try:
            self.call("aria2.forceRemove", [gid])
        except Aria2ModelDownloadError:
            pass

    def remove_download_result(self, gid: str) -> None:
        try:
            self.call("aria2.removeDownloadResult", [gid])
        except Aria2ModelDownloadError:
            pass


class PluginAria2ProcessManager:
    """Start a plugin-local aria2 RPC only when the main process did not provide one."""

    def __init__(self, root_dir: Optional[str] = None, client_factory=Aria2RpcClient, popen=subprocess.Popen):
        self.root_dir = Path(root_dir or Path(__file__).resolve().parents[2])
        self.client_factory = client_factory
        self.popen = popen
        self.process = None
        self.client = None
        self._lock = threading.Lock()
        self.network_mode = os.environ.get("MODEL_NETWORK_MODE", "direct").strip().lower()

    def ensure_running(self) -> Aria2RpcClient:
        with self._lock:
            if self.client and self._is_healthy(self.client):
                return self.client

            self._stop_process()
            executable = self._find_binary()
            port = self._find_free_port()
            secret = secrets.token_urlsafe(24)
            rpc_url = f"http://127.0.0.1:{port}/jsonrpc"
            args = [
                executable,
                "--enable-rpc=true",
                f"--stop-with-process={os.getpid()}",
                "--rpc-listen-all=false",
                f"--rpc-listen-port={port}",
                f"--rpc-secret={secret}",
                "--rpc-allow-origin-all=false",
                "--file-allocation=none",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=0",
                "--console-log-level=warn",
            ]
            if self.network_mode == "system_proxy":
                proxy = system_proxy_url()
                args.extend([
                    f"--all-proxy={proxy}",
                    f"--http-proxy={proxy}",
                    f"--https-proxy={proxy}",
                    "--no-proxy=127.0.0.1,localhost,::1",
                ])
            else:
                args.extend(["--all-proxy=", "--http-proxy=", "--https-proxy=", "--ftp-proxy="])
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.process = self.popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._process_environment(),
                creationflags=creationflags,
            )
            self.client = self.client_factory(rpc_url, secret=secret)

            for _ in range(50):
                if self._is_healthy(self.client):
                    return self.client
                if self.process.poll() is not None:
                    break
                time.sleep(0.1)

            self._stop_process()
            raise Aria2ModelDownloadError("插件本地 aria2c 启动失败，请检查 bin/aria2c.exe")

    def stop(self) -> None:
        with self._lock:
            self._stop_process()

    def _find_binary(self) -> str:
        candidates = [
            os.environ.get("ARIA2C_PATH", ""),
            str(self.root_dir / "bin" / "aria2c.exe"),
            shutil.which("aria2c") or "",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return os.path.abspath(candidate)
        raise Aria2ModelDownloadError("未找到 aria2c，请将 aria2c.exe 放入程序根目录 bin 文件夹")

    @staticmethod
    def _is_healthy(client: Aria2RpcClient) -> bool:
        try:
            client.call("aria2.getVersion")
            return True
        except Aria2ModelDownloadError:
            return False

    def _stop_process(self) -> None:
        process = self.process
        self.client = None
        self.process = None
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _process_environment(self) -> dict:
        env = os.environ.copy()
        for key in list(env):
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "NO_PROXY"}:
                del env[key]
        env["NO_PROXY"] = "*" if self.network_mode == "direct" else "127.0.0.1,localhost,::1"
        return env


_plugin_aria2_process_manager = PluginAria2ProcessManager()
atexit.register(_plugin_aria2_process_manager.stop)


def get_plugin_aria2_process_manager() -> PluginAria2ProcessManager:
    return _plugin_aria2_process_manager


class Aria2ModelDownloader:
    def __init__(self, client: Optional[Aria2RpcClient] = None, poll_interval: float = 0.5):
        self.client = client or Aria2RpcClient.from_environment()
        self.poll_interval = poll_interval

    def download_snapshot(
        self,
        files: Iterable[RemoteModelFile],
        output_dir: str,
        progress_callback: Optional[Callable[[TransferSnapshot], None]] = None,
    ) -> str:
        return self.download_snapshots([(files, output_dir)], progress_callback)[0]

    def download_snapshots(
        self,
        snapshots: Iterable[tuple[Iterable[RemoteModelFile], str]],
        progress_callback: Optional[Callable[[TransferSnapshot], None]] = None,
    ) -> list[str]:
        prepared_snapshots = [(list(files), Path(output_dir).resolve()) for files, output_dir in snapshots]
        model_files = [(item, root) for files, root in prepared_snapshots for item in files]
        if not model_files:
            raise Aria2ModelDownloadError("远程模型仓库中没有可下载文件")

        for _, root in prepared_snapshots:
            root.mkdir(parents=True, exist_ok=True)
        total_size = sum(max(0, item.size) for item, _ in model_files)
        completed_size = 0

        for index, (item, root) in enumerate(model_files, start=1):
            target = self._resolve_target(root, item.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            remaining_size = sum(max(0, pending.size) for pending, _ in model_files[index:])
            if self._is_complete(target, item):
                completed_size += max(item.size, target.stat().st_size)
                self._report(
                    progress_callback, total_size, completed_size, 0, item, index, len(model_files),
                    phase="checking",
                )
                continue

            options = {
                "dir": str(target.parent),
                "out": target.name,
                "continue": "true",
                "split": "8",
                "max-connection-per-server": "8",
                "min-split-size": "1M",
                "max-tries": "5",
                "retry-wait": "3",
                "connect-timeout": "30",
                "timeout": "120",
                "auto-file-renaming": "false",
                "allow-overwrite": "true",
            }
            gid = self.client.add_uri(item.url, options)
            succeeded = False
            try:
                while True:
                    status = self.client.tell_status(gid)
                    state = status.get("status", "")
                    current_total = int(status.get("totalLength") or item.size or 0)
                    current_done = int(status.get("completedLength") or 0)
                    if current_total > 0:
                        current_done = min(current_done, current_total)
                    speed = float(status.get("downloadSpeed") or 0)
                    aggregate_total = completed_size + current_total + remaining_size
                    self._report(
                        progress_callback,
                        aggregate_total,
                        completed_size + current_done,
                        speed,
                        item,
                        index,
                        len(model_files),
                    )

                    if state == "complete":
                        succeeded = True
                        break
                    if state in {"error", "removed"}:
                        error = status.get("errorMessage") or status.get("errorCode") or state
                        raise Aria2ModelDownloadError(f"aria2 下载失败: {error}")
                    time.sleep(self.poll_interval)
            finally:
                if not succeeded:
                    self.client.force_remove(gid)
                self.client.remove_download_result(gid)

            self._verify_file(target, item)
            completed_size += max(item.size, target.stat().st_size)
            self._report(progress_callback, max(total_size, completed_size), completed_size, 0, item, index, len(model_files))

        return [str(root) for _, root in prepared_snapshots]

    @staticmethod
    def _resolve_target(root: Path, relative_path: str) -> Path:
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise Aria2ModelDownloadError(f"模型仓库包含非法路径: {relative_path}") from exc
        return target

    @staticmethod
    def _is_complete(path: Path, item: RemoteModelFile) -> bool:
        return path.is_file() and (item.size <= 0 or path.stat().st_size == item.size)

    @staticmethod
    def _verify_file(path: Path, item: RemoteModelFile) -> None:
        if not path.is_file():
            raise Aria2ModelDownloadError(f"模型文件下载后不存在: {item.relative_path}")
        if item.size > 0 and path.stat().st_size != item.size:
            raise Aria2ModelDownloadError(f"模型文件大小校验失败: {item.relative_path}")
        if item.sha256:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().lower() != item.sha256.lower():
                raise Aria2ModelDownloadError(f"模型文件哈希校验失败: {item.relative_path}")

    @staticmethod
    def _report(callback, total_size, downloaded_size, speed, item, index, count, phase="downloading"):
        if callback:
            callback(
                TransferSnapshot(
                    total_size=total_size,
                    downloaded_size=downloaded_size,
                    speed=speed,
                    file_name=item.relative_path,
                    file_index=index,
                    file_count=count,
                    phase=phase,
                )
            )


def huggingface_model_files(repo_id: str, revision: str = "main") -> list[RemoteModelFile]:
    import httpx
    from huggingface_hub import HfApi, hf_hub_url, set_client_factory

    proxy = system_proxy_url()
    set_client_factory(lambda: httpx.Client(proxy=proxy, follow_redirects=True, timeout=None))

    info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    files = []
    for sibling in info.siblings:
        relative_path = sibling.rfilename
        if relative_path == ".gitattributes":
            continue
        lfs = sibling.lfs or {}
        files.append(
            RemoteModelFile(
                url=hf_hub_url(repo_id, relative_path, revision=revision),
                relative_path=relative_path,
                size=int(sibling.size or lfs.get("size") or 0),
                sha256=str(lfs.get("sha256") or ""),
            )
        )
    return files


def modelscope_model_files(repo_id: str, revision: str = "master") -> list[RemoteModelFile]:
    from modelscope.hub.api import HubApi
    from modelscope.hub.file_download import get_file_download_url

    api = HubApi()
    endpoint = api.get_endpoint_for_read(repo_id=repo_id, repo_type="model")
    revision_detail = api.get_valid_revision_detail(repo_id, revision=revision, endpoint=endpoint)
    resolved_revision = revision_detail["Revision"]
    repo_files = api.get_model_files(
        model_id=repo_id,
        revision=resolved_revision,
        recursive=True,
        endpoint=endpoint,
    )
    files = []
    for model_file in repo_files:
        relative_path = model_file.get("Path") or model_file.get("Name")
        if not relative_path or model_file.get("Type") == "tree":
            continue
        files.append(
            RemoteModelFile(
                url=get_file_download_url(repo_id, relative_path, resolved_revision, endpoint=endpoint),
                relative_path=relative_path,
                size=int(model_file.get("Size") or 0),
                sha256=str(model_file.get("Sha256") or model_file.get("Sha256Hash") or ""),
            )
        )
    return files


def create_download_progress_callback(service_status, report_to_main, progress_start: float, progress_end: float):
    width = max(0.0, progress_end - progress_start)

    def update(snapshot: TransferSnapshot):
        progress = progress_start + width * snapshot.progress / 100.0
        if snapshot.phase == "checking":
            message = f"正在检查模型文件 {snapshot.file_index}/{snapshot.file_count}: {snapshot.file_name}"
            details = {
                "total_size": 0,
                "downloaded_size": 0,
                "speed": 0,
                "file_name": snapshot.file_name,
                "file_index": snapshot.file_index,
                "file_count": snapshot.file_count,
            }
            service_status.update(phase="checking", message=message, progress=progress, **details)
            report_to_main("checking", message, progress=progress, **details)
            return
        message = (
            f"正在下载模型文件 {snapshot.file_index}/{snapshot.file_count}: "
            f"{snapshot.file_name} ({snapshot.progress:.1f}%, {format_speed(snapshot.speed)})"
        )
        details = {
            "total_size": snapshot.total_size,
            "downloaded_size": snapshot.downloaded_size,
            "speed": snapshot.speed,
            "eta": format_eta(snapshot.total_size, snapshot.downloaded_size, snapshot.speed),
            "file_name": snapshot.file_name,
            "file_index": snapshot.file_index,
            "file_count": snapshot.file_count,
        }
        service_status.update(phase="downloading", message=message, progress=progress, **details)
        report_to_main("downloading", message, progress=progress, **details)

    return update


def format_size(size_bytes: float) -> str:
    value = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return "0.00 B"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_size(bytes_per_second)}/s"


def format_eta(total_size: int, downloaded_size: int, speed: float) -> str:
    if total_size <= 0 or speed <= 0:
        return "计算中..."
    seconds = max(0, int((total_size - downloaded_size) / speed))
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    return f"{seconds // 3600}小时{(seconds % 3600) // 60}分"
