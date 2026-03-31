import os
import argparse
import uvicorn
import torch
import re
import threading
import sys
import io
import uuid
import time
import logging
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response, JSONResponse
from funasr import AutoModel
from modelscope.hub.snapshot_download import snapshot_download
from async_task_manager import get_task_manager, TaskStatus

# 配置标准输出使用 UTF-8 编码（仅在 Windows 且未配置时）
if sys.platform == 'win32' and not isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError):
        pass

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout
)
logger = logging.getLogger('funasr') 

ffmpeg_rel = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../bin"))
os.environ["PATH"] += os.pathsep + ffmpeg_rel
os.environ["FFMPEG_LOG_LEVEL"] = "error" 
os.environ["LOGLEVEL"] = "ERROR"

MODEL_SIZE = "paraformer-zh"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "server_temp")
os.makedirs(TEMP_DIR, exist_ok=True)

app = FastAPI()

# 初始化异步任务管理器
task_manager = get_task_manager(storage_dir=os.path.join(BASE_DIR, "tasks"), max_workers=2)

REMOTE_MODEL_ID = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

# ── 主进程回报接口（模块级，供启动阶段使用）──
MAIN_SERVER_URL = os.environ.get("MAIN_SERVER_URL", "")
PLUGIN_NAME = "funasr"

# ── 全局状态对象类定义 ──
class ServiceStatus:
    """FunASR 服务状态管理"""
    def __init__(self, device: str):
        self.lock = threading.Lock()
        self.phase = "initializing"  # initializing, downloading, loading, ready, failed
        self.progress = 0.0
        self.message = ""
        self.error = ""
        self.timestamp = None
        self.device = device

    def update(self, phase: str = None, message: str = "", progress: float = None, error: str = ""):
        with self.lock:
            if phase:
                self.phase = phase
            if message:
                self.message = message
            if progress is not None:
                self.progress = progress
            if error:
                self.error = error
            self.timestamp = time.time()

    def to_dict(self):
        with self.lock:
            return {
                "phase": self.phase,
                "progress": self.progress,
                "message": self.message,
                "error": self.error,
                "timestamp": self.timestamp,
                "device": self.device,
            }

# 详细的 CUDA 检测
logger.info("=" * 50)
logger.info("CUDA 环境检测:")
logger.info(f"  torch.cuda.is_available(): {torch.cuda.is_available()}")
if torch.cuda.is_available():
    logger.info(f"  CUDA 设备数量: {torch.cuda.device_count()}")
    logger.info(f"  当前 CUDA 设备: {torch.cuda.current_device()}")
    logger.info(f"  设备名称: {torch.cuda.get_device_name(0)}")
    logger.info(f"  CUDA 版本: {torch.version.cuda}")
    logger.info(f"  cuDNN 版本: {torch.backends.cudnn.version()}")
    logger.info(f"  cuDNN 可用: {torch.backends.cudnn.enabled}")
    DEVICE = "cuda"
else:
    logger.info("  未检测到 CUDA，将使用 CPU")
    DEVICE = "cpu"
logger.info(f"  最终选择设备: {DEVICE}")
logger.info("=" * 50)
logger.info(f"正在初始化模型... (当前检测设备: {DEVICE})")

# 在 DEVICE 定义后创建全局状态对象
global_status = ServiceStatus(DEVICE)

def _report_to_main(phase: str, message: str, progress: float = -1, success: bool = True):
    """向主进程汇报启动状态"""
    if not MAIN_SERVER_URL:
        return
    try:
        import requests as _req
        payload = {"phase": phase, "message": message, "success": success}
        if progress >= 0:
            payload["progress"] = progress
        _req.post(
            f"{MAIN_SERVER_URL.rstrip('/')}/api/plugins/{PLUGIN_NAME}/startup_report",
            json=payload, timeout=5
        )
    except Exception:
        pass


def check_and_download_model(local_dir, remote_id):
    if not os.path.exists(local_dir) or not os.listdir(local_dir):
        logger.info(f"检测到本地模型缺失: {local_dir}")
        logger.info(f"开始从 ModelScope 下载模型 ({remote_id})...")
        global_status.update(phase="downloading", message="正在从 ModelScope 下载模型，首次启动可能需要数分钟...", progress=10)
        _report_to_main("downloading", "正在从 ModelScope 下载模型，首次启动可能需要数分钟...", progress=10)
        snapshot_download(remote_id, local_dir=local_dir)
        global_status.update(phase="downloading", message="模型下载完成", progress=60)
        _report_to_main("downloading", "模型下载完成", progress=60)
    else:
        logger.info(f"检测到本地模型已存在: {local_dir}")
        global_status.update(phase="downloading", message="本地模型已存在，跳过下载", progress=60)
        _report_to_main("downloading", "本地模型已存在，跳过下载", progress=60)

# 执行检查
global_status.update(phase="initializing", message="FunASR 服务启动中...", progress=5)
_report_to_main("downloading", "FunASR 服务启动中...", progress=5)
check_and_download_model(MODEL_SIZE, REMOTE_MODEL_ID)

