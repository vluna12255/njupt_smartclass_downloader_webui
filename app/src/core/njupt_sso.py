"""南邮统一身份认证客户端"""
import datetime
import urllib.parse
import requests
from Crypto.Cipher import AES
from Crypto.Util import Padding

from ..utils.logger import get_logger

logger = get_logger('njupt_sso')


class NjuptSsoException(Exception):
    """SSO登录异常"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


class NjuptSso:
    """南邮统一身份认证客户端"""
    
    def __init__(self, session: requests.Session):
        self.session = session
        self.base_url = "https://i.njupt.edu.cn"

    def login(self, username: str, password: str) -> None:
        """执行SSO登录"""
        checkKey = str(int(datetime.datetime.now().timestamp() * 1000))

        url = f"{self.base_url}/ssoLogin/login"
        data = {
            "username": self._encrypt(username, checkKey),
            "password": self._encrypt(password, checkKey),
            "captchaVerification": None,
            "checkKey": checkKey,
            "appId": "common",
            "mode": "none",
        }

        response = self.session.post(url, json=data).json()
        if not response["success"]:
            raise NjuptSsoException(response["code"], response["message"])

    def grant_service(self, service: str) -> None:
        """授权访问指定服务"""
        url = f"{self.base_url}/cas/login?service={urllib.parse.quote(service)}"
        response = self.session.get(url)
        if not response.ok:
            raise Exception(
                f"Failed to grant service '{service}', code: {response.status_code}"
            )

    @staticmethod
    def _encrypt(data: str, key: str) -> str:
        """AES加密"""
        cipher_key = b"iam" + key.encode()
        cipher_iv = cipher_key
        cipher = AES.new(cipher_key, AES.MODE_CBC, cipher_iv)
        return cipher.encrypt(Padding.pad(data.encode(), AES.block_size)).hex()
