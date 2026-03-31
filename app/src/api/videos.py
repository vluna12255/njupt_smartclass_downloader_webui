"""视频路由：搜索课程和批量下载"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..models.models import VideoSearchCondition
from ..utils.logger import get_logger

logger = get_logger('videos_api')

router = APIRouter()


def setup_video_routes(templates: Jinja2Templates, session_manager, task_manager):
    """注册视频路由：POST /search, POST /batch_download"""
    
    def _search_with_keyword(client, keyword, page_size_request, max_retries, session_manager_ref):
        """用单个关键词变体抓取所有分页结果，返回视频列表"""
        videos = []
        current_page = 1
        retry_count = 0
        while True:
            try:
                condition = VideoSearchCondition(
                    title_key=keyword,
                    page_number=current_page,
                    page_size=page_size_request
                )
                result = client.search_video(condition)
                retry_count = 0
                if not result or not result.videos:
                    break
                videos.extend(result.videos)
                if len(result.videos) < page_size_request or len(videos) >= 500:
                    break
                current_page += 1
            except Exception as e:
                error_str = str(e)
                if "302" in error_str and retry_count < max_retries:
                    success, _ = session_manager_ref.perform_auto_login()
                    if success:
                        client = session_manager_ref.get_client()
                        retry_count += 1
                        continue
                    else:
                        break
                else:
                    logger.error(f"搜索关键词 '{keyword}' 失败: {error_str}")
                    break
        return videos

    @router.post("/search", response_class=HTMLResponse)
    async def search_video(request: Request, keyword: str = Form(""), page: int = Form(1)):
        keyword = keyword.strip()
        client = session_manager.get_client()
        
        if not keyword:
            # 返回 None 表示未开始搜索，不显示任何内容
            if templates is None:
                return HTMLResponse("Templates error", status_code=500)
            return templates.TemplateResponse("partials/video_list.html", {
                "request": request, 
                "videos": None
            })
        
        if not client:
            session_manager.perform_auto_login()
            client = session_manager.get_client()
        
        if not client:
            return HTMLResponse('<div class="col-span-full text-center text-red-500 py-20"><p class="text-base font-medium">登录失效</p><p class="text-sm mt-2">请检查账号密码配置或重新登录</p></div>', status_code=401)
        
        page_size_request = 50
        max_retries = 2

        # 生成大小写变体：原始、全小写、全大写、首字母大写、每词首字母大写
        variants_ordered = []
        seen_variants = set()
        for variant in [
            keyword,
            keyword.lower(),
            keyword.upper(),
            keyword.capitalize(),
            keyword.title(),
        ]:
            if variant not in seen_variants:
                seen_variants.add(variant)
                variants_ordered.append(variant)

        logger.info(f"正在搜索关键词: {keyword}，变体: {variants_ordered}")

        seen_ids = set()
        all_videos = []
        for variant in variants_ordered:
            variant_results = _search_with_keyword(
                client, variant, page_size_request, max_retries, session_manager
            )
            for v in variant_results:
                if v.id not in seen_ids:
                    seen_ids.add(v.id)
                    all_videos.append(v)
        
        logger.info(f"搜索完成: 关键词 '{keyword}' 共找到 {len(all_videos)} 个结果")
        
        if templates is None:
            return HTMLResponse("Templates error", status_code=500)
        return templates.TemplateResponse("partials/video_list.html", {
            "request": request, 
            "videos": all_videos
        })
    
    @router.post("/batch_download")
    async def batch_download(request: Request):
        form_data = await request.form()
        video_ids = form_data.getlist("video_ids")
        raw_file_types = form_data.getlist("file_types")
        
        whisper_vga = form_data.get("whisper_vga") == "true"
        whisper_video1 = form_data.get("whisper_video1") == "true"
        whisper_video2 = form_data.get("whisper_video2") == "true"
        
        legacy_use_whisper = form_data.get("use_whisper") == "true"
        if legacy_use_whisper and not (whisper_vga or whisper_video1 or whisper_video2):
            whisper_vga = whisper_video1 = whisper_video2 = True
        
        if not video_ids:
            return JSONResponse({"status": "error", "msg": "未选择任何视频"})
        
        file_types = list(raw_file_types)
        
        # 检查插件状态
        from ..plugins.plugin_manager import plugin_manager
        from ..utils.config_manager import config_manager
        
        if "PPT" in file_types:
            has_slides = plugin_manager.get_plugin_status("slides_extractor", check_running=False)["installed"]
            if not has_slides:
                logger.info(">>> [Task Security] 检测到 PPT 请求但插件未安装，已自动移除 PPT 任务")
                file_types.remove("PPT")
        
        current_cfg = config_manager.get()
        
        # 优先使用前端传递的引擎选择（即使用户没有保存设置）
        frontend_engine = form_data.get("asr_engine")
        if frontend_engine:
            current_engine = frontend_engine
            logger.info(f"使用前端选择的引擎: {current_engine}")
        else:
            current_engine = current_cfg.asr_engine
            logger.info(f"使用配置文件中的引擎: {current_engine}")
        
        engine_status = plugin_manager.get_plugin_status(current_engine, check_running=False)["installed"]
        
        if not engine_status:
            if whisper_vga or whisper_video1 or whisper_video2:
                logger.info(f">>> [Task Security] 检测到字幕请求但引擎 {current_engine} 未安装，已自动取消字幕生成")
            whisper_vga = False
            whisper_video1 = False
            whisper_video2 = False
        
        client = session_manager.get_client()
        if not client or not session_manager.is_session_valid():
            session_manager.perform_auto_login()
            client = session_manager.get_client()
        
        if not client:
            return JSONResponse({"status": "error", "msg": "登录已失效，请刷新页面"})
        
        # 根据当前配置的引擎动态设置 API URL
        whisper_url = ""
        if hasattr(current_cfg, "whisper_url"):
            whisper_url = current_cfg.whisper_url
        
        # 如果配置的引擎是 funasr，但 URL 还是默认的 whisper URL，则自动修正
        if current_engine == "funasr" and ":8000" in whisper_url:
            whisper_url = whisper_url.replace(":8000", ":8001")
            logger.info(f"检测到引擎为 funasr，自动调整 URL 为: {whisper_url}")
        elif current_engine == "whisper" and ":8001" in whisper_url:
            whisper_url = whisper_url.replace(":8001", ":8000")
            logger.info(f"检测到引擎为 whisper，自动调整 URL 为: {whisper_url}")
        
        whisper_task_config = {
            "api_url": whisper_url,
            "tracks": {
                "VGA": whisper_vga,
                "Video1": whisper_video1,
                "Video2": whisper_video2
            }
        }
        
        count = 0
        session = session_manager.get_session()
        for vid in video_ids:
            try:
                info = client.get_video_info_by_id(vid)
                task_manager.add_batch_task(
                    vid, 
                    info.title,  # 使用服务器返回的原始标题
                    session, 
                    file_types, 
                    whisper_config=whisper_task_config
                )
                count += 1
            except Exception as e:
                logger.error(f"Task Add Failed {vid}: {e}", exc_info=True)
        
        msg = f"已添加 {count} 个任务"
        if "PPT" in raw_file_types and "PPT" not in file_types:
            msg += " (PPT插件未安装，已忽略PPT)"
        
        return JSONResponse({"status": "success", "msg": msg})
    
    return router

