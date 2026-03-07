"""
统一错误处理器 - 提供智能重试和错误恢复机制
"""
import time
import logging
from typing import Callable, Optional, Any, TypeVar, List, Type, Tuple
from functools import wraps
from .logger import get_logger
from .exceptions import (
    SmartclassException, 
    NetworkException, 
    DownloadException,
    ErrorSeverity,
    ErrorCategory
)

logger = get_logger('error_handler')

T = TypeVar('T')


class RetryConfig:
    """重试配置 - 支持针对不同错误类型的策略"""
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retry_on: Optional[Tuple[Type[Exception], ...]] = None,
        no_retry_on: Optional[Tuple[Type[Exception], ...]] = None
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        # 指定哪些异常应该重试
        self.retry_on = retry_on or (Exception,)
        # 指定哪些异常不应该重试（优先级更高）
        self.no_retry_on = no_retry_on or (
            PermissionError, 
            FileNotFoundError, 
            NotADirectoryError,
            IsADirectoryError,
            ValueError,
            TypeError
        )
    
    def get_delay(self, attempt: int) -> float:
        """计算重试延迟（指数退避）"""
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        
        if self.jitter:
            import random
            delay = delay * (0.5 + random.random())
        
        return delay
    
    def should_retry_exception(self, exception: Exception) -> bool:
        """判断异常是否应该重试"""
        # 优先检查不应重试的异常
        if isinstance(exception, self.no_retry_on):
            return False
        
        # 检查是否在允许重试的异常列表中
        if isinstance(exception, self.retry_on):
            return True
        
        return False


# 预定义的重试配置
class RetryConfigs:
    """常用重试配置"""
    
    # 网络请求重试配置（激进）
    NETWORK = RetryConfig(
        max_attempts=5,
        base_delay=2.0,
        max_delay=30.0,
        exponential_base=2.0,
        retry_on=(
            ConnectionError, 
            TimeoutError, 
            OSError,  # 包含网络相关的 OSError
            NetworkException
        ),
        no_retry_on=(
            PermissionError,
            FileNotFoundError,
            ValueError
        )
    )
    
    # 文件操作重试配置（保守）
    FILE_IO = RetryConfig(
        max_attempts=2,
        base_delay=0.5,
        max_delay=5.0,
        retry_on=(OSError, IOError),
        no_retry_on=(
            PermissionError,
            FileNotFoundError,
            NotADirectoryError,
            IsADirectoryError
        )
    )
    
    # 默认配置（中等）
    DEFAULT = RetryConfig(
        max_attempts=3,
        base_delay=1.0,
        max_delay=60.0
    )


