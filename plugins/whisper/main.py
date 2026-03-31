import uvicorn
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from faster_whisper import WhisperModel, download_model
import os
import shutil
import json
import torch
import uuid
import time
from datetime import timedelta
import ast
import argparse
from contextlib import asynccontextmanager
import threading
import sys
import io

if sys.platform == 'win32' and not isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError):
        pass

# 导入异步任务管理器
from async_task_manager import get_task_manager, TaskStatus

MODEL_SIZE = "large-v3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR_PATH = os.path.join(BASE_DIR, MODEL_SIZE)
TEMP_DIR = os.path.join(BASE_DIR, "server_temp")

# ── 主进程回报接口 ──
MAIN_SERVER_URL = os.environ.get("MAIN_SERVER_URL", "")
PLUGIN_NAME = "whisper"

# ── 全局状态对象类定义 ──
class ServiceStatus:
    """Whisper 服务状态管理"""
    def __init__(self, device: str, compute_type: str):
        self.lock = threading.Lock()
        self.phase = "initializing"  # initializing, downloading, loading, ready, failed
        self.progress = 0.0
        self.message = ""
        self.error = ""
        self.timestamp = None
        self.device = device
        self.compute_type = compute_type
    
    def update(self, phase: str = None, message: str = "", progress: float = None, error: str = ""):
        """更新状态"""
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
        """转换为字典"""
        with self.lock:
            return {
                "phase": self.phase,
                "progress": self.progress,
                "message": self.message,
                "error": self.error,
                "timestamp": self.timestamp,
                "device": self.device,
                "compute_type": self.compute_type
            }

def _report_to_main(phase: str, message: str, progress: float = -1, success: bool = True):
    """向主进程汇报启动状态"""
    if not MAIN_SERVER_URL:
        return
    try:
        import requests as _rq
        payload = {"phase": phase, "message": message, "success": success}
        if progress >= 0:
            payload["progress"] = progress
        _rq.post(
            f"{MAIN_SERVER_URL.rstrip('/')}/api/plugins/{PLUGIN_NAME}/startup_report",
            json=payload, timeout=5
        )
    except Exception:
        pass

