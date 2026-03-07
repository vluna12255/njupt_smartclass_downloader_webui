"""转写服务"""
import os
import sys
import subprocess
import time
import requests
import json
import shutil

from ..utils.logger import get_logger

logger = get_logger('transcribe')

if getattr(sys, 'frozen', False):
    BASE_PATH = os.path.dirname(sys.executable)
else:
    BASE_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FFMPEG_PATH = os.path.join(BASE_PATH, "bin", "ffmpeg.exe")


class TranscribeService:
    """音频转写服务"""
    
    def __init__(self):
        pass
    
    def convert_video_to_wav(self, input_path, output_path):
        """将视频转换为WAV音频"""
        if not os.path.exists(FFMPEG_PATH):
            raise FileNotFoundError(f"找不到 FFmpeg，路径错误: {FFMPEG_PATH}")
        
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path, "-vn", "-ar", "16000", "-ac", "1", 
            "-c:a", "pcm_s16le", "-af", "highpass=f=200, lowpass=f=3000, dynaudnorm=p=0.9", 
            output_path
        ]
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        result = subprocess.run(cmd, capture_output=True, startupinfo=startupinfo)
        if result.returncode != 0:
            err_log = result.stderr.decode('utf-8', errors='ignore')
            logger.error(f"FFmpeg音频转换失败: {err_log[-200:]}")
            raise Exception(f"FFmpeg音频转换失败: {err_log[-200:]}")
    
    def check_service_health(self, url):
        """检查服务可用性"""
        try:
            requests.get(url, timeout=5)
            return True
        except:
            return False
    
    def call_whisper_api(self, url: str, video_path: str, output_dir: str):
        if not url:
            raise ValueError("Whisper 服务器地址未设置")
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"未找到文件: {video_path}")
        file_dir, file_name = os.path.split(video_path)
        file_ext = os.path.splitext(file_name)[1]
        temp_filename = "upload_temp" + file_ext
        temp_path = os.path.join(file_dir, temp_filename)
        
        shutil.copy(video_path, temp_path)
        logger.debug(f"[Whisper] 准备上传文件: {temp_path}")

        api_url = url.strip().rstrip("/") + "/transcribe"
        os.makedirs(output_dir, exist_ok=True)
        target_srt_path = os.path.join(output_dir, "subtitle.srt")

        config_payload = {
            "whisper": {
                "model_size": "large-v3",
                "lang": "chinese",
                "is_translate": False,
                "beam_size": 10,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "compute_type": "float16",
                "best_of": 5,
                "patience": 1.0,
                "condition_on_previous_text": False,
                "prompt_reset_on_temperature": 0.4, 
                "initial_prompt": "今天",
                "temperature": 0.0,
                "compression_ratio_threshold": 2.5,
                "length_penalty": 1.0,
                "repetition_penalty": 1.2, 
                "no_repeat_ngram_size": 0, 
                "prefix": None,
                "suppress_blank": True,
                "suppress_tokens": "[-1]",
                "max_initial_timestamp": 1.0,
                "word_timestamps": False,
                "prepend_punctuations": "\"'“¿([{-",
                "append_punctuations": "\"'.。,，!！?？:：”)]}、", 
                "max_new_tokens": None, 
                "chunk_length": 30, 
                "hallucination_silence_threshold": 3.0, 
                "hotwords": "Hello!!", 
                "batch_size": 24 
            },
            "vad": {
                "vad_filter": True,
                "threshold": 0.3,
                "min_speech_duration_ms": 250,
                "max_speech_duration_s": 9999,
                "min_silence_duration_ms": 1000,
                "speech_pad_ms": 2000
            }
        }

        try:
            logger.debug("[Whisper] 发送请求到 Faster-Whisper...")
            
            with open(temp_path, 'rb') as f:
                files = {
                    'file': (temp_filename, f, 'application/octet-stream')
                }
                data = {
                    'config_json': json.dumps(config_payload)
                }

                response = requests.post(api_url, files=files, data=data, timeout=3600)

            if response.status_code == 200:
                content = response.content
                try:
                    error_data = json.loads(content.decode('utf-8'))
                    if 'error' in error_data:
                        error_msg = error_data.get('error', '未知错误')
                        raise Exception(f"Whisper 服务错误: {error_msg}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
                
                content_str = content.decode('utf-8', errors='ignore').strip()
                if not content_str:
                    raise Exception("服务器返回空内容")
                
                if not any(line.strip().isdigit() for line in content_str.split('\n')[:10]):
                    raise Exception(f"返回内容不是有效的 SRT 格式: {content_str[:200]}")
                
                with open(target_srt_path, 'wb') as f:
                    f.write(content)
                logger.debug(f"[Whisper] 字幕已保存: {target_srt_path}")
                return target_srt_path
            else:
                raise Exception(f"服务器返回错误: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"[Whisper] 详细错误: {e}")
            raise Exception(f"Whisper调用失败: {str(e)}")
        
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    def call_funasr_api(self, url: str, video_path: str, output_dir: str):
        if not url:
            raise ValueError("FunASR 服务器地址未设置")
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"未找到文件: {video_path}")

        file_dir, file_name = os.path.split(video_path)
        file_ext = os.path.splitext(file_name)[1]
        temp_filename = "upload_temp" + file_ext
        temp_path = os.path.join(file_dir, temp_filename)
        
        shutil.copy(video_path, temp_path)
        logger.debug(f"[FunASR] 准备上传文件: {temp_path}")

        api_url = url.strip().rstrip("/") + "/transcribe"
        os.makedirs(output_dir, exist_ok=True)
        target_srt_path = os.path.join(output_dir, "subtitle.srt")
        prompt = "今天"
        config_payload = {
            "funasr": {
                "punc_model": "ct-punc",  
                "hotwords": prompt,       
                "use_itn": True,          
                "fs": 16000, 
                "audio_format": "wav",
                "sv_threshold": 0.3 
            },
            "vad": {
                "vad_filter": True
            }
        }

        try:
            logger.debug("[FunASR] 发送请求到 funasr...")
            
            with open(temp_path, 'rb') as f:
                files = {
                    'file': (temp_filename, f, 'application/octet-stream')
                }
                data = {
                    'config_json': json.dumps(config_payload)
                }
                
                response = requests.post(api_url, files=files, data=data, timeout=3600)

            if response.status_code == 200:
                # 检查返回内容是否是错误信息（JSON格式）
                content = response.content
                try:
                    # 尝试解析为 JSON，如果成功说明是错误响应
                    error_data = json.loads(content.decode('utf-8'))
                    if 'error' in error_data:
                        error_msg = error_data.get('error', '未知错误')
                        raise Exception(f"FunASR 服务错误: {error_msg}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 不是 JSON，说明是正常的 SRT 内容
                    pass
                
                # 验证内容不为空且看起来像 SRT 格式
                content_str = content.decode('utf-8', errors='ignore').strip()
                if not content_str:
                    raise Exception("服务器返回空内容")
                
                # 简单验证 SRT 格式（应该包含数字序号）
                if not any(line.strip().isdigit() for line in content_str.split('\n')[:10]):
                    raise Exception(f"返回内容不是有效的 SRT 格式: {content_str[:200]}")
                
                with open(target_srt_path, 'wb') as f:
                    f.write(content)
                logger.debug(f"[FunASR] 字幕已保存: {target_srt_path}")
                return target_srt_path
            else:
                raise Exception(f"服务器返回错误: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"[FunASR] 详细错误: {e}")
            raise Exception(f"funasr调用失败: {str(e)}")
        
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

