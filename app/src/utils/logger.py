"""统一日志系统"""
import logging
import os
import sys
import io
from logging.handlers import RotatingFileHandler

if sys.platform == 'win32' and not isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError):
        pass

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

def get_compilation_type():
    if getattr(sys, 'frozen', False):
        return 'pyinstaller'
    exe_name = os.path.basename(sys.executable).lower()
    if not exe_name.startswith('python'):
        return 'nuitka'
    return 'development'

# 统一在根目录创建 logs 文件夹
APP_ROOT = get_app_root()
LOG_DIR = os.path.join(APP_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FORMAT = '[%(asctime)s] %(levelname)-7s %(message)s'
DATE_FORMAT = '%H:%M:%S'

logging.root.handlers = []
logging.root.setLevel(logging.WARNING)


class LazyRotatingFileHandler(RotatingFileHandler):
    """延迟创建日志文件"""
    
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=True):
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay=True)


def setup_logger(name, level=logging.INFO):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    logger.propagate = False
    
    fh = LazyRotatingFileHandler(
        os.path.join(LOG_DIR, f"{name}.log"),
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8',
        delay=True
    )
    fh.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    fh.setFormatter(file_formatter)
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    console_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    ch.setFormatter(console_formatter)
    
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

def get_logger(name='smartclass'):
    """获取日志器"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        setup_logger(name)
    return logger