# 详细的 CUDA 检测
print("=" * 50)
print("CUDA 环境检测:")
print(f"  torch.cuda.is_available(): {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"  CUDA 设备数量: {torch.cuda.device_count()}")
    print(f"  当前 CUDA 设备: {torch.cuda.current_device()}")
    print(f"  设备名称: {torch.cuda.get_device_name(0)}")
    print(f"  CUDA 版本: {torch.version.cuda}")
    print(f"  cuDNN 版本: {torch.backends.cudnn.version()}")
    print(f"  cuDNN 可用: {torch.backends.cudnn.enabled}")
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"
else:
    print("  未检测到 CUDA，将使用 CPU")
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"
print(f"  最终选择设备: {DEVICE}")
print(f"  计算类型: {COMPUTE_TYPE}")
print("=" * 50)

# 在 DEVICE 和 COMPUTE_TYPE 定义后创建全局状态对象
global_status = ServiceStatus(DEVICE, COMPUTE_TYPE)

print(f"正在初始化模型... (当前检测设备: {DEVICE})")

global_model = None

def _validate_model_files(model_path: str) -> bool:
    """校验模型目录中的关键文件是否完整（model.bin 必须存在且 > 2000MB）"""
    model_bin = os.path.join(model_path, "model.bin")
    if not os.path.exists(model_bin):
        return False
    if os.path.getsize(model_bin) < 2000 * 1024 * 1024: 
        return False
    return True
0

def load_model_logic():
    print(f"正在启动服务，开始检查模型文件 ---")
    print(f"--- 目标模型: {MODEL_SIZE} ---")
    print(f"--- 存储目录: {MODEL_DIR_PATH} ---")

    # 检测模型文件完整性，不完整则清理后重新下载
    if os.path.exists(MODEL_DIR_PATH) and not _validate_model_files(MODEL_DIR_PATH):
        print(f"--- 检测到模型文件不完整（model.bin 缺失或过小），正在清理残留目录以重新下载 ---")
        global_status.update(phase="downloading", message="模型文件不完整，正在重新下载...", progress=1)
        _report_to_main("downloading", "模型文件不完整，正在重新下载...", progress=1)
        try:
            import shutil as _shutil
            _shutil.rmtree(MODEL_DIR_PATH)
            print(f"--- 已清理目录: {MODEL_DIR_PATH} ---")
        except Exception as clean_err:
            print(f"--- 清理目录失败: {clean_err}，将尝试继续下载 ---")

    # ── 阶段1：下载模型 ──
    global_status.update(phase="downloading", message=f"正在下载模型 {MODEL_SIZE}...", progress=5)
    _report_to_main("downloading", f"正在下载模型 {MODEL_SIZE}...", progress=5)
    try:
        actual_model_path = download_model(
            MODEL_SIZE,
            output_dir=MODEL_DIR_PATH
        )
    except Exception as dl_err:
        err_msg = f"模型下载失败: {dl_err}"
        print(f"严重错误: {err_msg}")
        global_status.update(phase="failed", message=err_msg, error=err_msg, progress=0)
        _report_to_main("failed", err_msg, progress=0, success=False)
        raise RuntimeError(err_msg) from dl_err

    # ── 阶段2：加载模型到设备 ──
    print(f"--- 正在加载模型到设备: {DEVICE}, 计算类型: {COMPUTE_TYPE} ---")
    global_status.update(phase="loading", message=f"正在加载模型到 {DEVICE.upper()}...", progress=80)
    _report_to_main("loading", f"正在加载模型到 {DEVICE.upper()}...", progress=80)
    try:
        model = WhisperModel(
            actual_model_path,
            device=DEVICE,
            compute_type=COMPUTE_TYPE
        )
    except Exception as load_err:
        err_msg = f"模型加载失败: {load_err}"
        print(f"严重错误: {err_msg}")
        global_status.update(phase="failed", message=err_msg, error=err_msg, progress=0)
        _report_to_main("failed", err_msg, progress=0, success=False)
        raise RuntimeError(err_msg) from load_err

    print("--- 模型加载完成 ---")

    # 验证模型是否真的在 GPU 上
    if DEVICE == "cuda":
        try:
            print(f"--- 验证 GPU 使用情况 ---")
            print(f"  当前 GPU 显存使用: {torch.cuda.memory_allocated(0) / 1024**3:.2f} GB")
            print(f"  GPU 显存缓存: {torch.cuda.memory_reserved(0) / 1024**3:.2f} GB")
        except Exception as e:
            print(f"  警告: 无法获取 GPU 信息 - {e}")

    return model

@asynccontextmanager
async def lifespan(app: FastAPI):
    global global_model
    
    # 启动时清理 server_temp 目录
    try:
        if os.path.exists(TEMP_DIR):
            print(f"[启动清理] 正在清理临时目录: {TEMP_DIR}")
            cleaned_count = 0
            for item in os.listdir(TEMP_DIR):
                item_path = os.path.join(TEMP_DIR, item)
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                        cleaned_count += 1
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                        cleaned_count += 1
                except Exception as e:
                    print(f"[启动清理] 无法删除 {item}: {e}")
            if cleaned_count > 0:
                print(f"[启动清理] 已清理 {cleaned_count} 个文件/目录")
        else:
            os.makedirs(TEMP_DIR, exist_ok=True)
            print(f"[启动清理] 创建临时目录: {TEMP_DIR}")
    except Exception as e:
        print(f"[启动清理] 清理临时目录失败: {e}")
    
    try:
        global_model = load_model_logic()
        global_status.update(phase="ready", message=f"Whisper 服务已就绪（{DEVICE.upper()}）", progress=100)
        _report_to_main("ready", f"Whisper 服务已就绪（{DEVICE.upper()}）", progress=100)
    except Exception as e:
        print(f"严重错误: 模型加载失败 - {e}")
        global_status.update(phase="failed", message=f"Whisper 模型加载失败: {e}", error=str(e), progress=0)
        _report_to_main("failed", f"Whisper 模型加载失败: {e}", progress=0, success=False)
    yield 

    print("正在关闭服务，清理资源...")
    if global_model:
        del global_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

app = FastAPI(lifespan=lifespan)

# 初始化异步任务管理器
task_manager = get_task_manager(storage_dir=os.path.join(BASE_DIR, "tasks"), max_workers=2)

SKIP_KEYWORDS = ["打赏支持明镜与点点栏目"]
os.makedirs(TEMP_DIR, exist_ok=True)

# ── 状态查询端点 ──
@app.get("/status")
async def get_status():
    """获取 Whisper 服务状态"""
    return JSONResponse(content=global_status.to_dict())

def format_timestamp(seconds: float):
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _do_transcribe_work(task_id: str, temp_path: str, config: dict):
    """执行转写工作（在后台线程中运行）"""
    unique_id = os.path.basename(temp_path).split('_')[1]
    srt_path = os.path.join(TEMP_DIR, f"{unique_id}.srt")
    
    try:
        task_manager.update_task(task_id, message="正在进行语音识别...", progress=10.0)
        
        # 解析配置
        w_cfg = config.get("whisper", {})
        vad_cfg = config.get("vad", {})
        
        lang_map = {"chinese": "zh", "english": "en", "japanese": "ja"}
        input_lang = w_cfg.get("lang", "chinese")
        language_code = lang_map.get(input_lang, input_lang) if input_lang != "auto" else None

        suppress_tokens_val = w_cfg.get("suppress_tokens", "[-1]")
        if isinstance(suppress_tokens_val, str):
            try:
                suppress_tokens_val = ast.literal_eval(suppress_tokens_val)
            except:
                suppress_tokens_val = [-1]

        vad_parameters = {
            "threshold": vad_cfg.get("threshold", 0.5),
            "min_speech_duration_ms": vad_cfg.get("min_speech_duration_ms", 250),
            "max_speech_duration_s": vad_cfg.get("max_speech_duration_s", 9999),
            "min_silence_duration_ms": vad_cfg.get("min_silence_duration_ms", 1000),
            "speech_pad_ms": vad_cfg.get("speech_pad_ms", 2000),
        }

        segments, info = global_model.transcribe(
            temp_path,
            language=language_code,
            task="translate" if w_cfg.get("is_translate") else "transcribe",
            beam_size=w_cfg.get("beam_size", 5),
            best_of=w_cfg.get("best_of", 5),
            patience=w_cfg.get("patience", 1.0),
            length_penalty=w_cfg.get("length_penalty", 1.0),
            temperature=w_cfg.get("temperature", 0.0),
            compression_ratio_threshold=w_cfg.get("compression_ratio_threshold", 2.5),
            log_prob_threshold=w_cfg.get("log_prob_threshold", -1.0),
            no_speech_threshold=w_cfg.get("no_speech_threshold", 0.6),
            condition_on_previous_text=w_cfg.get("condition_on_previous_text", False),
            initial_prompt=w_cfg.get("initial_prompt"),
            prefix=w_cfg.get("prefix"),
            suppress_blank=w_cfg.get("suppress_blank", True),
            suppress_tokens=suppress_tokens_val,
            repetition_penalty=w_cfg.get("repetition_penalty", 1.0),
            no_repeat_ngram_size=w_cfg.get("no_repeat_ngram_size", 0),
            max_new_tokens=w_cfg.get("max_new_tokens"),
            hotwords=w_cfg.get("hotwords"),
            hallucination_silence_threshold=w_cfg.get("hallucination_silence_threshold"),
            vad_filter=vad_cfg.get("vad_filter", True),
            vad_parameters=vad_parameters,
            word_timestamps=w_cfg.get("word_timestamps", False)
        )
        
        task_manager.update_task(task_id, message="正在生成字幕文件...", progress=80.0)

        with open(srt_path, "w", encoding="utf-8") as srt_file:
            srt_index = 0
            for i, segment in enumerate(segments, start=1):
                start_time = format_timestamp(segment.start)
                end_time = format_timestamp(segment.end)
                text = segment.text.strip()
                
                if any(keyword in text for keyword in SKIP_KEYWORDS):
                    print(f"[跳过幻觉片段 - 原ID:{i}]: {text}", flush=True)
                    continue 
                
                srt_index += 1
                print(f"[{srt_index}] {start_time} -> {end_time}: {text}", flush=True)
                
                srt_file.write(f"{srt_index}\n")
                srt_file.write(f"{start_time} --> {end_time}\n")
                srt_file.write(f"{text}\n\n")
        
        # 返回结果文件路径
        return {"srt_path": srt_path, "unique_id": unique_id}

    finally:
        # 清理临时音频文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print(f"[清理] 已删除临时音频文件: {os.path.basename(temp_path)}", flush=True)
            except Exception as e:
                print(f"[清理失败] 无法删除音频文件 {temp_path}: {e}", flush=True)
        
        # 清理 GPU 缓存
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

@app.post("/transcribe")
async def transcribe_api(
    file: UploadFile = File(...),
    config_json: str = Form(...)
):
    if global_model is None:
        return {"error": "Model is not loaded properly."}

    unique_id = str(uuid.uuid4())
    temp_audio_path = os.path.join(TEMP_DIR, f"{unique_id}_{file.filename}")
    srt_path = os.path.join(TEMP_DIR, f"{unique_id}.srt")

    try:
        with open(temp_audio_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        try:
            cfg = json.loads(config_json)
            w_cfg = cfg.get("whisper", {})
            vad_cfg = cfg.get("vad", {})
        except:
            w_cfg = {}
            vad_cfg = {}

        lang_map = {"chinese": "zh", "english": "en", "japanese": "ja"}
        input_lang = w_cfg.get("lang", "chinese")
        language_code = lang_map.get(input_lang, input_lang) if input_lang != "auto" else None

        suppress_tokens_val = w_cfg.get("suppress_tokens", "[-1]")
        if isinstance(suppress_tokens_val, str):
            try:
                suppress_tokens_val = ast.literal_eval(suppress_tokens_val)
            except:
                suppress_tokens_val = [-1]

        vad_parameters = {
            "threshold": vad_cfg.get("threshold", 0.5),
            "min_speech_duration_ms": vad_cfg.get("min_speech_duration_ms", 250),
            "max_speech_duration_s": vad_cfg.get("max_speech_duration_s", 9999),
            "min_silence_duration_ms": vad_cfg.get("min_silence_duration_ms", 1000),
            "speech_pad_ms": vad_cfg.get("speech_pad_ms", 2000),
        }

        print(f"[{unique_id}] 开始转录... BeamSize: {w_cfg.get('beam_size', 5)}")

        segments, info = global_model.transcribe(
            temp_audio_path,
            language=language_code,
            task="translate" if w_cfg.get("is_translate") else "transcribe",
            beam_size=w_cfg.get("beam_size", 5),
            best_of=w_cfg.get("best_of", 5),
            patience=w_cfg.get("patience", 1.0),
            length_penalty=w_cfg.get("length_penalty", 1.0),
            temperature=w_cfg.get("temperature", 0.0),
            compression_ratio_threshold=w_cfg.get("compression_ratio_threshold", 2.5),
            log_prob_threshold=w_cfg.get("log_prob_threshold", -1.0),
            no_speech_threshold=w_cfg.get("no_speech_threshold", 0.6),
            condition_on_previous_text=w_cfg.get("condition_on_previous_text", False),
            initial_prompt=w_cfg.get("initial_prompt"),
            prefix=w_cfg.get("prefix"),
            suppress_blank=w_cfg.get("suppress_blank", True),
            suppress_tokens=suppress_tokens_val,
            repetition_penalty=w_cfg.get("repetition_penalty", 1.0),
            no_repeat_ngram_size=w_cfg.get("no_repeat_ngram_size", 0),
            max_new_tokens=w_cfg.get("max_new_tokens"),
            hotwords=w_cfg.get("hotwords"),
            hallucination_silence_threshold=w_cfg.get("hallucination_silence_threshold"),
            vad_filter=vad_cfg.get("vad_filter", True),
            vad_parameters=vad_parameters,
            word_timestamps=w_cfg.get("word_timestamps", False)
        )

        with open(srt_path, "w", encoding="utf-8") as srt_file:
            srt_index = 0
            for i, segment in enumerate(segments, start=1):
                start_time = format_timestamp(segment.start)
                end_time = format_timestamp(segment.end)
                text = segment.text.strip()
                
                if any(keyword in text for keyword in SKIP_KEYWORDS):
                    print(f"[跳过幻觉片段 - 原ID:{i}]: {text}", flush=True) 
                    continue 
                
                srt_index += 1
                print(f"[{srt_index}] {start_time} -> {end_time}: {text}", flush=True)
                
                srt_file.write(f"{srt_index}\n") 
                srt_file.write(f"{start_time} --> {end_time}\n")
                srt_file.write(f"{text}\n\n")

        # 读取文件内容后立即删除
        try:
            with open(srt_path, 'rb') as f:
                srt_content = f.read()
            
            # 删除字幕文件
            os.remove(srt_path)
            print(f"[清理] 已删除字幕文件: {os.path.basename(srt_path)}", flush=True)
            
            return Response(
                content=srt_content,
                media_type="application/x-subrip",
                headers={"Content-Disposition": "attachment; filename=subtitle.srt"}
            )
        except Exception as e:
            print(f"[清理失败] 无法删除字幕文件 {srt_path}: {e}", flush=True)
            # 即使删除失败，也返回文件内容
            return FileResponse(srt_path, media_type="application/x-subrip", filename="subtitle.srt")

    except Exception as e:
        print(f"Error: {e}")
        return JSONResponse(
            status_code=500, 
            content={"error": str(e), "type": "InternalServerError"}
        )
    finally:
        # 清理临时音频文件
        if os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
                print(f"[清理] 已删除临时音频文件: {os.path.basename(temp_audio_path)}", flush=True)
            except Exception as e:
                print(f"[清理失败] 无法删除音频文件 {temp_audio_path}: {e}", flush=True)


@app.post("/transcribe_async")
async def transcribe_async(
    file: UploadFile = File(...),
    config_json: str = Form(...)
):
    """异步转写接口 - 立即返回任务ID"""
    if global_model is None:
        return JSONResponse(
            status_code=500,
            content={"error": "Model is not loaded properly."}
        )
    
    unique_id = str(uuid.uuid4())
    temp_audio_path = os.path.join(TEMP_DIR, f"{unique_id}_{file.filename}")

    try:
        # 保存上传的文件
        with open(temp_audio_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 解析配置
        try:
            cfg = json.loads(config_json)
        except:
            cfg = {}
        
        # 创建异步任务
        task_id = task_manager.create_task(
            task_type="transcribe",
            metadata={
                "filename": file.filename,
                "audio_path": temp_audio_path
            }
        )
        
        # 提交任务到后台执行
        task_manager.submit_task(task_id, _do_transcribe_work, temp_audio_path, cfg)
        
        # 立即返回任务ID
        return JSONResponse(content={
            "status": "accepted",
            "task_id": task_id,
            "message": "任务已提交，请使用 /task/{task_id} 查询进度"
        })
    
    except Exception as e:
        print(f"Error creating async task: {e}")
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
    
    # 读取文件内容
    try:
        with open(srt_path, 'rb') as f:
            content = f.read()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read file: {str(e)}"}
        )
    
    # 下载后立即删除文件
    try:
        os.remove(srt_path)
        print(f"[清理] 已删除字幕文件: {os.path.basename(srt_path)}", flush=True)
    except Exception as e:
        print(f"[清理失败] 无法删除字幕文件 {srt_path}: {e}", flush=True)
    
    return Response(
        content=content,
        media_type="application/x-subrip"
    )


@app.get("/tasks")
async def list_tasks():
    """列出所有任务"""
    tasks = task_manager.list_tasks()
    return JSONResponse(content={"tasks": tasks})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    def monitor_stdin():
        try:
            sys.stdin.read() 
        except Exception:
            pass
        finally:
            print("父进程管道断开，服务自毁...")
            os._exit(0)
    watcher = threading.Thread(target=monitor_stdin, daemon=True)
    watcher.start()
    print(f"服务启动: http://127.0.0.1:{args.port}")
    
    # 配置 uvicorn 日志 - 禁用颜色输出以避免 ANSI 转义码
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["use_colors"] = False
    log_config["formatters"]["access"]["use_colors"] = False
    
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=log_config)
