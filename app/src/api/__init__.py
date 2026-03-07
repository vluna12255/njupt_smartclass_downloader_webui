"""API路由模块"""
from .auth import setup_auth_routes
from .videos import setup_video_routes
from .tasks import setup_task_routes
from .plugins import setup_plugin_routes
from .config import setup_config_routes

__all__ = [
    'setup_auth_routes',
    'setup_video_routes',
    'setup_task_routes',
    'setup_plugin_routes',
    'setup_config_routes'
]

