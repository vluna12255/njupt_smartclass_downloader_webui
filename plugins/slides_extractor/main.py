from typing import Callable, Optional
import cv2
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys
import argparse
import threading


from mode_frame import calculate_mode_frame
from pdf_compositor import make_pdf
from significant_frame import (
    find_all_significant_frame,
)
from taskbar_detector import (
    filter_fullscreen_segments,
)

def _process_segment_safe(
    video_path: str, 
    start_frame: int, 
    end_frame: int, 
    index: int,
    progress_callback: Optional[Callable[[int], None]] = None
):

    local_cap = cv2.VideoCapture(video_path)
    try:
        if not local_cap.isOpened():
            raise ValueError(f"Thread cannot open video: {video_path}")
        
        # 这里的 callback 用于更新全局计数器
        mode_frame = calculate_mode_frame(
            local_cap,
            start_frame,
            end_frame,
            lambda current, _: progress_callback(current) if progress_callback else None
        )
        return index, mode_frame
    finally:
        local_cap.release()

def extract_slides(
    video_input: str,
    pdf_output: str,
    threshold: float = 0.02,
    min_time_gap: float = 3,
    report_progress: Optional[Callable[[str, int, int], None]] = None,
    max_workers: int = 4, 
):
    cap = None
    try:
        cap = cv2.VideoCapture(video_input)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_input}")
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
 
        all_segments = find_all_significant_frame(
            cap,
            threshold,
            int(min_time_gap * fps),
            lambda current, total: (
                report_progress(f"Analyzing", current, total)
                if report_progress
                else None
            ),
        )

        if report_progress:
            report_progress("Filtering", 0, len(all_segments))
        fullscreen_segments = filter_fullscreen_segments(cap, all_segments)
        if report_progress:
            report_progress("Filtering", len(all_segments), len(all_segments))

        cap.release() 
        cap = None 
        
        n_mode_frame_to_calculate = sum(
            end_frame - start_frame for start_frame, end_frame in fullscreen_segments
        )

        processed_frames_lock = threading.Lock()
        global_processed_frames = 0
        
        def update_progress(increment):
            nonlocal global_processed_frames
            with processed_frames_lock:
                global_processed_frames += increment
                if report_progress:
                    report_progress(
                        "Compositing",
                        global_processed_frames,
                        n_mode_frame_to_calculate,
                    )

        unsorted_slides = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_segment = {}
            
            for i, (start_frame, end_frame) in enumerate(fullscreen_segments):
                future = executor.submit(
                    _process_segment_safe,
                    video_input,
                    start_frame,
                    end_frame,
                    i,
                    update_progress
                )
                future_to_segment[future] = i

            for future in as_completed(future_to_segment):
                try:
                    idx, mode_frame = future.result()

                    original_segment = fullscreen_segments[idx]
                    start_f, end_f = original_segment
                    slide_name = f"Slide {idx+1} (frames {start_f}-{end_f - 1})"
                    
                    unsorted_slides.append((idx, slide_name, mode_frame))
                except Exception as exc:
                    print(f"Segment processing generated an exception: {exc}")
                    raise exc

        unsorted_slides.sort(key=lambda x: x[0])
        slides = [(name, img) for _, name, img in unsorted_slides]

        if report_progress:
            report_progress("Saving", 0, len(slides))
        
        make_pdf(slides, pdf_output, video_width, video_height)
        
        if report_progress:
            report_progress("Saving", len(slides), len(slides))

        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1

    finally:
        if cap and cap.isOpened():
            cap.release()

app = FastAPI()

class ExtractRequest(BaseModel):
    video_path: str
    output_path: str
    threshold: float = 0.02
    min_time_gap: float = 3.0
    max_workers: int = 4

@app.get("/docs")
async def health_check():
    return {"status": "running"}

@app.post("/extract_slides")
async def extract_slides_endpoint(request: ExtractRequest):

    if not os.path.exists(request.video_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {request.video_path}")

    os.makedirs(os.path.dirname(request.output_path), exist_ok=True)

    print(f"--- 开始处理 PPT 提取 ---")
    print(f"输入: {request.video_path}")
    print(f"输出: {request.output_path}")

    try:
        def progress_callback(stage, current, total):
            percent = (current / total * 100) if total > 0 else 0
            print(f"[Progress] {stage}: {current}/{total} ({percent:.1f}%)", flush=True)

        extract_slides(
            video_input=request.video_path,
            pdf_output=request.output_path,
            threshold=request.threshold,
            min_time_gap=request.min_time_gap,
            report_progress=progress_callback,
            max_workers=request.max_workers
        )

        if not os.path.exists(request.output_path):
             raise Exception("Output PDF was not generated.")

        return {"status": "success", "output": request.output_path}

    except Exception as e:
        print(f"Error during extraction: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
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
    uvicorn.run(app, host="127.0.0.1", port=args.port)