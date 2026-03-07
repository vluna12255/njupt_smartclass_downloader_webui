"""配置管理路由：读取和保存系统设置"""
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from ..utils.config_manager import config_manager
from ..utils.logger import get_logger

logger = get_logger('config_api')

router = APIRouter()


def setup_config_routes(plugin_manager, session_manager):
    """注册配置路由：GET/POST /config"""
    
    @router.get("/config")
    async def get_config():
        """获取当前配置"""
        try:
            config_obj = config_manager.get()
            if hasattr(config_obj, "model_dump"):
                data = config_obj.model_dump()
            elif hasattr(config_obj, "dict"):
                data = config_obj.dict()
            else:
                try:
                    data = config_obj.__dict__.copy()
                except:
                    data = {}
            
            auth_data = config_manager.get_auth()
            if auth_data:
                data["auth"] = auth_data
                if "username" in auth_data: 
                    data["username"] = auth_data["username"]
                if "password" in auth_data: 
                    data["password"] = auth_data["password"]
            
            if "auto_login" not in data:
                data["auto_login"] = False
            
            return data
            
        except Exception as e:
            logger.error(f"获取配置失败: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "msg": "获取配置失败",
                    "details": str(e)
                }
            )
    
    @router.post("/config")
    def save_config(data: dict = Body(...)):
        """保存配置"""
        try:
            requested_engine = data.get("asr_engine")
            
            # 检查插件状态
            whisper_status = plugin_manager.get_plugin_status("whisper", check_running=False)["installed"]
            funasr_status = plugin_manager.get_plugin_status("funasr", check_running=False)["installed"]
            
            final_engine = requested_engine
            fallback_applied = False
            
            # 智能回退逻辑
            if requested_engine == "whisper" and not whisper_status:
                logger.warning("试图启用 Whisper 但未安装，拦截请求")
                if funasr_status:
                    final_engine = "funasr"
                    fallback_applied = True
                    logger.info("自动回退到 FunASR")
                    
            elif requested_engine == "funasr" and not funasr_status:
                logger.warning("试图启用 FunASR 但未安装，拦截请求")
                if whisper_status:
                    final_engine = "whisper"
                    fallback_applied = True
                    logger.info("自动回退到 Whisper")
            
            if final_engine:
                data["asr_engine"] = final_engine
            
            # 验证语音引擎可用性
            is_engine_valid = False
            if data.get("asr_engine") == "whisper" and whisper_status:
                is_engine_valid = True
            elif data.get("asr_engine") == "funasr" and funasr_status:
                is_engine_valid = True
            
            # 如果引擎不可用，禁用相关选项
            if not is_engine_valid:
                if any([data.get("default_whisper_vga"), 
                       data.get("default_whisper_video1"), 
                       data.get("default_whisper_video2")]):
                    logger.warning("语音引擎不可用，强制禁用默认字幕选项")
                data["default_whisper_vga"] = False
                data["default_whisper_video1"] = False
                data["default_whisper_video2"] = False
                data["auto_whisper"] = False
            
            # 检查 PPT 提取插件
            slides_status = plugin_manager.get_plugin_status("slides_extractor", check_running=False)["installed"]
            if data.get("default_ppt") is True and not slides_status:
                logger.warning("Slides Extractor 未安装，强制禁用默认 PPT 选项")
                data["default_ppt"] = False
            
            # 保存配置
            success, error_msg = config_manager.save(data)
            
            if not success:
                logger.error(f"配置保存失败: {error_msg}")
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "msg": "配置保存失败",
                        "details": error_msg
                    }
                )
            
            # 自动登录
            if data.get("auto_login", False) and not session_manager.get_client():
                session_manager.perform_auto_login()
            
            # 构建响应消息
            msg = "设置已保存"
            if fallback_applied or not is_engine_valid or (data.get("default_ppt") and not slides_status):
                msg += " (部分无效设置已被自动修正)"
            
            return {
                "status": "success",
                "msg": msg
            }
            
        except Exception as e:
            logger.error(f"保存配置时发生错误: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "msg": "保存配置失败",
                    "details": str(e)
                }
            )
    
    return router

