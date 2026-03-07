"""智慧课堂API客户端"""
import copy
import time
from datetime import datetime
from io import BytesIO
import json
from typing import Generator
import pytz
import requests
from Crypto.Cipher import AES
from Crypto.Util import Padding

from ..models.models import (
    VideoSearchCondition, VideoSummary, VideoInfo, 
    VideoSegmentInfo, VideoSearchResult
)
from ..utils.config_manager import config_manager

TZ_CST = pytz.timezone("Asia/Shanghai")


class SmartclassClient:
    """智慧课堂API客户端"""
    
    def __init__(self, session: requests.Session):
        self.session = session
        self.base_url = "https://njupt.smartclass.cn"
        self.cached_csrk_key = ""
        self.csrk_expiration = time.monotonic()
        self.config = config_manager.get()

    def fetch_domain_config(self):
        """获取并解密域名配置"""
        url = f"{self.base_url}/config.json"
        response = self.session.get(url, timeout=self.config.network_timeout)
        response.raise_for_status()
        
        response.encoding = None
        config = response.json()

        encrypted_domain_config = config["domainConfig"]
        encrypted_domain_config = bytes.fromhex(encrypted_domain_config)
        cipher_key = b"80bdbdbaf7494add99198960d715d41b"
        cipher_iv = b"bdbaf7494add9919"
        cipher = AES.new(cipher_key, AES.MODE_CBC, cipher_iv)
        decrypted_data = Padding.unpad(
            cipher.decrypt(encrypted_domain_config), AES.block_size
        )
        domain_config = json.load(BytesIO(decrypted_data))
        return domain_config

    def get_csrk_key(self) -> str:
        """获取CSRK密钥"""
        if time.monotonic() < self.csrk_expiration:
            return self.cached_csrk_key
        domain_config = self.fetch_domain_config()
        csrk_key = domain_config.get("csrkKey")
        if not csrk_key:
            raise ValueError("CSRK key not found in domain config")
        self.csrk_expiration = time.monotonic() + 1800
        self.cached_csrk_key = csrk_key
        return csrk_key

    def get_csrk_token(self) -> str:
        """生成CSRK令牌"""
        csrk_key = self.get_csrk_key()
        current_time = str(int(datetime.now().timestamp() * 1000))
        csrk_token = "".join(csrk_key[int(digit)] for digit in current_time)
        return csrk_token

    def search_video(self, condition: VideoSearchCondition) -> VideoSearchResult:
        """搜索视频"""
        url = f"{self.base_url}/Webapi/V1/Video/GetMyVideoList"
        params = {
            "csrkToken": self.get_csrk_token(),
            "Sort": condition.sort,
            "Order": condition.order,
            "PageSize": condition.page_size,
            "PageNumber": condition.page_number,
            "StartDate": condition.start_date,
            "EndDate": condition.end_date,
            "TitleKey": condition.title_key,
        }

        response = self.session.get(url, params=params, timeout=self.config.network_timeout)
        response.raise_for_status()
        
        result = response.json()

        is_success = result.get("Success")
        if is_success is None:
            is_success = result.get("success")
        
        if is_success is not None and not is_success:
            msg = result.get("Message") or result.get("message") or "Unknown error"
            raise ValueError(f"Search failed: {msg}")

        value_node = result.get("Value") or result.get("value")
        
        # 处理搜索结果为空的情况
        if value_node is None or value_node == "":
            # 搜索结果为空，返回空列表
            return VideoSearchResult(total_count=0, videos=[])
        
        # 如果 value_node 不是字典，尝试使用 result 本身
        if not isinstance(value_node, dict):
            value_node = result
        
        data_node = None
        if isinstance(value_node, dict):
            data_node = value_node.get("Data") or value_node.get("data") or value_node.get("rows")
        
        # 如果还是找不到数据节点，返回空结果而不是抛出异常
        if data_node is None:
            # 检查是否是列表类型（某些 API 可能直接返回列表）
            if isinstance(value_node, list):
                data_node = value_node
            else:
                # 搜索结果为空或格式不符，返回空列表
                return VideoSearchResult(total_count=0, videos=[])

        data = data_node
        total_count = result.get("TotalCount") or result.get("totalCount") or len(data)

        video_summaries = [
            VideoSummary(
                id=video["NewID"],
                title=video["Title"],
                start_time=TZ_CST.localize(
                    datetime.strptime(video["StartTime"], "%Y-%m-%d %H:%M:%S")
                ),
                stop_time=TZ_CST.localize(
                    datetime.strptime(video["StopTime"], "%Y-%m-%d %H:%M:%S")
                ),
                course_name=video["CourseName"],
                teachers=video["Teachers"],
                classroom_name=video["ClassRoomName"],
                cover_url=video["Cover"],
            )
            for video in data
        ]
        return VideoSearchResult(
            total_count=total_count, videos=video_summaries
        )

    def search_video_all(
        self, condition: VideoSearchCondition
    ) -> Generator[VideoSummary, None, None]:
        """搜索所有视频"""
        yielded_count = 0
        my_condition = copy.copy(condition)
        my_condition.page_number = 1
        while True:
            result = self.search_video(my_condition)
            yield from result.videos
            yielded_count += len(result.videos)
            if yielded_count >= result.total_count or len(result.videos) == 0:
                break
            my_condition.page_number += 1

    def get_video_info_by_id(self, video_id: str) -> VideoInfo:
        """获取视频详情"""
        url = f"{self.base_url}/Video/GetVideoInfoDtoByID"
        params = {"csrkToken": self.get_csrk_token(), "NewId": video_id}
        response = self.session.get(url, params=params, timeout=self.config.network_timeout)
        response.raise_for_status()
        result = response.json()
        if not result["Success"]:
            raise ValueError(f"Get video info failed: {result['Message']}")
        if "Value" not in result:
            raise ValueError("Unexpected response format")
        data = result["Value"]
        segments = [
            VideoSegmentInfo(index_file_uri=segment["IndexFileUri"])
            for segment in data["VideoSegmentInfo"]
        ]
        return VideoInfo(
            id=data["NewID"],
            title=data["Title"],
            start_time=TZ_CST.localize(
                datetime.strptime(data["StartTime"], "%Y-%m-%d %H:%M:%S")
            ),
            stop_time=TZ_CST.localize(
                datetime.strptime(data["StopTime"], "%Y-%m-%d %H:%M:%S")
            ),
            course_name=data["CourseName"],
            segments=segments,
        )

