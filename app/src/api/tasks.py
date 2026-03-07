"""任务管理路由：实时任务状态查询"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()


def setup_task_routes(templates: Jinja2Templates, task_manager):
    """注册任务路由：GET /tasks_status"""
    
    @router.get("/tasks_status", response_class=HTMLResponse)
    async def tasks_status(request: Request):
        if templates is None:
            return HTMLResponse("Templates error", status_code=500)
        tasks = task_manager.get_all_tasks()
        return templates.TemplateResponse("partials/task_list.html", {
            "request": request,
            "tasks": tasks
        })
    
    return router

