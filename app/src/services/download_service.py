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

        # ETA 二次 EWMA 状态（跨 monitor_loop 调用保持）
        _eta_state = {"smoothed_eta": None, "last_eta_update": 0.0}
        _ETA_ALPHA = 0.15          # ETA EWMA 平滑系数（越小越稳）
        _ETA_UPDATE_INTERVAL = 2.0  # ETA 最低刷新间隔（秒）

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
            """实时监控下载进度（滑动窗口测速 + ETA 二次 EWMA 平滑）"""
            # 滑动时间窗口：保存 (timestamp, bytes_downloaded) 的环形缓冲
            WINDOW_SECONDS = 6.0     # 窗口宽度（秒）
            window: list = []        # 每个元素为 (t, bytes)
            SPEED_ALPHA = 0.25       # 速度 EWMA 系数
            ewma_speed = 0.0

            while not state["stop"]:
                time.sleep(0.2)
                with self.state_lock:
                    curr_dl = state["downloaded"]
                    curr_t = time.time()
                    diff_t = curr_t - state["last_time"]

                    if diff_t > 0.15:
                        # --- 滑动窗口：追加新采样点并淘汰过期点 ---
                        window.append((curr_t, curr_dl))
                        cutoff = curr_t - WINDOW_SECONDS
                        while len(window) > 1 and window[0][0] < cutoff:
                            window.pop(0)

                        # 用窗口首尾计算区间平均速度
                        if len(window) >= 2:
                            dt = window[-1][0] - window[0][0]
                            db = window[-1][1] - window[0][1]
                            window_speed = db / dt if dt > 0 else 0.0
                        else:
                            window_speed = 0.0

                        # 对窗口速度再做一次 EWMA 平滑
                        if ewma_speed == 0.0:
                            ewma_speed = window_speed
                        else:
                            ewma_speed = SPEED_ALPHA * window_speed + (1 - SPEED_ALPHA) * ewma_speed

                        state["last_downloaded"] = curr_dl
                        state["last_time"] = curr_t

                        percent = curr_dl / total_size if total_size > 0 else 0

                        # --- ETA 二次 EWMA + 降频刷新 ---
                        if ewma_speed > 1024:
                            raw_eta = (total_size - curr_dl) / ewma_speed
                            # 首次直接赋值，后续做 EWMA 平滑
                            if _eta_state["smoothed_eta"] is None:
                                _eta_state["smoothed_eta"] = raw_eta
                            else:
                                _eta_state["smoothed_eta"] = (
                                    _ETA_ALPHA * raw_eta
                                    + (1 - _ETA_ALPHA) * _eta_state["smoothed_eta"]
                                )

                            # 降频：每 _ETA_UPDATE_INTERVAL 秒才更新显示文字
                            if curr_t - _eta_state["last_eta_update"] >= _ETA_UPDATE_INTERVAL:
                                _eta_state["last_eta_update"] = curr_t
                                s = _eta_state["smoothed_eta"]
                                if s < 60:
                                    eta_msg = f"剩余 {int(s)}秒"
                                elif s < 3600:
                                    eta_msg = f"剩余 {int(s/60)}分{int(s)%60}秒"
                                else:
                                    eta_msg = f"剩余 {int(s/3600)}小时{int(s%3600/60)}分"
                                state["_eta_msg"] = eta_msg
                        else:
                            state.setdefault("_eta_msg", "计算中...")

                        eta_msg = state.get("_eta_msg", "计算中...")

                        update_callback(task_id, downloaded_size=curr_dl,
                                      progress=base_progress + (percent * chunk_progress_width),
                                      speed=ewma_speed,
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

            # 滑动时间窗口（单线程版）
            WINDOW_SECONDS = 6.0
            window: list = []          # [(timestamp, bytes), ...]
            SPEED_ALPHA = 0.25
            ewma_speed = 0.0

            # ETA 二次 EWMA + 降频
            _ETA_ALPHA = 0.15
            _ETA_UPDATE_INTERVAL = 2.0
            smoothed_eta = None
            last_eta_update = 0.0
            eta_msg = "计算中..."

            tmp_file_path = f"{path}.{task_id}.tmp"

            with open(tmp_file_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        curr_t = time.time()
                        diff_t = curr_t - last_t

                        # 每 200ms 更新一次速度
                        if diff_t > 0.2:
                            # --- 滑动窗口 ---
                            window.append((curr_t, downloaded))
                            cutoff = curr_t - WINDOW_SECONDS
                            while len(window) > 1 and window[0][0] < cutoff:
                                window.pop(0)

                            if len(window) >= 2:
                                dt = window[-1][0] - window[0][0]
                                db = window[-1][1] - window[0][1]
                                window_speed = db / dt if dt > 0 else 0.0
                            else:
                                window_speed = 0.0

                            # 速度 EWMA
                            if ewma_speed == 0.0:
                                ewma_speed = window_speed
                            else:
                                ewma_speed = SPEED_ALPHA * window_speed + (1 - SPEED_ALPHA) * ewma_speed

                            file_percent = downloaded / total_len if total_len > 0 else 0

                            # --- ETA 二次 EWMA + 降频刷新 ---
                            if ewma_speed > 1024:
                                raw_eta = (total_len - downloaded) / ewma_speed
                                if smoothed_eta is None:
                                    smoothed_eta = raw_eta
                                else:
                                    smoothed_eta = (
                                        _ETA_ALPHA * raw_eta
                                        + (1 - _ETA_ALPHA) * smoothed_eta
                                    )

                                if curr_t - last_eta_update >= _ETA_UPDATE_INTERVAL:
                                    last_eta_update = curr_t
                                    s = smoothed_eta
                                    if s < 60:
                                        eta_msg = f"剩余 {int(s)}秒"
                                    elif s < 3600:
                                        eta_msg = f"剩余 {int(s/60)}分{int(s)%60}秒"
                                    else:
                                        eta_msg = f"剩余 {int(s/3600)}小时{int(s%3600/60)}分"

                            update_callback(task_id, downloaded_size=downloaded,
                                          progress=base_progress + (file_percent * chunk_progress_width),
                                          message=f"下载中... {eta_msg}",
                                          speed=ewma_speed)

                            last_t = curr_t
                            last_downloaded = downloaded
        
        if os.path.exists(path): 
            os.remove(path)
        os.rename(tmp_file_path, path)
        update_callback(task_id, progress=base_progress + chunk_progress_width, speed=0, message="下载完成")

