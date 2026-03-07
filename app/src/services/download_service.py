"""下载服务"""
import os
import time
import shutil
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

from ..utils.logger import get_logger
from ..utils.config_manager import config_manager
from ..utils.disk_checker import check_disk_space
from ..utils.file_merger import merge_files


logger = get_logger('download')


class DownloadService:
    """视频下载服务"""
    
    def __init__(self):
        self.state_lock = threading.Lock()
        self.config = config_manager.get()
    
    def download_file_monitor(self, session, url, path, task_id, base_progress, 
                             chunk_progress_width, v_type, update_callback):
        """下载文件"""
        update_callback(task_id, current_action=f"下载 {v_type}", message="建立连接...", speed=0)
        
        try:
            head_resp = session.head(url, timeout=self.config.network_timeout)
            head_resp.raise_for_status()
            total_size = int(head_resp.headers.get('content-length', 0))
            
            if total_size > 0:
                sufficient, msg = check_disk_space(path, total_size)
                if not sufficient:
                    raise Exception(f"磁盘空间不足: {msg}")
                logger.info(f"磁盘空间检查通过: {v_type}")
            
            accept_ranges = head_resp.headers.get('Accept-Ranges', 'none')
            supports_range = accept_ranges.lower() != 'none'
            
            if total_size > 10 * 1024 * 1024 and supports_range:
                self._download_chunked(session, url, path, task_id, total_size, 
                                      base_progress, chunk_progress_width, update_callback)
            else:
                self._download_simple(session, url, path, task_id, total_size, 
                                     base_progress, chunk_progress_width, update_callback)
                                     
        except requests.RequestException as e:
            logger.error(f"网络请求失败: {e}", exc_info=True)
            update_callback(task_id, message=f"连接失败: {str(e)}", speed=0)
            raise
        except Exception as e:
            logger.warning(f"HEAD 请求失败，回退到简单下载: {e}")
            self._download_simple(session, url, path, task_id, 0, 
                                 base_progress, chunk_progress_width, update_callback)

    def _download_chunked(self, session, url, path, task_id, total_size, 
                         base_progress, chunk_progress_width, update_callback):
        """多线程分块下载"""
        update_callback(task_id, total_size=total_size)
        
        num_chunks = min(self.config.max_chunk_workers, 16)
        chunk_size = total_size // num_chunks

        tmp_path = f"{path}.{task_id}.tmp"
        
        state = {
            "downloaded": 0,
            "last_downloaded": 0,
            "last_time": time.time(),
            "stop": False
        }
        
        def download_part(index, start, end):
            """下载单个分片"""
            part_file = f"{tmp_path}.part{index}"
            
            if os.path.exists(part_file):
                existing_size = os.path.getsize(part_file)
                if existing_size == (end - start + 1):
                    logger.info(f"分片 {index} 已存在，跳过下载")
                    with self.state_lock:
                        state["downloaded"] += existing_size
                    return
                elif existing_size > 0:
                    logger.info(f"分片 {index} 部分存在，从 {existing_size} 继续")
                    start += existing_size
                    with self.state_lock:
                        state["downloaded"] += existing_size
            
            headers = session.headers.copy()
            headers['Range'] = f'bytes={start}-{end}'
            
            max_retries = self.config.max_retries
            for attempt in range(max_retries):
                try:
                    with session.get(url, headers=headers, stream=True, timeout=self.config.download_timeout) as r:
                        r.raise_for_status()
                        mode = 'ab' if os.path.exists(part_file) else 'wb'
                        with open(part_file, mode) as f:
                            for chunk in r.iter_content(chunk_size=512*1024):
                                if chunk:
                                    f.write(chunk)
                                    with self.state_lock:
                                        state["downloaded"] += len(chunk)
                    break
                except requests.RequestException as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"分片 {index} 下载失败，重试 {attempt + 1}/{max_retries}: {e}")
                        time.sleep(self.config.retry_delay + (2 ** attempt))
                    else:
                        logger.error(f"分片 {index} 下载失败，已达最大重试次数")
                        raise
                except Exception as e:
                    logger.error(f"分片 {index} 下载异常: {e}", exc_info=True)
                    raise

        def monitor_loop():
            """实时监控下载进度"""
            speed_samples = []
            max_samples = 5
            
            while not state["stop"]:
                time.sleep(0.2)
                with self.state_lock:
                    curr_dl = state["downloaded"]
                    curr_t = time.time()
                    diff_t = curr_t - state["last_time"]
                    
                    if diff_t > 0.15:
                        instant_speed = (curr_dl - state["last_downloaded"]) / diff_t
                        
                        speed_samples.append(instant_speed)
                        if len(speed_samples) > max_samples:
                            speed_samples.pop(0)
                        
                        smooth_speed = sum(speed_samples) / len(speed_samples) if speed_samples else 0
                        
                        state["last_downloaded"] = curr_dl
                        state["last_time"] = curr_t
                        
                        percent = curr_dl / total_size if total_size > 0 else 0
                        
                        if smooth_speed > 1024:
                            remaining_bytes = total_size - curr_dl
                            eta_seconds = remaining_bytes / smooth_speed
                            
                            if eta_seconds < 60:
                                eta_msg = f"剩余 {int(eta_seconds)}秒"
                            elif eta_seconds < 3600:
                                eta_msg = f"剩余 {int(eta_seconds/60)}分钟"
                            else:
                                eta_msg = f"剩余 {int(eta_seconds/3600)}小时"
                        else:
                            eta_msg = "计算中..."
                        
                        update_callback(task_id, downloaded_size=curr_dl, 
                                      progress=base_progress + (percent * chunk_progress_width),
                                      speed=smooth_speed,
                                      message=f"下载中... {eta_msg}")

        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()

        try:
            with ThreadPoolExecutor(max_workers=num_chunks) as chunk_executor:
                futures = []
                for i in range(num_chunks):
                    start = i * chunk_size
                    end = (i + 1) * chunk_size - 1 if i < num_chunks - 1 else total_size - 1
                    futures.append(chunk_executor.submit(download_part, i, start, end))
                
                # 等待所有分片完成
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"分片下载失败: {e}")
                        for f in futures:
                            f.cancel()
                        raise
        except Exception as e:
            logger.error(f"分块下载失败: {e}", exc_info=True)
            raise
        finally:
            state["stop"] = True
            monitor_thread.join(timeout=2)
            update_callback(task_id, speed=0)

        update_callback(task_id, message="合并分片...", speed=0)
        
        try:
            chunk_files = [f"{tmp_path}.part{i}" for i in range(num_chunks)]
            
            def merge_progress(current, total):
                percent = current / total if total > 0 else 0
                update_callback(task_id, message=f"合并分片 {current}/{total}...", 
                              progress=base_progress + chunk_progress_width * 0.95 + percent * 0.05)
            
            success = merge_files(path, chunk_files, progress_callback=merge_progress)
            
            if not success:
                raise Exception("文件合并失败")
            
            for part_file in chunk_files:
                try:
                    if os.path.exists(part_file):
                        os.remove(part_file)
                except Exception as e:
                    logger.warning(f"删除分片文件失败: {e}")
            
            logger.info(f"文件下载完成: {path} ({total_size} bytes)")
            update_callback(task_id, progress=base_progress + chunk_progress_width, message="下载完成")
            
        except Exception as e:
            logger.error(f"合并分片失败: {e}", exc_info=True)
            raise

    def _download_simple(self, session, url, path, task_id, known_total_size, 
                        base_progress, chunk_progress_width, update_callback):
        """单线程流式下载"""
        download_headers = session.headers.copy()
        download_headers['Connection'] = 'close'
        
        with session.get(url, stream=True, timeout=self.config.download_timeout, headers=download_headers) as resp:
            resp.raise_for_status()
            total_len = int(resp.headers.get('content-length', known_total_size))
            update_callback(task_id, total_size=total_len)
            
            downloaded = 0
            start_t = time.time()
            last_t = start_t
            last_downloaded = 0
            
            speed_samples = []
            max_samples = 5
            
            tmp_file_path = f"{path}.{task_id}.tmp"
            
            with open(tmp_file_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=512 * 1024):  # 减小块大小，更频繁更新
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        curr_t = time.time()
                        diff_t = curr_t - last_t
                        
                        # 每200ms更新一次
                        if diff_t > 0.2:
                            instant_speed = (downloaded - last_downloaded) / diff_t
                            
                            # 速度平滑
                            speed_samples.append(instant_speed)
                            if len(speed_samples) > max_samples:
                                speed_samples.pop(0)
                            smooth_speed = sum(speed_samples) / len(speed_samples) if speed_samples else 0
                            
                            file_percent = downloaded / total_len if total_len > 0 else 0
                            
                            # 计算ETA
                            if smooth_speed > 1024:
                                eta_seconds = (total_len - downloaded) / smooth_speed
                                if eta_seconds < 60:
                                    eta_msg = f"剩余 {int(eta_seconds)}秒"
                                else:
                                    eta_msg = f"剩余 {int(eta_seconds/60)}分钟"
                            else:
                                eta_msg = "计算中..."
                            
                            update_callback(task_id, downloaded_size=downloaded, 
                                          progress=base_progress + (file_percent * chunk_progress_width), 
                                          message=f"下载中... {eta_msg}", 
                                          speed=smooth_speed)
                            
                            last_t = curr_t
                            last_downloaded = downloaded
        
        if os.path.exists(path): 
            os.remove(path)
        os.rename(tmp_file_path, path)
        update_callback(task_id, progress=base_progress + chunk_progress_width, speed=0, message="下载完成")

