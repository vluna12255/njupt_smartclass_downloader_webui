"""配置管理器"""
import os
import json
import sys
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional
from pathlib import Path

from .logger import get_logger

logger = get_logger('config_manager')

def get_app_root():
    """获取应用根目录"""
    is_frozen = getattr(sys, 'frozen', False)
    exe_name = os.path.basename(sys.executable).lower()
    is_nuitka = not exe_name.startswith('python')
    
    if is_frozen or is_nuitka:
        return os.path.abspath(os.path.dirname(sys.executable))
    else:
        current_file = os.path.abspath(__file__)
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
        return os.path.dirname(app_dir)

ROOT_DIR = get_app_root()

CONFIG_DIR = os.path.join(ROOT_DIR, "config")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
PLUGINS_DIR = os.path.join(ROOT_DIR, "plugins")     
RUNTIME_DIR = os.path.join(ROOT_DIR, "runtime")     
DOWNLOADS_DIR = os.path.join(ROOT_DIR, "SmartclassDownload")

CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
AUTH_FILE = os.path.join(CONFIG_DIR, "auth.json")

@dataclass
class AppConfig:
    """应用配置数据类"""
    download_dir: str = ""
    auto_login: bool = True
    auto_whisper: bool = False
    asr_engine: str = "funasr" 
    whisper_url: str = "http://127.0.0.1:8001/" 
    
    max_download_concurrent: int = 3
    max_chunk_workers: int = 8
    enable_resume: bool = True
    
    # 网络超时和重试配置（新增）
    network_timeout: int = 30  # 网络请求超时时间（秒）
    download_timeout: int = 120  # 下载超时时间（秒）
    max_retries: int = 2  # 最大重试次数
    retry_delay: int = 5  # 重试延迟（秒）
    
    default_vga: bool = True
    default_video1: bool = True
    default_video2: bool = False
    default_ppt: bool = False

    default_whisper_vga: bool = False
    default_whisper_video1: bool = False
    default_whisper_video2: bool = False
    
    def validate(self) -> tuple[bool, Optional[str]]:
        """验证配置参数"""
        if not 1 <= self.max_download_concurrent <= 10:
            return False, "下载并发数必须在 1-10 之间"
        
        if not 1 <= self.max_chunk_workers <= 32:
            return False, "分块下载线程数必须在 1-32 之间"
        
        if not 10 <= self.network_timeout <= 300:
            return False, "网络超时时间必须在 10-300 秒之间"
        
        if not 30 <= self.download_timeout <= 600:
            return False, "下载超时时间必须在 30-600 秒之间"
        
        if not 1 <= self.max_retries <= 5:
            return False, "最大重试次数必须在 1-5 之间"
        
        if not 1 <= self.retry_delay <= 30:
            return False, "重试延迟必须在 1-30 秒之间"
        
        if self.asr_engine not in ["whisper", "funasr"]:
            return False, f"不支持的语音识别引擎: {self.asr_engine}"
        
        if self.download_dir:
            try:
                path = Path(self.download_dir)
                if not path.parent.exists():
                    return False, f"下载目录的父目录不存在: {path.parent}"
            except Exception as e:
                return False, f"下载目录路径无效: {e}"
        
        return True, None


