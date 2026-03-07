"""主服务器入口"""
import sys
import os
import multiprocessing
import webbrowser
import socket
from contextlib import asynccontextmanager

def _get_app_root():
    """获取应用根目录"""
    is_frozen = getattr(sys, 'frozen', False)
    exe_name = os.path.basename(sys.executable).lower()
    is_nuitka = not exe_name.startswith('python')
    
    if is_frozen or is_nuitka:
        # 编译后：exe 所在目录就是根目录
        return os.path.abspath(os.path.dirname(sys.executable))
    else:
        # 开发环境：server.py 在 app/ 目录下，项目根目录是其父目录
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(base)

def _is_compiled():
    """判断是否为编译后的环境"""
    is_frozen = getattr(sys, 'frozen', False)
    exe_name = os.path.basename(sys.executable).lower()
    is_nuitka = not exe_name.startswith('python')
    return is_frozen or is_nuitka

def _get_compilation_type():
    if getattr(sys, 'frozen', False):
        return 'pyinstaller'
    exe_name = os.path.basename(sys.executable).lower()
    if not exe_name.startswith('python'):
        return 'nuitka'
    return 'development'

# 获取应用根目录
project_root = _get_app_root()
base_dir = project_root
current_dir = base_dir
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# 导入重构后的模块
from src.core.session_manager import SessionManager
from src.services.task_manager import TaskManager
from src.plugins.plugin_manager import plugin_manager
from src.utils.config_manager import config_manager
from src.utils.logger import get_logger
from src.utils.temp_file_manager import temp_manager
from src.utils.websocket_broadcaster import get_broadcaster

from src.utils.startup_cleaner import run_startup_cleanup

from src.api import (
    setup_auth_routes,
    setup_video_routes,
    setup_task_routes,
    setup_plugin_routes,
    setup_config_routes
)

logger = get_logger('server')

SERVER_PORT = 8080

import ssl
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

session_manager = SessionManager()
task_manager = TaskManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("主程序启动")
    
    try:
        logger.info("执行启动清理...")
        run_startup_cleanup(project_root)
        logger.info("启动清理完成")
    except Exception as e:
        logger.error(f"启动清理失败: {e}", exc_info=True)
    
    try:
        logger.info("执行系统自检...")
        current_cfg = config_manager.get()
        target_engine = current_cfg.asr_engine
        
        status = plugin_manager.get_plugin_status(target_engine, check_running=False)
        
        if not status["installed"]:
            fallback_engine = "whisper" if target_engine == "funasr" else "funasr"
            fallback_status = plugin_manager.get_plugin_status(fallback_engine, check_running=False)
            
            if fallback_status["installed"]:
                logger.info(f"自动切换至已安装的语音识别模型: {fallback_engine}")
                config_manager.save({"asr_engine": fallback_engine})
            else:
                logger.info("未检测到已安装的语音识别插件")
                config_manager.save({"asr_engine": "whisper"})
        
        logger.info("系统自检完成")
                
    except Exception as e:
        logger.error(f"启动自检失败: {e}", exc_info=True)
    
    task_manager.set_plugin_manager(plugin_manager)
    plugin_manager.set_uninstall_callback(task_manager.abort_plugin_task)
    
    if current_cfg.auto_login:
        session_manager.perform_auto_login()
    
    try:
        url = f"http://127.0.0.1:{SERVER_PORT}"
        logger.info(f"正在打开默认浏览器: {url}")
        webbrowser.open(url)
        logger.info("浏览器已打开")
    except Exception as e:
        logger.warning(f"打开浏览器失败: {e}")
    
    logger.info("系统就绪")
    
    yield
    
    logger.info("主程序关闭，正在清理资源...")
    
    try:
        broadcaster = get_broadcaster()
        await broadcaster.close_all()
        logger.info("WebSocket 连接已关闭")
    except Exception as e:
        logger.error(f"关闭 WebSocket 失败: {e}", exc_info=True)
    
    try:
        plugin_manager.stop_all_services()
        logger.info("插件服务已停止")
    except Exception as e:
        logger.error(f"停止插件服务失败: {e}", exc_info=True)
    
    try:
        if hasattr(task_manager, 'executor'):
            task_manager.executor.shutdown(wait=True, timeout=10)
            logger.info("任务管理器已关闭")
    except Exception as e:
        logger.error(f"关闭任务管理器失败: {e}", exc_info=True)
    

    
    try:
        temp_manager.cleanup_all()
        logger.info("临时文件已清理")
    except Exception as e:
        logger.error(f"清理临时文件失败: {e}", exc_info=True)
    
    logger.info("资源清理完成")

app = FastAPI(lifespan=lifespan)

template_dir = os.path.join(base_dir, "templates")
templates = None

