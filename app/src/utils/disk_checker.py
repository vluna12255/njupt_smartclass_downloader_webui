"""
磁盘空间检查工具 - 下载前预检测磁盘空间
"""
import os
import shutil
from typing import Tuple

from .logger import get_logger

logger = get_logger('disk_checker')


class DiskSpaceChecker:
    """磁盘空间检查器"""
    
    # 预留空间(1GB)
    RESERVED_SPACE = 1024 * 1024 * 1024
    
    @staticmethod
    def check_available_space(path: str) -> int:
        """
        检查指定路径的可用磁盘空间
        
        Args:
            path: 文件路径或目录路径
            
        Returns:
            可用空间(字节)
        """
        try:
            # 获取目录路径
            if os.path.isfile(path):
                directory = os.path.dirname(path)
            else:
                directory = path
            
            # 确保目录存在
            if not os.path.exists(directory):
                directory = os.path.dirname(directory)
            
            # 获取磁盘使用情况
            stat = shutil.disk_usage(directory)
            return stat.free
            
        except Exception as e:
            logger.error(f"检查磁盘空间失败: {e}")
            return 0
    
    @staticmethod
    def format_size(size_bytes: int) -> str:
        """
        格式化文件大小
        
        Args:
            size_bytes: 字节数
            
        Returns:
            格式化后的字符串
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
    
    @staticmethod
    def check_space_sufficient(path: str, required_bytes: int, 
                               reserved_bytes: int = None) -> Tuple[bool, str]:
        """
        检查磁盘空间是否充足
        
        Args:
            path: 文件路径
            required_bytes: 需要的空间(字节)
            reserved_bytes: 预留空间(字节),默认1GB
            
        Returns:
            (是否充足, 提示信息)
        """
        if reserved_bytes is None:
            reserved_bytes = DiskSpaceChecker.RESERVED_SPACE
        
        available = DiskSpaceChecker.check_available_space(path)
        total_required = required_bytes + reserved_bytes
        
        if available < total_required:
            msg = (f"磁盘空间不足!\n"
                  f"需要: {DiskSpaceChecker.format_size(required_bytes)} "
                  f"(+ {DiskSpaceChecker.format_size(reserved_bytes)} 预留)\n"
                  f"可用: {DiskSpaceChecker.format_size(available)}\n"
                  f"缺少: {DiskSpaceChecker.format_size(total_required - available)}")
            logger.warning(msg)
            return False, msg
        
        logger.info(f"磁盘空间检查通过: "
                   f"需要 {DiskSpaceChecker.format_size(required_bytes)}, "
                   f"可用 {DiskSpaceChecker.format_size(available)}")
        return True, "磁盘空间充足"
    
    @staticmethod
    def get_disk_info(path: str) -> dict:
        """
        获取磁盘详细信息
        
        Args:
            path: 路径
            
        Returns:
            磁盘信息字典
        """
        try:
            directory = os.path.dirname(path) if os.path.isfile(path) else path
            stat = shutil.disk_usage(directory)
            
            return {
                'total': stat.total,
                'used': stat.used,
                'free': stat.free,
                'percent': (stat.used / stat.total * 100) if stat.total > 0 else 0,
                'total_formatted': DiskSpaceChecker.format_size(stat.total),
                'used_formatted': DiskSpaceChecker.format_size(stat.used),
                'free_formatted': DiskSpaceChecker.format_size(stat.free)
            }
        except Exception as e:
            logger.error(f"获取磁盘信息失败: {e}")
            return {}


# 便捷函数
def check_disk_space(path: str, required_bytes: int) -> Tuple[bool, str]:
    """
    检查磁盘空间的便捷函数
    
    Args:
        path: 文件路径
        required_bytes: 需要的空间(字节)
        
    Returns:
        (是否充足, 提示信息)
    """
    return DiskSpaceChecker.check_space_sufficient(path, required_bytes)


def get_available_space(path: str) -> int:
    """
    获取可用空间的便捷函数
    
    Args:
        path: 路径
        
    Returns:
        可用空间(字节)
    """
    return DiskSpaceChecker.check_available_space(path)

