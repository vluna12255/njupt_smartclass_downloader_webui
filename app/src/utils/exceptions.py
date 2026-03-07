"""
自定义异常类 - 提供更精确的错误处理
"""
from typing import Optional, Dict, Any
from enum import Enum


class ErrorSeverity(str, Enum):
    """错误严重程度"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(str, Enum):
    """错误分类"""
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    PLUGIN = "plugin"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SmartclassException(Exception):
    """基础异常类 - 提供结构化错误信息"""
    
    def __init__(
        self, 
        message: str, 
        details: str = "",
        user_message: Optional[str] = None,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        recoverable: bool = True,
        retry_after: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.details = details
        self.user_message = user_message or self._generate_user_message(message)
        self.category = category
        self.severity = severity
        self.recoverable = recoverable
        self.retry_after = retry_after  # 建议重试延迟（秒）
        self.context = context or {}
        super().__init__(self.message)
    
    def _generate_user_message(self, message: str) -> str:
        """生成用户友好的错误消息"""
        return translate_error_to_chinese(message)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化"""
        return {
            "message": self.message,
            "user_message": self.user_message,
            "details": self.details,
            "category": self.category.value,
            "severity": self.severity.value,
            "recoverable": self.recoverable,
            "retry_after": self.retry_after,
            "context": self.context
        }


class NetworkException(SmartclassException):
    """网络异常"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.NETWORK)
        kwargs.setdefault('user_message', '网络连接失败，请检查网络设置')
        kwargs.setdefault('recoverable', True)
        kwargs.setdefault('retry_after', 5)
        super().__init__(message, **kwargs)


class DownloadException(SmartclassException):
    """下载异常"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.FILESYSTEM)
        kwargs.setdefault('user_message', '下载失败，请重试')
        kwargs.setdefault('recoverable', True)
        kwargs.setdefault('retry_after', 3)
        super().__init__(message, **kwargs)


class PluginException(SmartclassException):
    """插件异常"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.PLUGIN)
        kwargs.setdefault('user_message', '插件服务异常，请检查插件状态')
        kwargs.setdefault('recoverable', True)
        super().__init__(message, **kwargs)


class TaskCancelledException(SmartclassException):
    """任务被取消"""
    def __init__(self, message: str = "任务已被用户取消", **kwargs):
        kwargs.setdefault('category', ErrorCategory.SYSTEM)
        kwargs.setdefault('severity', ErrorSeverity.INFO)
        kwargs.setdefault('user_message', '任务已取消')
        kwargs.setdefault('recoverable', False)
        super().__init__(message, **kwargs)


class FileValidationException(SmartclassException):
    """文件校验失败"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.VALIDATION)
        kwargs.setdefault('user_message', '文件校验失败，可能已损坏')
        kwargs.setdefault('recoverable', True)
        kwargs.setdefault('retry_after', 0)
        super().__init__(message, **kwargs)


class AuthenticationException(SmartclassException):
    """认证异常"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.AUTHENTICATION)
        kwargs.setdefault('user_message', '登录凭证已失效，请重新登录')
        kwargs.setdefault('recoverable', True)
        super().__init__(message, **kwargs)


class DiskSpaceException(SmartclassException):
    """磁盘空间不足"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.FILESYSTEM)
        kwargs.setdefault('severity', ErrorSeverity.CRITICAL)
        kwargs.setdefault('user_message', '磁盘空间不足，请清理后重试')
        kwargs.setdefault('recoverable', False)
        super().__init__(message, **kwargs)


class ConfigurationException(SmartclassException):
    """配置错误"""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault('category', ErrorCategory.VALIDATION)
        kwargs.setdefault('user_message', '配置错误，请检查设置')
        kwargs.setdefault('recoverable', True)
        super().__init__(message, **kwargs)


def translate_error_to_chinese(error_text: str) -> str:
    """将常见错误翻译成用户友好的中文消息"""
    error_text_lower = error_text.lower()
    
    # 网络相关错误
    network_errors = {
        "connection refused": "连接被拒绝，服务可能未启动",
        "connection error": "网络连接错误，请检查网络",
        "connection reset": "连接被重置，请重试",
        "timeout": "请求超时，请检查网络",
        "timed out": "连接超时，网络可能不稳定",
        "network is unreachable": "网络不可达，请检查网络连接",
        "name or service not known": "域名解析失败，请检查 DNS",
        "max retries exceeded": "重试次数过多，请稍后再试",
        "ssl": "SSL 证书验证失败",
        "proxy": "代理服务器错误",
    }
    
    # 文件系统错误
    filesystem_errors = {
        "no such file": "文件不存在",
        "file not found": "文件未找到",
        "permission denied": "权限不足，请检查文件夹权限",
        "access denied": "访问被拒绝",
        "disk full": "磁盘空间已满",
        "no space left": "磁盘空间不足",
        "read-only": "文件系统为只读",
        "directory not empty": "目录不为空",
    }
    
    # 服务器错误
    server_errors = {
        "internal server error": "服务器内部错误（500）",
        "bad gateway": "网关错误（502）",
        "service unavailable": "服务暂时不可用（503）",
        "gateway timeout": "网关超时（504）",
        "403": "访问被拒绝，可能需要重新登录",
        "404": "资源不存在（404）",
        "401": "未授权，请重新登录",
        "429": "请求过于频繁，请稍后再试",
    }
    
    # 插件相关错误
    plugin_errors = {
        "model not found": "模型文件未找到，请重新安装插件",
        "model is not loaded": "模型未正确加载",
        "cuda out of memory": "显存不足，请降低并发数或使用 CPU",
        "out of memory": "内存不足，请关闭其他程序",
        "plugin not installed": "插件未安装",
        "service not running": "服务未运行",
    }
    
    # 下载相关错误
    download_errors = {
        "failed to download": "下载失败",
        "index.xml": "课程索引文件获取失败，可能课程已下架",
        "invalid response": "服务器返回无效响应",
        "checksum": "文件校验失败，可能已损坏",
    }
    
    # 合并所有错误映射
    all_errors = {
        **network_errors,
        **filesystem_errors,
        **server_errors,
        **plugin_errors,
        **download_errors
    }
    
    # 查找匹配的错误
    for key, chinese in all_errors.items():
        if key in error_text_lower:
            return chinese
    
    # 如果错误信息太长，截断
    if len(error_text) > 100:
        return error_text[:97] + "..."
    
    return error_text
