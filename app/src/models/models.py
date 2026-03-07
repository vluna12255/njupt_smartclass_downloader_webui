"""数据模型 - 统一的数据类定义"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
from datetime import datetime


# ==================== 任务相关模型 ====================

class TaskStatus(str, Enum):
    """任务状态枚举"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """
    任务信息数据类
    
    Attributes:
        id: 任务唯一标识符
        title: 任务标题
        status: 任务状态
        progress: 进度百分比 (0-100)
        total_size: 总大小（字节）
        downloaded_size: 已下载大小（字节）
        speed: 下载速度（字节/秒）
        current_action: 当前操作描述
        message: 状态消息
        error: 错误信息
        created_at: 创建时间
        updated_at: 更新时间
    """
    id: str
    title: str
    status: TaskStatus
    
    progress: float = 0.0
    total_size: int = 0
    downloaded_size: int = 0
    speed: float = 0.0
    current_action: str = ""
    
    message: str = "等待中..."
    error: str = ""
    
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    _last_speed_update_time: float = field(default=0.0, repr=False)
    
    def update(self, **kwargs) -> None:
        """更新任务信息"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = datetime.now()

    @property
    def status_text(self) -> str:
        """获取状态的中文描述"""
        status_map = {
            TaskStatus.QUEUED: "排队中",
            TaskStatus.RUNNING: "进行中",
            TaskStatus.WAITING: "等待资源",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失败",
            TaskStatus.CANCELLED: "已取消"
        }
        return status_map.get(self.status, "未知")

    @property
    def downloaded_str(self) -> str:
        """格式化已下载大小"""
        return self._format_size(self.downloaded_size)

    @property
    def total_size_str(self) -> str:
        """格式化总大小"""
        return self._format_size(self.total_size)

    @property
    def speed_str(self) -> str:
        """格式化下载速度"""
        if self.speed < 0.1:
            return "0 KB/s"
        if self.speed < 1024 * 1024:
            return f"{self.speed / 1024:.0f} KB/s"
        return f"{self.speed / 1024 / 1024:.2f} MB/s"

    @property
    def eta_str(self) -> str:
        """格式化预计剩余时间"""
        if self.total_size <= 0 or self.speed < 1024:
            return "--"
        
        remaining = self.total_size - self.downloaded_size
        if remaining <= 0:
            return "0s"
        
        seconds = int(remaining / self.speed)
        return self._format_duration(seconds)
    
    @property
    def duration_str(self) -> str:
        """格式化任务持续时间"""
        duration = (self.updated_at - self.created_at).total_seconds()
        return self._format_duration(int(duration))
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        else:
            return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        """格式化时间长度"""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            'id': self.id,
            'title': self.title,
            'status': self.status.value,
            'status_text': self.status_text,
            'progress': self.progress,
            'total_size': self.total_size,
            'downloaded_size': self.downloaded_size,
            'speed': self.speed,
            'speed_str': self.speed_str,
            'current_action': self.current_action,
            'message': self.message,
            'error': self.error,
            'eta_str': self.eta_str,
            'duration_str': self.duration_str,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


# ==================== 视频相关模型 ====================

@dataclass
class VideoSearchCondition:
    """视频搜索条件"""
    title_key: str = ""
    page_size: int = 12
    page_number: int = 1
    sort: str = "StartTime"
    order: int = 0
    start_date: str = ""
    end_date: str = ""


@dataclass
class VideoSummary:
    """视频摘要信息"""
    id: str
    title: str
    start_time: datetime
    stop_time: datetime
    course_name: str
    teachers: str
    classroom_name: str
    cover_url: str


@dataclass
class VideoSegmentInfo:
    """视频分段信息"""
    index_file_uri: str


@dataclass
class VideoInfo:
    """视频详细信息"""
    id: str
    title: str
    start_time: datetime
    stop_time: datetime
    course_name: str
    segments: List[VideoSegmentInfo]


@dataclass
class VideoSearchResult:
    """视频搜索结果"""
    total_count: int
    videos: List[VideoSummary]


