"""认证路由：登录页面和登录处理"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import requests
from requests.exceptions import Timeout, ConnectionError

from ..core.njupt_sso import NjuptSso, NjuptSsoException
from ..core.smartclass_client import SmartclassClient
from ..utils.logger import get_logger

logger = get_logger('auth_api')

router = APIRouter()


def translate_login_error(error_text: str) -> str:
    """将技术错误信息转换为用户友好的中文提示"""
    error_lower = error_text.lower()
    
    if "read timed out" in error_lower or "timeout" in error_lower:
        return "网络连接超时，请检查网络或稍后重试"
    elif "connection refused" in error_lower:
        return "无法连接到服务器"
    elif "connection error" in error_lower or "connectionerror" in error_lower:
        return "网络连接错误"
    elif "name or service not known" in error_lower:
        return "域名解析失败，请检查网络"
    elif "ssl" in error_lower or "certificate" in error_lower:
        return "SSL证书验证失败"
    elif "401" in error_text or "unauthorized" in error_lower:
        return "账号或密码错误"
    elif "403" in error_text or "forbidden" in error_lower:
        return "访问被拒绝"
    elif "500" in error_text or "internal server error" in error_lower:
        return "服务器内部错误"
    
    if len(error_text) > 60:
        return "登录失败，请检查网络连接"
    
    return error_text


def perform_login(username: str, password: str):
    """执行南邮SSO登录并初始化智慧课堂客户端
    
    Returns:
        (成功标志, session, client, 消息)
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://njupt.smartclass.cn/",
            "Origin": "https://njupt.smartclass.cn",
            "X-Requested-With": "XMLHttpRequest"
        })
        
        sso = NjuptSso(session)
        sso.login(username, password)
        sso.grant_service("https://njupt.smartclass.cn/Login/SSO")
        session.get("https://njupt.smartclass.cn/", timeout=10)
        
        client = SmartclassClient(session)
        client.get_csrk_token()
        
        return True, session, client, "登录成功"
        
    except Timeout:
        return False, None, None, "网络连接超时，请检查网络或稍后重试"
    except ConnectionError:
        return False, None, None, "网络连接错误，请检查网络"
    except NjuptSsoException as e:
        return False, None, None, f"账号或密码错误: {e.message}"
    except Exception as e:
        error_msg = translate_login_error(str(e))
        return False, None, None, error_msg


def setup_auth_routes(templates: Jinja2Templates, session_manager):
    """注册登录相关路由：GET/POST /login"""
    
    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if templates is None:
            return HTMLResponse("Templates error", status_code=500)
        return templates.TemplateResponse("login.html", {"request": request})
    
    @router.post("/login", response_class=HTMLResponse)
    async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
        if templates is None:
            return HTMLResponse("Templates error", status_code=500)
        
        success, session, client, message = perform_login(username, password)
        
        if success:
            # 保存到全局session管理器
            session_manager.set_session(session, client)
            
            # 保存凭证
            from ..utils.config_manager import config_manager
            config_manager.save_auth(username, password)
            
            return RedirectResponse(url="/", status_code=303)
        else:
            return templates.TemplateResponse("login.html", {
                "request": request, 
                "error": message
            })
    
    return router

