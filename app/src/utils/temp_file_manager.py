"""
临时文件管理器 - 确保临时文件被正确清理
使用方法：
    from temp_file_manager import temp_manager, temp_file
    
    # 方式1: 上下文管理器
    with temp_file("output.tmp") as tmp:
        # 使用临时文件
        pass
    # 自动清理
    
    # 方式2: 手动管理
    temp_manager.register("file.tmp")
    # ... 使用 ...
    temp_manager.cleanup("file.tmp")
"""
import os
import glob
import atexit
from contextlib import contextmanager
from .logger import get_logger

logger = get_logger('temp_file_manager')

class TempFileManager:
    def __init__(self):
        self._temp_files = []
        atexit.register(self.cleanup_all)
    
    def register(self, filepath):
        if filepath and filepath not in self._temp_files:
            self._temp_files.append(filepath)
    
    def unregister(self, filepath):
        if filepath in self._temp_files:
            self._temp_files.remove(filepath)
    
    def cleanup(self, filepath):
        if not filepath or not os.path.exists(filepath):
            return True
        try:
            os.remove(filepath)
            self.unregister(filepath)
            return True
        except Exception as e:
            print(f"清理临时文件失败 {filepath}: {e}")
            return False
    
    def cleanup_all(self):
        for filepath in self._temp_files[:]:
            self.cleanup(filepath)
        self._temp_files.clear()
    
    def cleanup_pattern(self, directory, pattern):
        """按模式清理，如 cleanup_pattern(dir, "*.tmp")"""
        if not os.path.exists(directory):
            return 0
        files = glob.glob(os.path.join(directory, pattern))
        count = 0
        for filepath in files:
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                    count += 1
                except:
                    pass
        return count

@contextmanager
def temp_file(filepath, manager=None):
    if manager:
        manager.register(filepath)
    try:
        yield filepath
    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass
        if manager:
            manager.unregister(filepath)

temp_manager = TempFileManager()
