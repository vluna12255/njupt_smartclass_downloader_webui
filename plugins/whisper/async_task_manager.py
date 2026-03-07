"""
异步任务管理器 - 用于处理长时间运行的任务
支持任务提交、状态查询、结果获取
"""
import os
import json
import uuid
import threading
import time
from enum import Enum
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消


class AsyncTask:
    """异步任务对象"""
    def __init__(self, task_id: str, task_type: str):
        self.task_id = task_id
        self.task_type = task_type
        self.status = TaskStatus.PENDING
        self.progress = 0.0
        self.message = ""
        self.error = None
        self.result = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.metadata = {}
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AsyncTask':
        """从字典创建"""
        task = cls(data["task_id"], data["task_type"])
        task.status = TaskStatus(data["status"])
        task.progress = data.get("progress", 0.0)
        task.message = data.get("message", "")
        task.error = data.get("error")
        task.result = data.get("result")
        task.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("started_at"):
            task.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            task.completed_at = datetime.fromisoformat(data["completed_at"])
        task.metadata = data.get("metadata", {})
        return task


class AsyncTaskManager:
    """异步任务管理器"""
    
    def __init__(self, storage_dir: str = None, max_workers: int = 2):
        """
        初始化任务管理器
        
        Args:
            storage_dir: 任务持久化存储目录
            max_workers: 最大并发工作线程数
        """
        self.storage_dir = storage_dir or os.path.join(os.path.dirname(__file__), "tasks")
        os.makedirs(self.storage_dir, exist_ok=True)
        
        self.tasks: Dict[str, AsyncTask] = {}
        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(max_workers)
        
        # 加载已有任务
        self._load_tasks()
        
        # 启动清理线程
        self.cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self.cleanup_thread.start()
    
    def _get_task_file(self, task_id: str) -> str:
        """获取任务文件路径"""
        return os.path.join(self.storage_dir, f"{task_id}.json")
    
    def _save_task(self, task: AsyncTask):
        """持久化任务"""
        try:
            with open(self._get_task_file(task.task_id), 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存任务失败: {e}")
    
    def _load_tasks(self):
        """加载所有任务"""
        try:
            for filename in os.listdir(self.storage_dir):
                if filename.endswith('.json'):
                    try:
                        with open(os.path.join(self.storage_dir, filename), 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            task = AsyncTask.from_dict(data)
                            self.tasks[task.task_id] = task
                    except Exception as e:
                        print(f"加载任务 {filename} 失败: {e}")
        except Exception as e:
            print(f"加载任务目录失败: {e}")
    
    def _cleanup_worker(self):
        """定期清理过期任务和检测超时任务（基于心跳检测）"""
        while True:
            try:
                time.sleep(300)  # 每5分钟检查一次
                
                with self.lock:
                    now = datetime.now()
                    expired_tasks = []
                    
                    for task_id, task in self.tasks.items():
                        # 检测运行中的任务是否长时间无更新（15分钟无心跳判定为卡死）
                        if task.status == TaskStatus.RUNNING:
                            # 检查任务的最后更新时间（通过 metadata 存储）
                            last_update = task.metadata.get('last_heartbeat')
                            if last_update:
                                try:
                                    last_update_time = datetime.fromisoformat(last_update)
                                    idle_minutes = (now - last_update_time).total_seconds() / 60
                                    
                                    # 15分钟无心跳判定为卡死
                                    if idle_minutes > 15:
                                        task.status = TaskStatus.FAILED
                                        task.error = f"任务无响应超过 {int(idle_minutes)} 分钟，判定为卡死"
                                        task.completed_at = now
                                        task.message = "任务卡死"
                                        self._save_task(task)
                                        print(f"任务无响应被强制终止: {task_id} (空闲 {int(idle_minutes)} 分钟)")
                                        # 清理任务相关的临时文件
                                        self._cleanup_task_files(task)
                                except:
                                    pass
                        
                        # 检测等待中的任务是否超时（超过10分钟未开始）
                        elif task.status == TaskStatus.PENDING:
                            if (now - task.created_at) > timedelta(minutes=10):
                                task.status = TaskStatus.FAILED
                                task.error = "任务等待超时（10分钟），可能服务繁忙"
                                task.completed_at = now
                                task.message = "等待超时"
                                self._save_task(task)
                                print(f"等待任务超时被取消: {task_id}")
                                # 清理任务相关的临时文件
                                self._cleanup_task_files(task)
                        
                        # 清理完成任务的临时文件（1小时后）
                        elif task.status == TaskStatus.COMPLETED:
                            if task.completed_at and (now - task.completed_at) > timedelta(hours=1):
                                self._cleanup_task_files(task)
                        
                        # 清理24小时前完成的任务
                        if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                            if task.completed_at and (now - task.completed_at) > timedelta(hours=24):
                                expired_tasks.append(task_id)
                    
                    for task_id in expired_tasks:
                        task = self.tasks.get(task_id)
                        if task:
                            self._cleanup_task_files(task)
                        self._delete_task(task_id)
                        print(f"清理过期任务: {task_id}")
            
            except Exception as e:
                print(f"清理任务异常: {e}")
    
    def _cleanup_task_files(self, task: AsyncTask):
        """清理任务相关的临时文件"""
        try:
            # 清理结果文件
            if task.result and isinstance(task.result, dict):
                srt_path = task.result.get("srt_path")
                if srt_path and os.path.exists(srt_path):
                    try:
                        os.remove(srt_path)
                        print(f"[清理] 已删除任务 {task.task_id} 的字幕文件: {os.path.basename(srt_path)}")
                    except Exception as e:
                        print(f"[清理失败] 无法删除字幕文件 {srt_path}: {e}")
            
            # 清理音频文件（如果还存在）
            if task.metadata and isinstance(task.metadata, dict):
                audio_path = task.metadata.get("audio_path")
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                        print(f"[清理] 已删除任务 {task.task_id} 的音频文件: {os.path.basename(audio_path)}")
                    except Exception as e:
                        print(f"[清理失败] 无法删除音频文件 {audio_path}: {e}")
        except Exception as e:
            print(f"清理任务文件失败: {e}")
    
    def _delete_task(self, task_id: str):
        """删除任务（需要持有锁）"""
        if task_id in self.tasks:
            del self.tasks[task_id]
        
        task_file = self._get_task_file(task_id)
        if os.path.exists(task_file):
            try:
                os.remove(task_file)
            except Exception as e:
                print(f"删除任务文件失败: {e}")
    
    def create_task(self, task_type: str, metadata: dict = None) -> str:
        """
        创建新任务
        
        Args:
            task_type: 任务类型
            metadata: 任务元数据
        
        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())
        task = AsyncTask(task_id, task_type)
        
        if metadata:
            task.metadata = metadata
        
        with self.lock:
            self.tasks[task_id] = task
            self._save_task(task)
        
        return task_id
    
    def submit_task(self, task_id: str, worker_func: Callable, *args, **kwargs):
        """
        提交任务执行
        
        Args:
            task_id: 任务ID
            worker_func: 工作函数
            *args, **kwargs: 传递给工作函数的参数
        """
        def _worker():
            with self.semaphore:
                task = self.get_task(task_id)
                if not task:
                    return
                
                # 更新状态为运行中
                self.update_task(task_id, 
                    status=TaskStatus.RUNNING,
                    started_at=datetime.now(),
                    message="任务执行中..."
                )
                
                try:
                    # 执行工作函数
                    result = worker_func(task_id, *args, **kwargs)
                    
                    # 更新为完成
                    self.update_task(task_id,
                        status=TaskStatus.COMPLETED,
                        completed_at=datetime.now(),
                        progress=100.0,
                        result=result,
                        message="任务完成"
                    )
                
                except Exception as e:
                    # 更新为失败
                    self.update_task(task_id,
                        status=TaskStatus.FAILED,
                        completed_at=datetime.now(),
                        error=str(e),
                        message=f"任务失败: {str(e)}"
                    )
        
        # 启动工作线程
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
    
    def get_task(self, task_id: str) -> Optional[AsyncTask]:
        """获取任务"""
        with self.lock:
            return self.tasks.get(task_id)
    
    def update_task(self, task_id: str, **kwargs):
        """
        更新任务状态
        
        支持的参数: status, progress, message, error, result, metadata
        """
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            
            # 更新字段
            if 'status' in kwargs:
                task.status = kwargs['status']
            if 'progress' in kwargs:
                task.progress = kwargs['progress']
            if 'message' in kwargs:
                task.message = kwargs['message']
            if 'error' in kwargs:
                task.error = kwargs['error']
            if 'result' in kwargs:
                task.result = kwargs['result']
            if 'started_at' in kwargs:
                task.started_at = kwargs['started_at']
            if 'completed_at' in kwargs:
                task.completed_at = kwargs['completed_at']
            if 'metadata' in kwargs:
                task.metadata.update(kwargs['metadata'])
            
            # 持久化
            self._save_task(task)
    
    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        注意: 只能取消等待中的任务，运行中的任务无法强制停止
        """
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                task.message = "任务已取消"
                self._save_task(task)
                return True
            
            return False
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态（返回字典）"""
        task = self.get_task(task_id)
        if task:
            return task.to_dict()
        return None
    
    def list_tasks(self, status: TaskStatus = None) -> list:
        """列出所有任务"""
        with self.lock:
            tasks = list(self.tasks.values())
            
            if status:
                tasks = [t for t in tasks if t.status == status]
            
            return [t.to_dict() for t in tasks]


# 全局单例
_task_manager = None


def get_task_manager(storage_dir: str = None, max_workers: int = 2) -> AsyncTaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = AsyncTaskManager(storage_dir, max_workers)
    return _task_manager