class ErrorHandler:
    """统一错误处理器"""
    
    @staticmethod
    def should_retry(exception: Exception) -> bool:
        """判断异常是否应该重试（兼容旧代码）"""
        if isinstance(exception, SmartclassException):
            return exception.recoverable
        
        # 网络相关异常通常可以重试
        if isinstance(exception, (ConnectionError, TimeoutError, OSError)):
            # OSError 需要进一步判断
            if isinstance(exception, OSError):
                # 网络相关的 errno
                import errno
                network_errors = {
                    errno.ECONNREFUSED,  # 连接被拒绝
                    errno.ECONNRESET,    # 连接重置
                    errno.ETIMEDOUT,     # 连接超时
                    errno.EHOSTUNREACH,  # 主机不可达
                    errno.ENETUNREACH,   # 网络不可达
                }
                if hasattr(exception, 'errno') and exception.errno in network_errors:
                    return True
                # 文件系统相关的 errno 不应重试
                fs_errors = {
                    errno.EACCES,   # 权限拒绝
                    errno.ENOENT,   # 文件不存在
                    errno.ENOTDIR,  # 不是目录
                    errno.EISDIR,   # 是目录
                }
                if hasattr(exception, 'errno') and exception.errno in fs_errors:
                    return False
            return True
        
        # 文件系统错误通常不应重试
        if isinstance(exception, (PermissionError, FileNotFoundError, NotADirectoryError, IsADirectoryError)):
            return False
        
        # IO 错误需要判断
        if isinstance(exception, IOError):
            return True
        
        return False
    
    @staticmethod
    def get_retry_delay(exception: Exception, attempt: int) -> float:
        """获取重试延迟"""
        if isinstance(exception, SmartclassException) and exception.retry_after:
            return exception.retry_after
        
        # 默认指数退避
        return min(2 ** attempt, 60)
    
    @staticmethod
    def _determine_log_level(exception: Exception, severity: ErrorSeverity = None) -> int:
        """根据异常类型和严重程度确定日志级别"""
        # 如果有明确的严重程度，使用它
        if severity:
            if severity == ErrorSeverity.CRITICAL:
                return logging.CRITICAL
            elif severity == ErrorSeverity.ERROR:
                return logging.ERROR
            elif severity == ErrorSeverity.WARNING:
                return logging.WARNING
            else:
                return logging.INFO
        
        # 根据异常类型判断
        if isinstance(exception, (PermissionError, FileNotFoundError)):
            return logging.ERROR
        elif isinstance(exception, (ConnectionError, TimeoutError)):
            return logging.WARNING  # 网络错误通常是临时的
        elif isinstance(exception, (OSError, IOError)):
            # OSError 需要进一步判断
            import errno
            if hasattr(exception, 'errno'):
                if exception.errno in (errno.EACCES, errno.ENOENT):
                    return logging.ERROR
                elif exception.errno in (errno.ECONNREFUSED, errno.ETIMEDOUT):
                    return logging.WARNING
            return logging.ERROR
        elif isinstance(exception, (ValueError, TypeError)):
            return logging.ERROR  # 参数错误通常是严重的
        else:
            return logging.ERROR  # 默认
    
    @staticmethod
    def handle_exception(
        exception: Exception,
        context: str = "",
        log_level: int = None
    ) -> SmartclassException:
        """统一异常处理，转换为 SmartclassException"""
        
        # 如果已经是 SmartclassException，直接返回
        if isinstance(exception, SmartclassException):
            actual_log_level = log_level or ErrorHandler._determine_log_level(exception, exception.severity)
            logger.log(actual_log_level, f"{context}: {exception.message}", exc_info=True)
            return exception
        
        # 转换标准异常
        exc = None
        severity = ErrorSeverity.ERROR
        
        if isinstance(exception, ConnectionError):
            severity = ErrorSeverity.WARNING
            exc = NetworkException(
                str(exception),
                details=f"Context: {context}",
                user_message="网络连接失败，请检查网络",
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, TimeoutError):
            severity = ErrorSeverity.WARNING
            exc = NetworkException(
                str(exception),
                details=f"Context: {context}",
                user_message="请求超时，请检查网络连接",
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, PermissionError):
            severity = ErrorSeverity.ERROR
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message="权限不足，请检查文件夹权限",
                category=ErrorCategory.FILESYSTEM,
                severity=severity,
                recoverable=False,
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, FileNotFoundError):
            severity = ErrorSeverity.ERROR
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message="文件不存在",
                category=ErrorCategory.FILESYSTEM,
                severity=severity,
                recoverable=False,
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, (NotADirectoryError, IsADirectoryError)):
            severity = ErrorSeverity.ERROR
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message="路径类型错误",
                category=ErrorCategory.FILESYSTEM,
                severity=severity,
                recoverable=False,
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, OSError):
            # OSError 需要详细判断
            import errno
            if hasattr(exception, 'errno'):
                if exception.errno in (errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT):
                    severity = ErrorSeverity.WARNING
                    exc = NetworkException(
                        str(exception),
                        details=f"Context: {context}",
                        user_message="网络连接异常",
                        context={"original_type": type(exception).__name__, "errno": exception.errno}
                    )
                elif exception.errno in (errno.EACCES, errno.ENOENT):
                    severity = ErrorSeverity.ERROR
                    exc = SmartclassException(
                        str(exception),
                        details=f"Context: {context}",
                        user_message="文件系统错误",
                        category=ErrorCategory.FILESYSTEM,
                        severity=severity,
                        recoverable=False,
                        context={"original_type": type(exception).__name__, "errno": exception.errno}
                    )
            
            if not exc:
                severity = ErrorSeverity.ERROR
                exc = SmartclassException(
                    str(exception),
                    details=f"Context: {context}",
                    user_message=f"系统错误: {str(exception)[:50]}",
                    category=ErrorCategory.UNKNOWN,
                    severity=severity,
                    context={"original_type": type(exception).__name__}
                )
        elif isinstance(exception, IOError):
            severity = ErrorSeverity.WARNING
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message="IO 操作失败",
                category=ErrorCategory.FILESYSTEM,
                severity=severity,
                recoverable=True,
                context={"original_type": type(exception).__name__}
            )
        elif isinstance(exception, (ValueError, TypeError)):
            severity = ErrorSeverity.ERROR
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message="参数错误",
                category=ErrorCategory.VALIDATION,
                severity=severity,
                recoverable=False,
                context={"original_type": type(exception).__name__}
            )
        else:
            # 未知异常
            severity = ErrorSeverity.ERROR
            exc = SmartclassException(
                str(exception),
                details=f"Context: {context}",
                user_message=f"发生未知错误: {str(exception)[:50]}",
                category=ErrorCategory.UNKNOWN,
                severity=severity,
                context={"original_type": type(exception).__name__}
            )
        
        actual_log_level = log_level or ErrorHandler._determine_log_level(exception, severity)
        logger.log(actual_log_level, f"{context}: {exc.message}", exc_info=True)
        return exc


