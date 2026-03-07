"""PPT提取服务"""
import os
import time
import requests

from ..utils.logger import get_logger

logger = get_logger('ppt')


class PPTService:
    """PPT提取服务"""
    
    def __init__(self, plugin_manager):
        self.plugin_manager = plugin_manager
    
    def extract_slides(self, video_path, output_pdf_path, service_url, 
                      threshold=0.02, min_time_gap=3.0, max_retries=3):
        """从视频中提取幻灯片"""
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return False
        
        api_url = f"{service_url}/extract_slides"
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "video_path": os.path.abspath(video_path),
                    "output_path": os.path.abspath(output_pdf_path),
                    "threshold": threshold,
                    "min_time_gap": min_time_gap
                }
                
                resp = requests.post(api_url, json=payload, timeout=900)
                
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("status") == "success" and os.path.exists(output_pdf_path):
                        logger.info(f"PPT提取成功: {output_pdf_path}")
                        return True
                else:
                    logger.error(f"PPT Plugin Error ({resp.status_code}): {resp.text}")
                    
            except Exception as e:
                logger.warning(f"PPT Request Error (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
        
        return False
    
    def ensure_service_running(self, plugin_name="slides_extractor"):
        """确保PPT提取服务运行"""
        status = self.plugin_manager.get_plugin_status(plugin_name)
        
        if not status["installed"]:
            logger.warning(f"{plugin_name} 未安装")
            return False
        
        if not status["running"]:
            try:
                logger.info(f"正在启动 {plugin_name} 服务...")
                self.plugin_manager.start_service(plugin_name)
                
                # 等待服务启动
                for _ in range(60):
                    time.sleep(1)
                    if self.plugin_manager.get_plugin_status(plugin_name)["running"]:
                        logger.info(f"{plugin_name} 服务已启动")
                        return True
                
                logger.error(f"{plugin_name} 服务启动超时")
                return False
            except Exception as e:
                logger.error(f"启动 {plugin_name} 服务失败: {e}")
                return False
        
        return True

