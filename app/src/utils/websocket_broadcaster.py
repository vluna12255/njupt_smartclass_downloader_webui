"""
WebSocket 广播器 - 实时推送任务状态更新
"""
import asyncio
import json
from typing import Set, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from .logger import get_logger

logger = get_logger('websocket')


class WebSocketBroadcaster:
    """WebSocket 广播器 - 管理所有 WebSocket 连接"""
    
    def __init__(self):
        self.connections: Set[WebSocket] = set()
        self.lock = asyncio.Lock()
        logger.info("WebSocket 广播器初始化")
    
    async def connect(self, websocket: WebSocket):
        """接受新的 WebSocket 连接"""
        await websocket.accept()
        async with self.lock:
            self.connections.add(websocket)
        logger.info(f"WebSocket 连接建立 (总连接数: {len(self.connections)})")
    
    async def disconnect(self, websocket: WebSocket):
        """断开 WebSocket 连接"""
        async with self.lock:
            self.connections.discard(websocket)
        logger.info(f"WebSocket 连接断开 (总连接数: {len(self.connections)})")
    
    async def broadcast(self, message: Dict[str, Any]):
        """
        广播消息到所有连接的客户端
        
        Args:
            message: 要广播的消息字典
        """
        if not self.connections:
            return
        
        # 转换为 JSON
        try:
            json_message = json.dumps(message, ensure_ascii=False)
        except Exception as e:
            logger.error(f"消息序列化失败 {e}")
            return
        
        # 广播到所有连接
        disconnected = set()
        async with self.lock:
            for connection in self.connections:
                try:
                    await connection.send_text(json_message)
                except WebSocketDisconnect:
                    disconnected.add(connection)
                    logger.debug("检测到断开的连接")
                except Exception as e:
                    logger.error(f"发送消息失败 {e}")
                    disconnected.add(connection)
            
            # 清理断开的连接
            for conn in disconnected:
                self.connections.discard(conn)
        
        if disconnected:
            logger.debug(f"清理了 {len(disconnected)} 个断开的连接")
    
    async def broadcast_task_update(self, task_data: Dict[str, Any]):
        """
        广播任务更新
        
        Args:
            task_data: 任务数据
        """
        message = {
            'type': 'task_update',
            'data': task_data
        }
        await self.broadcast(message)
    
    async def broadcast_task_list(self, tasks: list):
        """
        广播任务列表
        
        Args:
            tasks: 任务列表
        """
        message = {
            'type': 'task_list',
            'data': tasks
        }
        await self.broadcast(message)
    
    async def broadcast_notification(self, title: str, message: str, level: str = 'info'):
        """
        广播通知消息
        
        Args:
            title: 通知标题
            message: 通知内容
            level: 通知级别 (info/warning/error/success)
        """
        notification = {
            'type': 'notification',
            'data': {
                'title': title,
                'message': message,
                'level': level
            }
        }
        await self.broadcast(notification)
    
    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self.connections)
    
    async def close_all(self):
        """关闭所有连接"""
        async with self.lock:
            for connection in list(self.connections):
                try:
                    await connection.close()
                except Exception as e:
                    logger.error(f"关闭连接失败 {e}")
            self.connections.clear()
        logger.info("所有 WebSocket 连接已关闭")


# 全局广播器实例
_global_broadcaster: WebSocketBroadcaster = None


def get_broadcaster() -> WebSocketBroadcaster:
    """获取全局广播器实例"""
    global _global_broadcaster
    if _global_broadcaster is None:
        _global_broadcaster = WebSocketBroadcaster()
    return _global_broadcaster


# 同步包装器 - 用于在同步代码中调用
def broadcast_sync(message: Dict[str, Any]):
    """
    同步方式广播消息(在新的事件循环中执行)
    
    Args:
        message: 要广播的消息
    """
    broadcaster = get_broadcaster()
    
    # 如果没有连接，直接返回
    if not broadcaster.connections:
        return
    
    try:
        # 尝试获取当前事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果在运行中的循环内，使用 asyncio.create_task 并忽略警告
            # 因为我们不需要等待广播完成
            asyncio.ensure_future(broadcaster.broadcast(message), loop=loop)
        except RuntimeError:
            # 没有运行中的循环，创建新的临时循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(broadcaster.broadcast(message))
            finally:
                loop.close()
                asyncio.set_event_loop(None)
    except Exception as e:
        # 静默失败，不影响主流程
        logger.debug(f"广播消息失败 {e}")


def broadcast_task_update_sync(task_data: Dict[str, Any]):
    """同步方式广播任务更新"""
    message = {
        'type': 'task_update',
        'data': task_data
    }
    broadcast_sync(message)