def with_retry(
    retry_config: Optional[RetryConfig] = None,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
    exceptions: tuple = None
):
    """
    装饰器：为函数添加智能重试机制
    
    Args:
        retry_config: 重试配置（可使用 RetryConfigs 中的预定义配置）
        on_retry: 重试时的回调函数
        exceptions: 需要捕获的异常类型（已废弃，使用 retry_config.retry_on）
    """
    if retry_config is None:
        retry_config = RetryConfigs.DEFAULT
    
    # 兼容旧的 exceptions 参数
    if exceptions is not None:
        retry_config.retry_on = exceptions
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(retry_config.max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # 使用新的重试判断逻辑
                    should_retry = retry_config.should_retry_exception(e)
                    
                    if not should_retry:
                        # 根据异常类型确定日志级别
                        log_level = ErrorHandler._determine_log_level(e)
                        logger.log(
                            log_level,
                            f"{func.__name__}: 不可恢复的错误，停止重试 - {type(e).__name__}: {str(e)[:100]}"
                        )
                        raise
                    
                    # 最后一次尝试，不再重试
                    if attempt >= retry_config.max_attempts - 1:
                        logger.error(
                            f"{func.__name__}: 达到最大重试次数 ({retry_config.max_attempts}) - "
                            f"{type(e).__name__}: {str(e)[:100]}"
                        )
                        raise
                    
                    # 计算延迟
                    delay = retry_config.get_delay(attempt)
                    
                    # 使用 WARNING 级别记录重试
                    logger.warning(
                        f"{func.__name__}: 尝试 {attempt + 1}/{retry_config.max_attempts} 失败，"
                        f"{delay:.1f}秒后重试 - {type(e).__name__}: {str(e)[:100]}"
                    )
                    
                    # 调用重试回调
                    if on_retry:
                        try:
                            on_retry(e, attempt + 1)
                        except Exception as callback_error:
                            logger.error(f"重试回调失败: {callback_error}")
                    
                    # 等待后重试
                    time.sleep(delay)
            
            # 理论上不会到这里，但为了类型安全
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")
        
        return wrapper
    return decorator


def safe_execute(
    func: Callable[..., T],
    *args,
    default: Optional[T] = None,
    context: str = "",
    log_errors: bool = True,
    **kwargs
) -> Optional[T]:
    """
    安全执行函数，捕获所有异常并返回默认值
    
    Args:
        func: 要执行的函数
        default: 发生异常时的默认返回值
        context: 上下文信息
        log_errors: 是否记录错误日志
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_errors:
            exc = ErrorHandler.handle_exception(e, context=context or func.__name__)
            logger.error(f"安全执行失败: {exc.user_message}")
        return default


class ErrorAggregator:
    """错误聚合器 - 收集和分析多个错误"""
    
    def __init__(self):
        self.errors: List[SmartclassException] = []
    
    def add(self, error: Exception, context: str = ""):
        """添加错误"""
        if isinstance(error, SmartclassException):
            self.errors.append(error)
        else:
            self.errors.append(ErrorHandler.handle_exception(error, context))
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def has_critical_errors(self) -> bool:
        """是否有严重错误"""
        return any(e.severity == ErrorSeverity.CRITICAL for e in self.errors)
    
    def get_summary(self) -> str:
        """获取错误摘要"""
        if not self.errors:
            return "无错误"
        
        by_category = {}
        for error in self.errors:
            category = error.category.value
            by_category[category] = by_category.get(category, 0) + 1
        
        summary_parts = [f"{cat}: {count}个" for cat, count in by_category.items()]
        return f"共 {len(self.errors)} 个错误 ({', '.join(summary_parts)})"
    
    def get_user_messages(self) -> List[str]:
        """获取所有用户友好的错误消息"""
        return [e.user_message for e in self.errors]
    
    def clear(self):
        """清空错误"""
        self.errors.clear()

