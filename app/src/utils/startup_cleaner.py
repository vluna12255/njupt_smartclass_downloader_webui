"""
启动清理工具 - 在应用启动前清理临时文件和日志
"""
import os
import glob
from pathlib import Path
from .logger import get_logger
from .config_manager import config_manager

logger = get_logger('startup_cleaner')


class StartupCleaner:
    """启动时清理临时文件和日志"""
    
    def __init__(self, root_dir):
        """
        初始化清理器
        
        Args:
            root_dir: 项目根目录
        """
        self.root_dir = root_dir
        self.logs_dir = os.path.join(root_dir, "logs")
        configured_download_dir = (config_manager.get().download_dir or "").strip()
        self.download_dir = configured_download_dir or os.path.join(root_dir, "SmartclassDownload")
    
    def clean_logs(self):
        """清理 logs/ 目录下的所有文件"""
        if not os.path.exists(self.logs_dir):
            return 0
        
        cleaned_count = 0
        truncated_count = 0
        try:
            import logging
            
            # 收集所有正在使用的日志文件
            active_log_files = set()
            for name in list(logging.Logger.manager.loggerDict.keys()):
                log = logging.getLogger(name)
                for handler in log.handlers:
                    if isinstance(handler, logging.FileHandler):
                        active_log_files.add(os.path.abspath(handler.baseFilename))
            
            # 遍历 logs 目录下的所有文件
            for item in os.listdir(self.logs_dir):
                item_path = os.path.join(self.logs_dir, item)
                
                # 只处理文件，不处理子目录
                if os.path.isfile(item_path):
                    abs_path = os.path.abspath(item_path)
                    
                    # 如果文件正在被日志系统使用，清空内容而不是删除
                    if abs_path in active_log_files:
                        try:
                            # 清空文件内容（truncate）
                            with open(item_path, 'w', encoding='utf-8') as f:
                                pass
                            truncated_count += 1
                        except Exception as e:
                            logger.warning(f"无法清空日志文件 {item} {e}")
                    else:
                        # 文件未被使用，直接删除
                        try:
                            os.remove(item_path)
                            cleaned_count += 1
                        except PermissionError:
                            # 文件被其他进程占用，跳过
                            pass
                        except Exception as e:
                            logger.warning(f"无法删除日志文件 {item} {e}")
            
            total = cleaned_count + truncated_count
            if total > 0:
                details = []
                if cleaned_count > 0:
                    details.append(f"删除 {cleaned_count} 个")
                if truncated_count > 0:
                    details.append(f"清空 {truncated_count} 个")
                logger.debug(f"日志清理 {', '.join(details)}")
            
            return total
            
        except Exception as e:
            logger.error(f"清理日志目录失败 {e}", exc_info=True)
            return cleaned_count + truncated_count
    
    def clean_tmp_files(self):
        """清理下载目录中所有包含 'tmp' 的文件和 .wav 文件"""
        if not os.path.exists(self.download_dir):
            return 0
        
        cleaned_count = 0
        try:
            # 使用 glob 递归查找所有包含 tmp 的文件和 .wav 文件
            patterns = [
                os.path.join(self.download_dir, "**", "*tmp*"),  # 任何包含 tmp 的文件
                os.path.join(self.download_dir, "*tmp*"),         # 根目录下的 tmp 文件
                os.path.join(self.download_dir, "**", "*.wav"),  # 所有 .wav 文件
            ]
            
            tmp_files = set()  # 使用集合避免重复
            for pattern in patterns:
                tmp_files.update(glob.glob(pattern, recursive=True))
            
            # 删除找到的临时文件
            for file_path in tmp_files:
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        cleaned_count += 1
                    except PermissionError:
                        # 文件被占用，跳过
                        pass
                    except Exception as e:
                        logger.warning(f"无法删除临时文件 {file_path} {e}")
            
            return cleaned_count
            
        except Exception as e:
            logger.error(f"清理临时文件失败 {e}", exc_info=True)
            return cleaned_count
    
    def clean_all(self):
        """执行所有清理任务"""
        # 清理日志
        logs_count = self.clean_logs()
        
        # 清理临时文件
        tmp_count = self.clean_tmp_files()
        
        total_count = logs_count + tmp_count
        
        if total_count > 0:
            logger.info(f"启动清理完成 共清理 {total_count} 个文件")
            if logs_count > 0:
                logger.info(f"日志目录 {self.logs_dir} ({logs_count} 个文件)")
            if tmp_count > 0:
                logger.info(f"临时文件 {self.download_dir} ({tmp_count} 个文件)")
        
        return total_count


def run_startup_cleanup(root_dir):
    """
    执行启动清理的便捷函数
    
    Args:
        root_dir: 项目根目录
        
    Returns:
        清理的文件总数
    """
    cleaner = StartupCleaner(root_dir)
    return cleaner.clean_all()


if __name__ == "__main__":
    # 测试代码
    import sys
    
    # 获取项目根目录
    current_file = os.path.abspath(__file__)
    # app/src/utils/startup_cleaner.py -> app/src/utils -> app/src -> app -> 项目根目录
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    root_dir = os.path.dirname(app_dir)
    
    print(f"项目根目录: {root_dir}")
    run_startup_cleanup(root_dir)