# 设备配置
device = "cuda" if torch.cuda.is_available() else "cpu"
logger.info(f"正在初始化模型... (设备: {device})")

# 全局模型变量
model = None

try:
    logger.info(f"--- 正在加载模型到设备: {device} ---")
    global_status.update(phase="loading", message=f"正在加载 FunASR 模型到 {device.upper()}，请稍候...", progress=65)
    _report_to_main("loading", f"正在加载 FunASR 模型到 {device.upper()}，请稍候...", progress=65)
    model = AutoModel(
        model=MODEL_SIZE,
        trust_remote_code=True,
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        vad_kwargs={"max_single_segment_time": 10000},
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        device=device,
        disable_update=True,
    )
    if model is None:
        raise Exception("模型加载返回 None")
    logger.info("✓ FunASR 模型加载完成，服务准备就绪")
    global_status.update(phase="ready", message=f"FunASR 服务已就绪（{device.upper()}）", progress=100)
    _report_to_main("ready", f"FunASR 服务已就绪（{device.upper()}）", progress=100)

    # 验证模型是否真的在 GPU 上
    if device == "cuda":
        try:
            logger.info(f"--- 验证 GPU 使用情况 ---")
            logger.info(f"  当前 GPU 显存使用: {torch.cuda.memory_allocated(0) / 1024**3:.2f} GB")
            logger.info(f"  GPU 显存缓存: {torch.cuda.memory_reserved(0) / 1024**3:.2f} GB")
        except Exception as e:
            logger.warning(f"  警告: 无法获取 GPU 信息 - {e}")

except Exception as e:
    logger.error(f"✗ 严重错误: FunASR 模型加载失败 - {e}")
    logger.error("✗ 服务将不会启动，请检查:")
    logger.error("  1. 模型文件是否完整")
    logger.error("  2. 显存/内存是否充足")
    logger.error("  3. 依赖库是否正确安装")
    global_status.update(phase="failed", message=f"FunASR 模型加载失败: {e}", error=str(e), progress=0)
    _report_to_main("failed", f"FunASR 模型加载失败: {e}", progress=0, success=False)
    # 模型加载失败时，阻止服务启动
    raise RuntimeError(f"FunASR 模型加载失败: {e}")

def format_srt_time(ms):
    seconds, milliseconds = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02},{int(milliseconds):03}"

def split_long_segment(text, start_ms, end_ms, max_chars=25):
    if len(text) <= max_chars:
        return [{"text": text, "start": start_ms, "end": end_ms}]
    
    sub_texts = re.split(r'(?<=[，。？！；,?!;])', text)
    sub_texts = [t for t in sub_texts if t.strip()]
    
    if not sub_texts:
        return [{"text": text, "start": start_ms, "end": end_ms}]

    total_len = len(text)
    total_duration = end_ms - start_ms
    
    results = []
    current_start = start_ms
    
    for sub_text in sub_texts:
        sub_len = len(sub_text)
        duration_ratio = sub_len / total_len
        segment_duration = total_duration * duration_ratio
        current_end = current_start + segment_duration
        
        results.append({
            "text": sub_text,
            "start": current_start,
            "end": current_end
        })
        current_start = current_end
        
    return results

def generate_optimized_srt(res_item):
    srt_content = ""
    segments = res_item.get('sentence_info', [])
    if not segments:
        return f"1\n00:00:00,000 --> 00:00:10,000\n{res_item.get('text', '')}\n"

    counter = 1
    for seg in segments:
        raw_text = seg.get('text', '').strip()
        raw_start = seg.get('start', 0)
        raw_end = seg.get('end', 0)
        
        processed_segments = split_long_segment(raw_text, raw_start, raw_end, max_chars=30)
        
        for p_seg in processed_segments:
            if not p_seg['text'].strip():
                continue
            start_time = format_srt_time(p_seg['start'])
            end_time = format_srt_time(p_seg['end'])
            text = p_seg['text']
            srt_content += f"{counter}\n{start_time} --> {end_time}\n{text}\n\n"
            counter += 1
    return srt_content


