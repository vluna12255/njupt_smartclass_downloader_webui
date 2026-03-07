from typing import Callable, Optional
import cv2
import numpy as np


def calculate_mode_frame(
    cap: cv2.VideoCapture,
    start_frame: int,
    end_frame: int,
    report_progress: Optional[Callable[[int, int], None]] = None,
) -> np.ndarray:
    frame_count = end_frame - start_frame
    if frame_count <= 0:
        raise ValueError(f"Invalid frame range [{start_frame}, {end_frame})")

    # Set to start frame
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    # Read first frame to get dimensions
    ret, first_frame = cap.read()
    if not ret:
        raise ValueError(f"Cannot read frame {start_frame}")

    if frame_count == 1:
        if report_progress:
            report_progress(1, 1)
        return first_frame

    height, width = first_frame.shape[:2]

    # Initialize Boyer-Moore state
    candidates = first_frame.copy()
    counts = np.ones((height, width), dtype=np.int32)
    matches = np.zeros((height, width), dtype=bool)


    MAX_SAMPLES = 40
    step = max(1, frame_count // MAX_SAMPLES)
    
    # 实际参与计算的帧数
    processed_count = 0 

    for current_idx in range(1, frame_count):
        if current_idx % step == 0:
            # 需要处理这一帧：完整解码
            ret, frame = cap.read()
            if not ret:
                break
            
            # Boyer-Moore 投票逻辑
            np.all(frame == candidates, axis=2, out=matches)
            counts += np.where(matches, 1, -1)

            zero_mask = counts == 0
            candidates[zero_mask] = frame[zero_mask]
            counts[zero_mask] = 1
            
            processed_count += 1
        else:
            # 不需要处理：仅抓取数据流，不解码图像=
            if not cap.grab():
                break

        # Report progress
        
        if report_progress and (current_idx % (step * 5) == 0 or current_idx == frame_count - 1):
            report_progress(current_idx + 1, frame_count)

    return candidates