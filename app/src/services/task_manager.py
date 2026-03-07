"""任务管理器"""
import threading
import time
import os
import glob
import requests
import subprocess
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, Future

from ..models.models import TaskInfo, TaskStatus
from ..utils.logger import get_logger
from ..utils.temp_file_manager import temp_manager
from ..utils.config_manager import config_manager
from ..utils.websocket_broadcaster import broadcast_task_update_sync
from ..utils.exceptions import (
    SmartclassException, 
    NetworkException, 
    DownloadException,
    PluginException,
    AuthenticationException,
    DiskSpaceException,
    translate_error_to_chinese
)
from ..utils.error_handler import ErrorHandler, with_retry, RetryConfig
from .download_service import DownloadService
from .transcribe_service import TranscribeService
from .ppt_service import PPTService

logger = get_logger('task_manager')


class TaskManager:
    """任务调度器"""
    
    def __init__(self, max_download_concurrent=None, max_whisper_concurrent=1):
        self.tasks: Dict[str, TaskInfo] = {}
        self.lock = threading.Lock()
        
        config = config_manager.get()
        if max_download_concurrent is None:
            max_download_concurrent = config.max_download_concurrent
        
        self.executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="MainWorker")
        self.futures: Dict[str, Future] = {}
        
        self.download_sem = threading.Semaphore(max_download_concurrent)
        self.whisper_sem = threading.Semaphore(max_whisper_concurrent)
        
        self.download_service = DownloadService()
        self.transcribe_service = TranscribeService()
        self.ppt_service = None
        
        self._clean_residuals()
    
    def set_plugin_manager(self, plugin_manager):
        """注入插件管理器"""
        self.ppt_service = PPTService(plugin_manager)
    
    def _clean_residuals(self):
        """启动后台清理线程"""
        threading.Thread(target=self._clean_worker, daemon=True).start()
    
    def _is_file_valid(self, path: str, min_size: int = 1) -> bool:
        """检查文件有效性"""
        if not path:
            return False
        return os.path.exists(path) and os.path.getsize(path) > min_size
    
    def _clean_worker(self):
        """清理临时文件"""
        try:
            saved_config = config_manager.get()
            target_download_dir = saved_config.download_dir if saved_config.download_dir else None
            
            if target_download_dir:
                drive, tail = os.path.splitdrive(target_download_dir)
                if drive and (not tail or tail in ['\\', '/']):
                    target_download_dir = os.path.join(target_download_dir, "SmartclassDownload")
        except Exception as e:
            logger.error(f"获取下载目录失败: {e}", exc_info=True)
            target_download_dir = None
        
        if target_download_dir and os.path.exists(target_download_dir):
            logger.info(f"系统启动，正在扫描并清理残留的临时文件: {target_download_dir}")
            try:
                count = temp_manager.cleanup_pattern(target_download_dir, "*.tmp*")
                logger.info(f"清理了 {count} 个临时下载文件")
            except Exception as e:
                logger.error(f"清理临时文件出错: {e}", exc_info=True)
    
    def add_install_task(self, plugin_name: str):
        """创建插件安装任务"""
        task_id = f"install_{plugin_name}"
        with self.lock:
            if task_id in self.tasks:
                current_status = self.tasks[task_id].status
                if current_status in [TaskStatus.RUNNING, TaskStatus.QUEUED]:
                    return False
            
            self.tasks[task_id] = TaskInfo(
                id=task_id, 
                title=f"系统: 安装 {plugin_name}", 
                status=TaskStatus.QUEUED,
                message="准备安装环境..."
            )
        
        task_data = {
            "type": "install",
            "id": task_id,
            "plugin_name": plugin_name
        }
        
        future = self.executor.submit(self._safe_process_task_wrapper, task_data)
        self.futures[task_id] = future
        return True
    
    def add_batch_task(self, video_id: str, title: str, session: requests.Session, 
                      target_types: List[str], whisper_config: Dict = None):
        """创建视频下载任务"""
        with self.lock:
            final_task_id = video_id
            counter = 1
            while final_task_id in self.tasks:
                final_task_id = f"{video_id}_{counter}"
                counter += 1
            
            logger.info(f"添加新任务: {final_task_id} (原始ID: {video_id}, 标题: {title})")
            self.tasks[final_task_id] = TaskInfo(id=final_task_id, title=title, status=TaskStatus.QUEUED)
        
        task_data = {
            "type": "download",
            "id": final_task_id,
            "video_id": video_id,
            "cookies": session.cookies.get_dict(), 
            "title": title,
            "target_types": target_types,
            "whisper_config": whisper_config or {},
            "retry_count": 0
        }
        
        future = self.executor.submit(self._safe_process_task_wrapper, task_data)
        self.futures[final_task_id] = future
        return True
    
    def get_all_tasks(self) -> List[TaskInfo]:
        """获取所有任务列表"""
        with self.lock:
            return list(self.tasks.values())
    
    def _update_task(self, task_id, **kwargs):
        """更新任务状态并广播"""
        with self.lock:
            task = self.tasks.get(task_id)
            if not task: 
                return
            
            if task.status in [TaskStatus.FAILED, TaskStatus.COMPLETED]:
                new_status = kwargs.get("status")
                if new_status and new_status not in [TaskStatus.FAILED, TaskStatus.COMPLETED]:
                    logger.debug(f"Task {task_id} 已处于终态 {task.status}，忽略状态更新为 {new_status}")
                    return
                if not new_status:
                    return
            
            for k, v in kwargs.items():
                if hasattr(task, k):
                    setattr(task, k, v)
            
            try:
                task_data = {
                    'id': task.id,
                    'title': task.title,
                    'status': task.status.value if hasattr(task.status, 'value') else str(task.status),
                    'progress': task.progress,
                    'message': task.message,
                    'speed': task.speed,
                    'current_action': task.current_action
                }
                broadcast_task_update_sync(task_data)
            except Exception as e:
                logger.debug(f"广播任务更新失败: {e}")
    
    def _safe_process_task_wrapper(self, data: Dict) -> None:
        """任务执行包装器"""
        task_id = data["id"]
        task_completed_successfully = False
        
        try:
            task_type = data.get("type", "download")
            
            if task_type == "install":
                self._process_install_task(data)
                task_completed_successfully = True
            else:
                result = self._process_download_task(data)
                task_completed_successfully = result if result is not None else False
                
        except SmartclassException as e:
            logger.error(f"Task {task_id} 失败: {e.message}", exc_info=True)
            self._update_task(
                task_id, 
                status=TaskStatus.FAILED, 
                error=e.message,
                message=e.user_message, 
                speed=0
            )
            
        except requests.RequestException as e:
            exc = ErrorHandler.handle_exception(e, context=f"Task {task_id}")
            self._update_task(
                task_id, 
                status=TaskStatus.FAILED, 
                error=exc.message,
                message=exc.user_message, 
                speed=0
            )
            
        except (FileNotFoundError, PermissionError, OSError) as e:
            exc = ErrorHandler.handle_exception(e, context=f"Task {task_id}")
            self._update_task(
                task_id, 
                status=TaskStatus.FAILED, 
                error=exc.message,
                message=exc.user_message, 
                speed=0
            )
            
        except Exception as e:
            exc = ErrorHandler.handle_exception(e, context=f"Task {task_id}")
            logger.error(f"Task {task_id} 未知错误: {exc.message}", exc_info=True)
            self._update_task(
                task_id, 
                status=TaskStatus.FAILED, 
                error=exc.message,
                message=exc.user_message, 
                speed=0
            )
            
        finally:
            needs_update = False
            with self.lock:
                task = self.tasks.get(task_id)
                if task and not task_completed_successfully:
                    if task.status not in [TaskStatus.FAILED, TaskStatus.COMPLETED]:
                        needs_update = True
            
            if needs_update:
                logger.warning(f"Task {task_id} 未正常完成，修正状态为 FAILED")
                self._update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message="任务异常终止",
                    speed=0
                )
            
            if task_id in self.futures:
                del self.futures[task_id]
    
    def _process_install_task(self, data):
        """执行插件安装流程"""
        task_id = data["id"]
        plugin_name = data["plugin_name"]
        
        self._update_task(task_id, status=TaskStatus.RUNNING, progress=0, message="正在初始化...")
        
        def status_callback(msg):
            self._update_task(task_id, message=msg)
        
        try:
            from ..plugins.plugin_manager import plugin_manager
            
            logger.info(f"开始安装插件: {plugin_name}")
            plugin_manager.install_plugin(plugin_name, status_callback=status_callback)
            
            logger.info(f"插件 {plugin_name} 安装成功")
            self._update_task(task_id, status=TaskStatus.COMPLETED, progress=100, 
                            message="安装成功！", speed=0)
            return True
        except subprocess.CalledProcessError as e:
            error_msg = f"安装命令执行失败 (退出码 {e.returncode})"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise Exception(error_msg)
        except Exception as e:
            logger.error(f"插件 {plugin_name} 安装失败: {e}", exc_info=True)
            raise Exception(f"安装失败: {str(e)}")
    
    def _process_download_task(self, data):
        """执行完整下载流程"""
        task_id = data["id"]
        video_id = data["video_id"]
        target_types = data["target_types"]
        whisper_config = data.get("whisper_config", {})
        
        task_success = False
        cookies = data.get("cookies", {})
        session = requests.Session()
        session.cookies.update(cookies)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://njupt.smartclass.cn/"
        })
        
        # 导入必要的模块
        from ..core.smartclass_client import SmartclassClient
        from io import BytesIO
        from lxml import etree
        from urllib.parse import urljoin
        
        self._update_task(task_id, status=TaskStatus.RUNNING, progress=0.0, 
                         message="正在解析课程信息...")
        
        # 获取视频信息（带重试机制，增加容错性）
        client = SmartclassClient(session)
        info = None
        config = config_manager.get()
        max_auth_retries = config.max_retries
        for attempt in range(max_auth_retries):
            try:
                info = client.get_video_info_by_id(video_id)
                break
            except Exception as e:
                if attempt == max_auth_retries - 1:
                    raise e
                logger.warning(f"获取视频信息失败，重试 ({attempt+1}/{max_auth_retries}): {e}")
                self._update_task(task_id, message=f"网络延迟，等待重试({attempt+1})...")
                time.sleep(config.retry_delay)
        
        # 获取配置
        saved_config = config_manager.get()
        use_root = saved_config.download_dir
        
        # 处理课程名（去除非法字符）
        safe_course = info.course_name
        for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            safe_course = safe_course.replace(char, '_')
        safe_course = safe_course.strip()
        
        # 格式化时间文件夹名：YYYYMMDD HHMM_HHMM
        date_str = info.start_time.strftime("%Y%m%d")
        folder_name = f"{date_str} {info.start_time.strftime('%H%M')}_{info.stop_time.strftime('%H%M')}"
        
        # 创建目录结构：课程名/时间/
        course_dir = os.path.join(use_root, safe_course)
        base_dir = os.path.join(course_dir, folder_name)
        
        # 确保目录存在
        try:
            if not os.path.exists(base_dir):
                os.makedirs(base_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"创建目录 {base_dir} 失败: {e}")
            raise Exception(f"无法创建下载目录: {e}")
        
        logger.info(f"Task {task_id}: 目标目录 - {base_dir}")
        
        # 检查视频段信息
        if not info.segments:
            raise Exception("该课程没有分段信息")
        
        seg = info.segments[0]
        
        # 定义进度范围
        RANGE_DOWNLOAD = (0.0, 60.0)
        RANGE_PPT = (60.0, 80.0)
        RANGE_WHISPER = (80.0, 99.0)
        
        # 解析 Whisper 配置
        whisper_url = whisper_config.get("api_url") or saved_config.whisper_url
        whisper_tracks_map = whisper_config.get("tracks", {})
        whisper_targets = [t for t, enabled in whisper_tracks_map.items() if enabled]
        
        need_ppt = "PPT" in target_types
        download_list = [t for t in target_types if t != "PPT"]
        
        # 如果需要 PPT 但没有下载 VGA，自动添加
        if need_ppt and "VGA" not in download_list:
            download_list.append("VGA")
        
        # === 阶段 1: 解析 XML 获取视频源 ===
        valid_sources = []
        xml_download_success = False
        tree = None
        
        try:
            self._update_task(task_id, message="正在获取视频索引...")
            xml_resp = session.get(seg.index_file_uri, timeout=saved_config.network_timeout)
            xml_resp.raise_for_status()
            tree = etree.parse(BytesIO(xml_resp.content))
            
            # 检查哪些视频源存在
            for src_type in ["Video1", "Video2", "VGA"]:
                if tree.xpath(f"/Info/{src_type}[@Src != '']/@Src"):
                    valid_sources.append(src_type)
            
            xml_download_success = True
            logger.info(f"Task {task_id}: XML 解析成功，找到视频源: {valid_sources}")
            
        except Exception as xml_e:
            logger.warning(f"Task {task_id}: XML索引获取失败 ({xml_e})，尝试检测本地缓存...")
            
            # 检查本地是否有文件
            local_found = []
            for p_type in ["Video1", "Video2", "VGA"]:
                p_path = os.path.join(base_dir, f"{p_type}.mp4")
                if self._is_file_valid(p_path, min_size=1024*1024):
                    local_found.append(p_type)
            
            if local_found:
                logger.info(f"Task {task_id}: 检测到本地文件 {local_found}，进入离线模式")
                self._update_task(task_id, message="服务器离线，使用本地文件...")
                valid_sources = local_found
                xml_download_success = False
            else:
                raise Exception(f"无法获取视频索引且无本地文件: {xml_e}")
        
        if not valid_sources:
            raise Exception("未找到有效视频源")
        
        # 如果有 Whisper 任务，确保对应的视频在下载列表中
        if whisper_targets:
            for t in whisper_targets:
                if t in valid_sources and t not in download_list:
                    download_list.append(t)
        
        # === 阶段 2: 下载视频文件 ===
        dl_start, dl_end = RANGE_DOWNLOAD
        total_dl_items = len(download_list)
        
        if total_dl_items == 0:
            self._update_task(task_id, progress=dl_end)
        else:
            per_file_width = (dl_end - dl_start) / total_dl_items
            
            self._update_task(task_id, status=TaskStatus.WAITING, message="等待下载队列...")
            
            with self.download_sem:
                self._update_task(task_id, status=TaskStatus.RUNNING)
                
                for idx, v_type in enumerate(download_list):
                    current_file_base_progress = dl_start + (idx * per_file_width)
                    next_progress = current_file_base_progress + per_file_width
                    video_path = os.path.join(base_dir, f"{v_type}.mp4")
                    
                    # 检查文件是否已存在
                    if self._is_file_valid(video_path, min_size=1024*1024):
                        logger.info(f"Task {task_id}: {v_type} 已存在，跳过")
                        self._update_task(task_id, message=f"{v_type} 已存在，跳过", 
                                        progress=next_progress)
                        continue
                    
                    # 如果 XML 解析失败，无法下载
                    if not xml_download_success:
                        self._update_task(task_id, message=f"{v_type} 缺失且无网络", 
                                        status=TaskStatus.FAILED)
                        raise Exception(f"需要下载 {v_type} 但服务器无法连接 (Index.xml 失败)")
                    
                    # 从 XML 中获取视频 URL
                    srcs = tree.xpath(f"/Info/{v_type}[@Src != '']/@Src")
                    if not srcs:
                        logger.warning(f"Task {task_id}: {v_type} 在 XML 中不存在")
                        self._update_task(task_id, progress=next_progress)
                        continue
                    
                    # 拼接完整 URL
                    video_url = urljoin(seg.index_file_uri, srcs[0])
                    logger.info(f"Task {task_id}: 开始下载 {v_type} from {video_url}")
                    
                    # 下载文件（带重试）
                    download_success = False
                    STEP_RETRIES = saved_config.max_retries
                    
                    for attempt in range(STEP_RETRIES):
                        try:
                            if attempt > 0 and os.path.exists(video_path):
                                os.remove(video_path)
                            
                            # 调用下载服务
                            def update_callback(tid, **kwargs):
                                self._update_task(tid, **kwargs)
                            
                            self.download_service.download_file_monitor(
                                session, video_url, video_path, task_id,
                                current_file_base_progress, per_file_width, v_type,
                                update_callback
                            )
                            
                            # 验证文件
                            if self._is_file_valid(video_path, min_size=1024*1024):
                                download_success = True
                                logger.info(f"Task {task_id}: {v_type} 下载成功")
                                break
                            else:
                                raise Exception("文件校验失败")
                                
                        except Exception as e:
                            if attempt < STEP_RETRIES - 1:
                                logger.warning(f"Task {task_id}: {v_type} 下载失败 (尝试 {attempt+1}/{STEP_RETRIES}): {e}")
                                self._update_task(task_id, message=f"{v_type} 网络波动，等待重试({attempt+1}/{STEP_RETRIES})...")
                                time.sleep(saved_config.retry_delay)
                            else:
                                logger.error(f"Task {task_id}: {v_type} 下载最终失败: {e}")
                    
                    if not download_success:
                        raise Exception(f"{v_type} 下载失败，已重试 {STEP_RETRIES} 次")
        
        self._update_task(task_id, progress=dl_end, speed=0)
        
        # === 阶段 3: PPT 提取 ===
        ppt_start, ppt_end = RANGE_PPT
        if not need_ppt:
            self._update_task(task_id, progress=ppt_end)
        else:
            vga_path = os.path.join(base_dir, "VGA.mp4")
            pdf_path = os.path.join(base_dir, "Slides.pdf")
            
            if self._is_file_valid(pdf_path, min_size=1024):
                logger.info(f"Task {task_id}: PPT 已存在，跳过")
                self._update_task(task_id, current_action="提取 PPT", 
                                message="PPT 已存在，跳过", progress=ppt_end)
            elif not os.path.exists(vga_path):
                logger.warning(f"Task {task_id}: 无VGA视频，跳过PPT")
                self._update_task(task_id, message="无VGA视频，跳过PPT", progress=ppt_end)
            else:
                self._update_task(task_id, current_action="提取 PPT", 
                                message="检查 PPT 插件...", progress=ppt_start)
                
                # 使用插件提取 PPT
                if self.ppt_service:
                    try:
                        from ..plugins.plugin_manager import plugin_manager
                        
                        plugin_name = "slides_extractor"
                        # 检查安装状态（不检查运行状态，避免 HTTP 请求）
                        status = plugin_manager.get_plugin_status(plugin_name, check_running=False)
                        
                        if not status["installed"]:
                            logger.warning(f"Task {task_id}: PPT插件未安装，跳过")
                            self._update_task(task_id, message="PPT插件未安装，跳过", progress=ppt_end)
                        else:
                            # 自动唤醒服务（避免重复启动）
                            self._update_task(task_id, message="正在唤醒 PPT 提取服务...")
                            
                            # 尝试启动服务，如果已在运行则跳过
                            start_result = plugin_manager.start_service(plugin_name)
                            if start_result:
                                # 只有真正启动了新进程才等待
                                logger.info(f"Task {task_id}: PPT 新进程已启动，等待服务就绪...")
                                service_started = False
                                for _ in range(60):
                                    time.sleep(1)
                                    if plugin_manager.get_plugin_status(plugin_name)["running"]:
                                        service_started = True
                                        break
                                
                                if not service_started:
                                    raise Exception("PPT 提取服务启动超时")
                            else:
                                # 服务已在运行，直接使用
                                logger.info(f"Task {task_id}: PPT 服务已在运行，直接使用")
                            
                            # 调用服务
                            self._update_task(task_id, message="正在分析幻灯片 (请稍候)...", 
                                            progress=ppt_start + 5)
                            
                            ppt_success = False
                            base_url = plugin_manager.get_service_url(plugin_name)
                            if not base_url:
                                raise Exception("无法获取 PPT 插件服务地址")
                            
                            api_url = f"{base_url}/extract_slides"
                            STEP_RETRIES = saved_config.max_retries
                            
                            for attempt in range(STEP_RETRIES):
                                try:
                                    payload = {
                                        "video_path": os.path.abspath(vga_path),
                                        "output_path": os.path.abspath(pdf_path),
                                        "threshold": 0.02,
                                        "min_time_gap": 3.0
                                    }
                                    resp = requests.post(api_url, json=payload, timeout=900)
                                    
                                    if resp.status_code == 200:
                                        result = resp.json()
                                        if result.get("status") == "success" and self._is_file_valid(pdf_path, min_size=1024):
                                            ppt_success = True
                                            logger.info(f"Task {task_id}: PPT 提取成功")
                                            break
                                    else:
                                        logger.error(f"PPT Plugin Error ({resp.status_code}): {resp.text}")
                                        
                                except Exception as e:
                                    if attempt < STEP_RETRIES - 1:
                                        logger.error(f"PPT Request Error: {e}")
                                        self._update_task(task_id, message=f"PPT 服务响应慢，等待重试({attempt+1})...")
                                        time.sleep(saved_config.retry_delay)
                            
                            if not ppt_success:
                                error_msg = "PPT 生成失败"
                                logger.warning(f"Task {task_id}: {error_msg}")
                                self._update_task(task_id, status=TaskStatus.FAILED, 
                                                message=error_msg, error=error_msg, speed=0)
                                raise Exception(error_msg)
                    
                    except Exception as e:
                        logger.error(f"Task {task_id}: PPT 提取异常 - {e}", exc_info=True)
                        # 如果是必需的 PPT 提取失败，应该抛出异常
                        raise Exception(f"PPT 提取失败: {str(e)}")
                
                self._update_task(task_id, progress=ppt_end)
        
        # === 阶段 4: Whisper/FunASR 识别 ===
        w_start, w_end = RANGE_WHISPER
        final_whisper_targets = []
        
        if whisper_targets:
            final_whisper_targets = [t for t in whisper_targets if t in valid_sources]
        
        if not final_whisper_targets:
            self._update_task(task_id, progress=w_end)
        else:
            # 判断目标插件类型
            target_plugin_name = "whisper"
            if ":8001" in whisper_url or "funasr" in whisper_url.lower():
                target_plugin_name = "funasr"
            
            # 本地自动唤醒逻辑
            if "127.0.0.1" in whisper_url or "localhost" in whisper_url:
                try:
                    from ..plugins.plugin_manager import plugin_manager
                    
                    # 检查是否已安装（不检查运行状态，避免 HTTP 请求）
                    status = plugin_manager.get_plugin_status(target_plugin_name, check_running=False)
                    if status["installed"]:
                        # 检查是否首次启动（需要下载模型）
                        is_first_run = plugin_manager.is_first_run(target_plugin_name)
                        
                        if is_first_run:
                            self._update_task(task_id, message=f"正在唤醒 {target_plugin_name} (首次启动需下载模型，可能需要几分钟)...", 
                                            current_action="启动服务")
                        else:
                            self._update_task(task_id, message=f"正在唤醒 {target_plugin_name}...", 
                                            current_action="启动服务")
                        
                        # 尝试启动服务，如果已在运行则跳过
                        start_result = plugin_manager.start_service(target_plugin_name)
                        if start_result:
                            # 只有真正启动了新进程才等待
                            logger.info(f"Task {task_id}: {target_plugin_name} 新进程已启动，等待服务就绪...")
                            service_started = False
                            for _ in range(600):
                                time.sleep(1)
                                if plugin_manager.get_plugin_status(target_plugin_name)["running"]:
                                    service_started = True
                                    break
                            
                            if not service_started:
                                raise Exception(f"{target_plugin_name} 服务启动超时")
                        else:
                            # 服务已在运行，直接使用
                            logger.info(f"Task {task_id}: {target_plugin_name} 服务已在运行，直接使用")
                    else:
                        logger.warning(f"Task {task_id}: {target_plugin_name} not installed, skipping.")
                        self._update_task(task_id, message=f"{target_plugin_name}未安装，跳过")
                        final_whisper_targets = []
                    
                    running_url = plugin_manager.get_service_url(target_plugin_name)
                    if running_url:
                        logger.info(f"检测到本地插件运行于: {running_url}，覆盖配置地址。")
                        whisper_url = running_url
                except Exception as e:
                    logger.error(f"插件管理器访问失败: {e}")
            
            total_w_count = len(final_whisper_targets)
            if total_w_count > 0:
                per_w_width = (w_end - w_start) / total_w_count
                
                for idx, t_type in enumerate(final_whisper_targets):
                    current_w_base = w_start + (idx * per_w_width)
                    next_w_progress = current_w_base + per_w_width
                    
                    target_srt_path = os.path.join(base_dir, f"{t_type}.srt")
                    if self._is_file_valid(target_srt_path, min_size=10):
                        logger.info(f"Task {task_id}: {t_type} 字幕已存在，跳过")
                        self._update_task(task_id, message=f"{t_type} 字幕已存在，跳过", 
                                        progress=next_w_progress, speed=0)
                        continue
                    
                    self._update_task(task_id, current_action=f"转写 {t_type}", 
                                    message="提取音频轨道...", progress=current_w_base, speed=0)
                    
                    src_video_path = os.path.join(base_dir, f"{t_type}.mp4")
                    target_wav_path = os.path.join(base_dir, f"audio_{t_type}.wav")
                    
                    # 转换音频
                    if not self._is_file_valid(target_wav_path, min_size=1024):
                        try:
                            self.transcribe_service.convert_video_to_wav(src_video_path, target_wav_path)
                        except Exception as e:
                            logger.error(f"Wav convert fail: {e}")
                            raise Exception(f"{t_type} 音频转换失败: {e}")
                    
                    self._update_task(task_id, status=TaskStatus.WAITING, 
                                    message=f"等待语音识别服务...")
                    
                    with self.whisper_sem:
                        self._update_task(task_id, status=TaskStatus.RUNNING, 
                                        message=f"{target_plugin_name} 正在识别 {t_type}...")
                        
                        whisper_success = False
                        STEP_RETRIES = saved_config.max_retries
                        
                        for attempt in range(STEP_RETRIES):
                            try:
                                # 检查插件是否还在
                                try:
                                    from ..plugins.plugin_manager import plugin_manager
                                    current_plugin_status = plugin_manager.get_plugin_status(
                                        target_plugin_name, check_running=False)
                                    if not current_plugin_status["installed"]:
                                        raise Exception(f"致命错误: 插件 {target_plugin_name} 已被卸载，停止重试")
                                except:
                                    pass
                                
                                # 等待服务可用（增加超时和等待时间）
                                retry_c = 0
                                max_check_retries = 30
                                while not self._check_whisper_server(whisper_url):
                                    retry_c += 1
                                    if retry_c > max_check_retries:
                                        raise Exception("服务连接超时")
                                    time.sleep(3)
                                
                                # 调用 API
                                generated_srt = None
                                if target_plugin_name == "funasr":
                                    generated_srt = self.transcribe_service.call_funasr_api(
                                        whisper_url, target_wav_path, base_dir)
                                else:
                                    generated_srt = self.transcribe_service.call_whisper_api(
                                        whisper_url, target_wav_path, base_dir)
                                
                                if generated_srt and os.path.exists(generated_srt):
                                    if os.path.exists(target_srt_path):
                                        os.remove(target_srt_path)
                                    os.rename(generated_srt, target_srt_path)
                                    if self._is_file_valid(target_srt_path, min_size=10):
                                        whisper_success = True
                                        logger.info(f"Task {task_id}: {t_type} 转写成功")
                                        break
                                
                                if attempt < STEP_RETRIES - 1:
                                    self._update_task(task_id, message=f"识别服务响应慢，等待重试({attempt+1})...")
                                    time.sleep(saved_config.retry_delay)
                                
                            except Exception as e:
                                if attempt < STEP_RETRIES - 1:
                                    logger.error(f"API Attempt {attempt} failed: {e}")
                                    time.sleep(saved_config.retry_delay)
                                else:
                                    logger.error(f"API 最终失败: {e}")
                        
                        # 清理临时文件
                        try:
                            os.remove(target_wav_path)
                        except:
                            pass
                        
                        if not whisper_success:
                            error_msg = f"{t_type} 转写失败（已重试{STEP_RETRIES}次）"
                            logger.error(f"Task {task_id}: {error_msg}")
                            # 转写失败直接抛出异常，让外层处理器统一处理状态
                            raise Exception(error_msg)
                        
                        self._update_task(task_id, progress=next_w_progress)
        
        # === 任务完成 ===
        # 最终验证：检查所有必需的文件是否都已生成
        missing_files = []
        
        # 检查下载的视频文件
        for v_type in download_list:
            video_path = os.path.join(base_dir, f"{v_type}.mp4")
            if not self._is_file_valid(video_path, min_size=1024*1024):
                missing_files.append(f"{v_type}.mp4")
        
        # 检查 PPT（如果需要）
        if need_ppt:
            pdf_path = os.path.join(base_dir, "Slides.pdf")
            if not self._is_file_valid(pdf_path, min_size=1024):
                missing_files.append("Slides.pdf")
        
        # 检查字幕文件（如果需要）
        for t_type in final_whisper_targets:
            srt_path = os.path.join(base_dir, f"{t_type}.srt")
            if not self._is_file_valid(srt_path, min_size=10):
                missing_files.append(f"{t_type}.srt")
        
        # 如果有文件缺失，标记为失败
        if missing_files:
            error_msg = f"任务未完全完成，缺失文件: {', '.join(missing_files)}"
            logger.error(f"Task {task_id}: {error_msg}")
            self._update_task(task_id, status=TaskStatus.FAILED, 
                            message=error_msg, error=error_msg, speed=0)
            raise Exception(error_msg)
        
        # 所有文件都已生成，标记为成功
        task_success = True
        self._update_task(task_id, status=TaskStatus.COMPLETED, progress=100.0, 
                        message="所有任务完成", current_action="结束", speed=0)
        logger.info(f"Task {task_id}: 所有任务完成")
        
        # 返回成功标记，供异常处理使用
        return task_success
    
    def _check_whisper_server(self, url):
        """检查语音识别服务是否在线"""
        try:
            config = config_manager.get()
            requests.get(url, timeout=config.network_timeout)
            return True
        except:
            return False
    
    def abort_plugin_task(self, plugin_name: str):
        """强制终止插件相关的运行中任务"""
        target_install_id = f"install_{plugin_name}"
        
        with self.lock:
            if target_install_id in self.tasks:
                task = self.tasks[target_install_id]
                if task.status in [TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.WAITING]:
                    task.status = TaskStatus.FAILED
                    task.message = "已由用户强制终止 (插件卸载)"
                    task.error = "用户手动卸载"
                    task.speed = 0
                    task.progress = 0
                    task.current_action = "已停止"
                    logger.info(f"安装任务 {target_install_id} 已被强行中止")
            
            # 中止正在使用该插件的运行中任务
            for task in self.tasks.values():
                if task.status == TaskStatus.RUNNING:
                    action_keywords = []
                    
                    if plugin_name == "whisper":
                        action_keywords = ["Whisper", "转写", "识别"]
                    elif plugin_name == "funasr":
                        action_keywords = ["FunASR", "转写", "识别"]
                    elif plugin_name == "slides_extractor":
                        action_keywords = ["PPT", "幻灯片"]
                    
                    is_related = any(k in task.current_action for k in action_keywords) or \
                                any(k in task.message for k in action_keywords)
                    
                    if is_related:
                        task.status = TaskStatus.FAILED
                        task.error = f"插件 {plugin_name} 已被卸载，任务强制中止"
                        task.message = "失败: 依赖插件已卸载"
                        task.speed = 0
                        logger.info(f"运行中任务 {task.id} 因插件 {plugin_name} 卸载而被中止")