def _do_transcribe_work(task_id: str, temp_path: str):
    """执行转写工作（在后台线程中运行）"""
    import gc
    from datetime import datetime
    
    unique_id = os.path.basename(temp_path).split('_')[1]
    srt_path = os.path.join(TEMP_DIR, f"{unique_id}.srt")
    
    # 启动心跳线程，防止被误判为卡死
    stop_heartbeat = threading.Event()
    last_update_time = [time.time()]  # 使用列表以便在闭包中修改
    
    def heartbeat():
        while not stop_heartbeat.is_set():
            time.sleep(30)  # 每30秒更新一次
            if not stop_heartbeat.is_set():
                elapsed = int(time.time() - last_update_time[0])
                # 更新心跳时间戳到 metadata
                task_manager.update_task(
                    task_id, 
                    message=f"正在处理中... (已运行 {elapsed}秒)",
                    metadata={'last_heartbeat': datetime.now().isoformat()}
                )
    
    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    
    try:
        task_manager.update_task(
            task_id, 
            message="正在进行语音识别...", 
            progress=10.0,
            metadata={'last_heartbeat': datetime.now().isoformat()}
        )
        last_update_time[0] = time.time()

        res = model.generate(
            input=[temp_path],
            cache={},
            batch_size=1, 
            language="中文", 
            itn=True,
            sentence_timestamp=True,
            vad_kwargs={"max_single_segment_time": 10000} 
        )
        
        # 记录 GPU 使用情况（如果使用 CUDA）
        if device == "cuda" and torch.cuda.is_available():
            logger.info(f"[GPU] 推理后显存使用: {torch.cuda.memory_allocated(0) / 1024**3:.2f} GB")
        
        task_manager.update_task(
            task_id, 
            message="正在生成字幕文件...", 
            progress=80.0,
            metadata={'last_heartbeat': datetime.now().isoformat()}
        )
        last_update_time[0] = time.time()
            
        if not res:
            srt_output = ""
        else:
            text_content = res[0].get('text', '')
            srt_output = generate_optimized_srt(res[0])
        
        # 保存结果
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_output)
        
        # 返回结果文件路径
        return {"srt_path": srt_path}

    finally:
        # 停止心跳线程
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1)
        
        # 清理临时音频文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        
        # 强制清理 GPU 显存（修复内存泄漏）
        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()  # 确保所有 CUDA 操作完成
        
        # 强制垃圾回收
        gc.collect()

@app.get("/status")
async def get_status():
    """获取 FunASR 服务状态"""
    return JSONResponse(content=global_status.to_dict())

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """同步转写接口（保持向后兼容）"""
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"error": "模型未正确加载，服务不可用"}
        )
    
    temp_path = f"temp_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            f.write(await file.read())

        res = model.generate(
            input=[temp_path],
            cache={},
            batch_size=1, 
            language="中文", 
            itn=True,
            sentence_timestamp=True,
            vad_kwargs={"max_single_segment_time": 10000} 
        )
        
        # 记录 GPU 使用情况（如果使用 CUDA）
        if device == "cuda" and torch.cuda.is_available():
            logger.info(f"[GPU] 推理后显存使用: {torch.cuda.memory_allocated(0) / 1024**3:.2f} GB")
            
        if not res:
            srt_output = ""
        else:
            text_content = res[0].get('text', '')
            srt_output = generate_optimized_srt(res[0])
        
        return Response(content=srt_output, media_type="application/x-subrip")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response(content=f"Error: {str(e)}", status_code=500)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()


@app.post("/transcribe_async")
async def transcribe_async(file: UploadFile = File(...)):
    """异步转写接口 - 立即返回任务ID"""
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"error": "模型未正确加载，服务不可用"}
        )
    
    unique_id = str(uuid.uuid4())
    temp_path = os.path.join(TEMP_DIR, f"temp_{unique_id}_{file.filename}")

    try:
        # 保存上传的文件
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        
        # 创建异步任务
        task_id = task_manager.create_task(
            task_type="transcribe",
            metadata={
                "filename": file.filename,
                "audio_path": temp_path
            }
        )
        
        # 提交任务到后台执行
        task_manager.submit_task(task_id, _do_transcribe_work, temp_path)
        
        # 立即返回任务ID
        return JSONResponse(content={
            "status": "accepted",
            "task_id": task_id,
            "message": "任务已提交，请使用 /task/{task_id} 查询进度"
        })
    
    except Exception as e:
        logger.error(f"Error creating async task: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    task = task_manager.get_task_status(task_id)
    
    if not task:
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found"}
        )
    
    return JSONResponse(content=task)


@app.get("/task/{task_id}/result")
async def get_task_result(task_id: str):
    """获取任务结果（下载字幕文件）"""
    task = task_manager.get_task(task_id)
    
    if not task:
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found"}
        )
    
    if task.status != TaskStatus.COMPLETED:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Task not completed",
                "status": task.status.value,
                "message": task.message
            }
        )
    
    # 获取结果文件路径
    result = task.result
    if not result or "srt_path" not in result:
        return JSONResponse(
            status_code=500,
            content={"error": "Result file not found"}
        )
    
    srt_path = result["srt_path"]
    if not os.path.exists(srt_path):
        return JSONResponse(
            status_code=404,
            content={"error": "Result file has been deleted"}
        )
    
    return Response(
        content=open(srt_path, 'rb').read(),
        media_type="application/x-subrip"
    )


@app.get("/tasks")
async def list_tasks():
    """列出所有任务"""
    tasks = task_manager.list_tasks()
    return JSONResponse(content={"tasks": tasks})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    def monitor_stdin():
        try:
            sys.stdin.read() 
        except Exception:
            pass
        finally:
            logger.info("父进程管道断开，服务自毁...")
            os._exit(0)
    watcher = threading.Thread(target=monitor_stdin, daemon=True)
    watcher.start()
    logger.info(f"服务启动: http://127.0.0.1:{args.port}")
    
    # 配置 uvicorn 日志 - 禁用颜色输出以避免 ANSI 转义码
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["use_colors"] = False
    log_config["formatters"]["access"]["use_colors"] = False
    
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=log_config)
