"""Session管理器"""
import time
import requests
from typing import Optional

from .njupt_sso import NjuptSso, NjuptSsoException
from .smartclass_client import SmartclassClient
from ..utils.config_manager import config_manager
from ..utils.logger import get_logger

logger = get_logger('session')


class SessionManager:
    """全局登录状态管理"""
    
    def __init__(self):
        self.global_session: Optional[requests.Session] = None
        self.smart_class_client: Optional[SmartclassClient] = None
    
    def set_session(self, session: requests.Session, client: SmartclassClient):
        """保存登录会话"""
        self.global_session = session
        self.smart_class_client = client
    
    def get_session(self) -> Optional[requests.Session]:
        """获取当前会话"""
        return self.global_session
    
    def get_client(self) -> Optional[SmartclassClient]:
        """获取客户端"""
        if self.smart_class_client and not self.is_session_valid():
            logger.info("检测到 Session 过期，尝试自动重连...")
            self.perform_auto_login()
        return self.smart_class_client
    
    def is_session_valid(self) -> bool:
        """检查会话是否有效"""
        if not self.global_session:
            return False
        try:
            r = self.global_session.get("https://njupt.smartclass.cn/", 
                                       allow_redirects=False, timeout=5)
            if r.status_code == 302 and "Login" in r.headers.get("Location", ""):
                return False
            return True
        except:
            return False
    
    def perform_auto_login(self):
        """使用保存的凭证自动登录"""
        config = config_manager.get()
        auth = config_manager.get_auth()
        
        username = ""
        password = ""
        
        if auth and "username" in auth:
            username = auth["username"]
            password = auth.get("password", "")
        elif hasattr(config, "username"):
            username = config.username
            password = getattr(config, "password", "")
        
        if username and password:
            logger.info(f"正在尝试使用保存的账号 {username} 自动登录...")
            try:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://njupt.smartclass.cn/",
                    "Origin": "https://njupt.smartclass.cn",
                    "X-Requested-With": "XMLHttpRequest"
                })
                
                sso = NjuptSso(session)
                sso.login(username, password)
                sso.grant_service("https://njupt.smartclass.cn/Login/SSO")
                session.get("https://njupt.smartclass.cn/", timeout=10)
                
                self.global_session = session
                self.smart_class_client = SmartclassClient(session)
                try:
                    self.smart_class_client.get_csrk_token()
                except:
                    pass
                    
                logger.info(">>> 自动登录成功 (SSO) <<<")
                return True, "登录成功"
            except Exception as e:
                error_msg = str(e)
                logger.error(f"自动登录失败: {e}")
                self.smart_class_client = None
                return False, error_msg
        
        return False, "无保存的凭证"