class ConfigManager:
    def __init__(self):
        self.config = AppConfig()
        self._init_directories()
        self.load()

    def _init_directories(self):
        """创建必要目录"""
        dirs_to_create = [
            CONFIG_DIR, 
            LOGS_DIR, 
            DOWNLOADS_DIR,
        ]
        for d in dirs_to_create:
            try:
                os.makedirs(d, exist_ok=True)
                logger.debug(f"目录已创建或已存在 {d}")
            except PermissionError:
                logger.error(f"权限不足，无法创建目录 {d}")
            except OSError as e:
                logger.error(f"创建目录失败 {d}: {e}", exc_info=True)

    def load(self):
        """加载配置"""
        default_download_path = os.path.join(ROOT_DIR, "SmartclassDownload")
        
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    valid_keys = self.config.__dataclass_fields__.keys()
                    clean_data = {k: v for k, v in data.items() if k in valid_keys}
                    
                    current = asdict(self.config)
                    current.update(clean_data)
                    self.config = AppConfig(**current)
                logger.info(f"配置加载成功 {CONFIG_FILE}")
            except json.JSONDecodeError as e:
                logger.error(f"配置文件格式错误 {e}，将使用默认值", exc_info=True)
                self.config = AppConfig()
            except PermissionError:
                logger.error(f"无权限读取配置文件 {CONFIG_FILE}")
                self.config = AppConfig()
            except Exception as e:
                logger.error(f"加载配置失败 {e}，将使用默认值", exc_info=True)
                self.config = AppConfig()
        else:
            logger.info("配置文件不存在，初始化默认配置")
            self.save(asdict(self.config))
            
        if not self.config.download_dir:
            self.config.download_dir = default_download_path
            
        try:
            os.makedirs(self.config.download_dir, exist_ok=True)
        except PermissionError:
            logger.error(f"权限不足，无法创建下载目录 {self.config.download_dir}")
        except OSError as e:
            logger.error(f"创建下载目录失败 {e}", exc_info=True)

    def save_auth(self, username: str, password: str):
        """保存登录凭证"""
        if not username or not password:
            logger.warning("尝试保存空的认证信息")
            return
            
        auth_data = {"username": username, "password": password}
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(AUTH_FILE, 'w', encoding='utf-8') as f:
                json.dump(auth_data, f, indent=4, ensure_ascii=False)
            logger.info(f"认证信息已保存 {username}")
        except PermissionError:
            logger.error(f"权限不足，无法保存认证信息到: {AUTH_FILE}")
        except OSError as e:
            logger.error(f"保存认证信息失败: {e}", exc_info=True)

    def get_auth(self) -> Dict[str, str]:
        """读取登录凭证"""
        if os.path.exists(AUTH_FILE):
            try:
                with open(AUTH_FILE, 'r', encoding='utf-8') as f:
                    auth_data = json.load(f)
                    if isinstance(auth_data, dict):
                        return auth_data
                    logger.warning("认证文件格式错误")
            except json.JSONDecodeError:
                logger.error(f"认证文件格式错误 {AUTH_FILE}")
            except PermissionError:
                logger.error(f"无权限读取认证文件 {AUTH_FILE}")
            except Exception as e:
                logger.error(f"读取认证信息失败 {e}", exc_info=True)
        return {}

    def save(self, new_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """保存配置"""
        try:
            is_auto_login = new_config.get("auto_login", self.config.auto_login)

            if is_auto_login and ("username" in new_config or "password" in new_config):
                current_auth = self.get_auth()
                user = new_config.get("username", current_auth.get("username", ""))
                pwd = new_config.get("password", current_auth.get("password", ""))
                if user and pwd:
                    self.save_auth(user, pwd)

            if "asr_engine" in new_config:
                engine = new_config["asr_engine"]
                if "whisper_url" not in new_config:
                    if engine == "whisper":
                        new_config["whisper_url"] = "http://127.0.0.1:8000/"  
                    elif engine == "funasr":
                        new_config["whisper_url"] = "http://127.0.0.1:8001/"

            valid_keys = self.config.__dataclass_fields__.keys()
            current = asdict(self.config)
            
            special_keys = {'username', 'password'}
            
            for k, v in new_config.items():
                if k in valid_keys:
                    current[k] = v
                elif k not in special_keys:
                    logger.warning(f"忽略未知配置项 {k}")

            new_config_obj = AppConfig(**current)
            
            is_valid, error_msg = new_config_obj.validate()
            if not is_valid:
                logger.error(f"配置验证失败 {error_msg}")
                return False, error_msg
            
            self.config = new_config_obj

            if self.config.download_dir:
                try:
                    os.makedirs(self.config.download_dir, exist_ok=True)
                except PermissionError:
                    error_msg = f"权限不足，无法创建下载目录 {self.config.download_dir}"
                    logger.error(error_msg)
                    return False, error_msg
                except OSError as e:
                    error_msg = f"无法创建下载目录 {e}"
                    logger.error(error_msg)
                    return False, error_msg
            
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(asdict(self.config), f, indent=4, ensure_ascii=False)
            logger.info(f"配置已保存 Engine={self.config.asr_engine}, URL={self.config.whisper_url}")
            return True, None
            
        except PermissionError:
            error_msg = f"权限不足，无法写入配置文件 {CONFIG_FILE}"
            logger.error(error_msg)
            return False, error_msg
        except OSError as e:
            error_msg = f"无法写入配置文件 {e}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg
        except Exception as e:
            error_msg = f"保存配置时发生未知错误 {e}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg

    def get(self) -> AppConfig:
        return self.config

config_manager = ConfigManager()