if os.path.exists(template_dir):
    try:
        templates = Jinja2Templates(directory=template_dir)
        logger.info(f"成功加载模板目录: {template_dir}")
    except Exception as e:
        logger.error(f"模板引擎初始化出错: {e}", exc_info=True)
else:
    logger.error(f"【严重警告】找不到模板目录: {template_dir}")


# 注册路由
auth_router = setup_auth_routes(templates, session_manager)
video_router = setup_video_routes(templates, session_manager, task_manager)
task_router = setup_task_routes(templates, task_manager)
plugin_router = setup_plugin_routes(plugin_manager, task_manager)
config_router = setup_config_routes(plugin_manager, session_manager)

app.include_router(auth_router)
app.include_router(video_router)
app.include_router(task_router)
app.include_router(plugin_router)
app.include_router(config_router)


# 主页路由
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if templates is None:
        return HTMLResponse(
            f"<h1>Error: Templates Not Found</h1><p>Path: {template_dir}</p>",
            status_code=500
        )
    
    client = session_manager.get_client()
    error_msg = None
    
    if not client:
        cfg = config_manager.get()
        if hasattr(cfg, "auto_login") and cfg.auto_login:
            success, msg = session_manager.perform_auto_login()
            if not success and msg != "无保存的凭证":
                error_msg = msg
    
    client = session_manager.get_client()
    
    if not client:
        return templates.TemplateResponse("login.html", {
            "request": request, 
            "error": error_msg
        })
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "videos": None, 
        "tasks": task_manager.get_all_tasks(),
        "user": "已登录"
    })


@app.get("/api/status")
async def check_status():
    """检查系统状态"""
    is_logged_in = True
    if not session_manager.get_client():
        is_logged_in = False
    elif not session_manager.is_session_valid():
        cfg = config_manager.get()
        if hasattr(cfg, "auto_login") and cfg.auto_login:
            success, _ = session_manager.perform_auto_login()
            is_logged_in = success
        else:
            is_logged_in = False
    
    # 获取 WebSocket 连接数
    broadcaster = get_broadcaster()
    ws_connections = broadcaster.get_connection_count()
    
    return {
        "status": "online", 
        "logged_in": is_logged_in,
        "websocket_connections": ws_connections
    }


@app.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket):
    """WebSocket 端点 - 实时推送任务状态"""
    broadcaster = get_broadcaster()
    await broadcaster.connect(websocket)
    
    try:
        # 发送初始任务列表
        tasks = task_manager.get_all_tasks()
        tasks_data = [
            {
                'id': t.id,
                'title': t.title,
                'status': t.status.value,
                'progress': t.progress,
                'message': t.message,
                'speed': t.speed,
                'current_action': t.current_action
            }
            for t in tasks
        ]
        await websocket.send_json({
            'type': 'task_list',
            'data': tasks_data
        })
        
        # 保持连接
        while True:
            # 接收心跳消息
            data = await websocket.receive_text()
            if data == 'ping':
                await websocket.send_text('pong')
                
    except WebSocketDisconnect:
        logger.debug("WebSocket 客户端断开连接")
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}", exc_info=True)
    finally:
        await broadcaster.disconnect(websocket)


def find_available_port(start_port):
    """查找可用端口"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', start_port))
        return start_port
    except OSError:
        print(f"警告: 端口 {start_port} 已被占用，正在寻找随机空闲端口...")
        sock.bind(('127.0.0.1', 0))
        new_port = sock.getsockname()[1]
        return new_port
    finally:
        sock.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    # 配置标准输出使用 UTF-8 编码（仅在 Windows 且未配置时）
    import io
    if sys.platform == 'win32' and not isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        except (AttributeError, ValueError):
            pass
    

    
    SERVER_PORT = find_available_port(8080)
    
    # 打印启动信息
    logger.info(f"服务端口: {SERVER_PORT}")
    logger.info(f"访问地址: http://127.0.0.1:{SERVER_PORT}")
    logger.info("服务正在启动...")
    
    # 配置 uvicorn 日志 - 统一使用 [HH:MM:SS] 格式
    import logging
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["use_colors"] = False
    log_config["formatters"]["default"]["fmt"] = "[%(asctime)s] %(levelname)s    %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%H:%M:%S"
    log_config["formatters"]["access"]["use_colors"] = False
    log_config["formatters"]["access"]["fmt"] = "[%(asctime)s] %(levelname)s    %(message)s"
    log_config["formatters"]["access"]["datefmt"] = "%H:%M:%S"
    
    # 配置 websockets 库的日志格式
    websockets_logger = logging.getLogger('websockets')
    websockets_logger.setLevel(logging.INFO)
    # 移除默认处理器
    websockets_logger.handlers.clear()
    # 添加统一格式的处理器
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s    %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    websockets_logger.addHandler(handler)
    
    uvicorn.run(
        app, 
        host="127.0.0.1", 
        port=SERVER_PORT, 
        reload=False, 
        access_log=False,
        log_config=log_config
    )
