"""插件管理相关路由"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import time

from ..utils.logger import get_logger

logger = get_logger('plugins_api')

router = APIRouter()


def setup_plugin_routes(plugin_manager, task_manager):
    """设置插件相关路由"""
    
    # Whisper 插件接口
    @router.get("/api/plugins/whisper/status")
    async def get_whisper_status():
        try:
            status = plugin_manager.get_plugin_status("whisper", check_running=False)
            tasks = task_manager.get_all_tasks()
            is_installing = any(t.id == "install_whisper" and t.status == "running" for t in tasks)
            return {
                "installed": status["installed"], 
                "running": status["running"], 
                "installing": is_installing,
                "uninstalling": status.get("uninstalling", False)
            }
        except Exception as e:
            return JSONResponse({"installed": False, "error": str(e)}, status_code=200)
    
    @router.post("/api/plugins/whisper/install")
    async def install_whisper():
        try:
            success = task_manager.add_install_task("whisper")
            if success:
                return {"status": "success"}
            else:
                return JSONResponse({"status": "error", "msg": "任务添加失败，可能任务已存在"}, status_code=400)
        except Exception as e:
            logger.error(f"Install Error: {e}", exc_info=True)
            return JSONResponse({"status": "error", "msg": f"服务端异常: {str(e)}"}, status_code=500)
    
    # FunASR 插件接口
    @router.get("/api/plugins/funasr/status")
    async def get_funasr_status():
        try:
            status = plugin_manager.get_plugin_status("funasr", check_running=False)
            tasks = task_manager.get_all_tasks()
            is_installing = any(t.id == "install_funasr" and t.status == "running" for t in tasks)
            return {
                "installed": status["installed"], 
                "running": status["running"], 
                "installing": is_installing,
                "uninstalling": status.get("uninstalling", False)
            }
        except Exception as e:
            logger.error(f"FunASR Status Error: {e}", exc_info=True)
            return JSONResponse({"installed": False, "error": str(e)}, status_code=200)
    
    @router.post("/api/plugins/funasr/install")
    async def install_funasr():
        try:
            success = task_manager.add_install_task("funasr")
            if success:
                return {"status": "success"}
            else:
                return JSONResponse({"status": "error", "msg": "任务添加失败"}, status_code=400)
        except Exception as e:
            return JSONResponse({"status": "error", "msg": f"服务端异常: {str(e)}"}, status_code=500)
    
    # Slides Extractor 插件接口
    @router.get("/api/plugins/slides_extractor/status")
    async def get_slides_extractor_status():
        try:
            status = plugin_manager.get_plugin_status("slides_extractor", check_running=False)
            tasks = task_manager.get_all_tasks()
            is_installing = any(t.id == "install_slides_extractor" and t.status == "running" for t in tasks)
            return {
                "installed": status["installed"], 
                "running": status["running"], 
                "installing": is_installing,
                "uninstalling": status.get("uninstalling", False)
            }
        except Exception as e:
            logger.error(f"Slides Extractor Status Error: {e}", exc_info=True)
            return JSONResponse({"installed": False, "error": str(e)}, status_code=200)
    
    @router.post("/api/plugins/slides_extractor/install")
    async def install_slides_extractor():
        try:
            success = task_manager.add_install_task("slides_extractor")
            if success:
                return {"status": "success"}
            else:
                return JSONResponse({"status": "error", "msg": "任务添加失败"}, status_code=400)
        except Exception as e:
            return JSONResponse({"status": "error", "msg": f"服务端异常: {str(e)}"}, status_code=500)
    
    @router.get("/api/plugins/dependency_check")
    async def check_dependencies():
        return {
            "whisper": plugin_manager.get_plugin_status("whisper", check_running=False)["installed"],
            "funasr": plugin_manager.get_plugin_status("funasr", check_running=False)["installed"],
            "slides_extractor": plugin_manager.get_plugin_status("slides_extractor", check_running=False)["installed"]
        }
    
    @router.post("/api/plugins/{plugin_name}/uninstall")
    def uninstall_plugin_endpoint(plugin_name: str):
        allowed_plugins = ["whisper", "funasr", "slides_extractor"]
        if plugin_name not in allowed_plugins:
            return JSONResponse({"status": "error", "msg": "未知插件"}, status_code=400)
        
        try:
            if hasattr(task_manager, "abort_plugin_task"):
                task_manager.abort_plugin_task(plugin_name)
        except Exception as e:
            print(f"中止任务状态警告: {e}")
        
        success, msg = plugin_manager.uninstall_plugin(plugin_name)
        
        time.sleep(0.5)
        
        if success:
            try:
                from ..utils.config_manager import config_manager
                current_cfg = config_manager.get()
                
                if plugin_name == "whisper" and current_cfg.asr_engine == "whisper":
                    print(">>> 检测到当前引擎(Whisper)被卸载，正在重置配置为 FunASR...")
                    config_manager.save({"asr_engine": "funasr"})
                    
                elif plugin_name == "funasr" and current_cfg.asr_engine == "funasr":
                    print(">>> 检测到当前引擎(FunASR)被卸载，正在重置配置为 Whisper...")
                    config_manager.save({"asr_engine": "whisper"})
                    
            except Exception as e:
                print(f"配置自动修正失败: {e}")
            
            return {"status": "success", "msg": msg}
        else:
            return JSONResponse({"status": "error", "msg": msg}, status_code=500)
    
    return router

