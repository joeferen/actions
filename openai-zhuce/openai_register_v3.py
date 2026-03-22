"""
OpenAI 自动注册脚本 V3 - 优化版
基于 V2，整合 codex_register.py 的关键功能：
1. 真实 Sentinel PoW 计算
2. 新注册后重新登录获取 token
3. 随机姓名/生日生成
4. 已注册账号识别处理
5. OTP 定时重发策略
"""
import json
import os
import re
import sys
import time
import random
import secrets
import hashlib
import base64
import argparse
import threading
import logging
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, urljoin
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests

# 尝试导入 sentinel_pow 模块
try:
    from sentinel_pow import build_sentinel_pow_token, SentinelPOWError
    HAS_SENTINEL_POW = True
except ImportError:
    HAS_SENTINEL_POW = False
    print("[Warn] sentinel_pow 模块未安装，将使用空 PoW")

# ==========================================
# 日志配置
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "openai_register_v3.log")

def _setup_logger():
    logger = logging.getLogger("reg_v3")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger

log = _setup_logger()

# ==========================================
# 配置
# ==========================================

MAILFREE_BASE = "https://mailfree.smanx.xx.kg"
JWT_TOKEN = "auto"
DEFAULT_DOMAIN_INDEX = 0

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SENTINEL_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "email_accounts_v3.txt")
TOKENS_DIR = SCRIPT_DIR  # 保存到当前目录，兼容 codex_maintenance.js

# 超时与重试配置
MAIL_POLL_TIMEOUT = 180
OTP_RESEND_INTERVAL = 25
MAX_RETRY_PER_ACCOUNT = 3

# ==========================================
# 随机姓名/生日数据 (来自 codex_register.py)
# ==========================================
_GIVEN_NAMES = [
    "Liam", "Noah", "Oliver", "James", "Elijah", "William", "Henry", "Lucas",
    "Benjamin", "Theodore", "Jack", "Levi", "Alexander", "Mason", "Ethan",
    "Daniel", "Jacob", "Michael", "Logan", "Jackson", "Sebastian", "Aiden",
    "Owen", "Samuel", "Ryan", "Nathan", "Carter", "Luke", "Jayden", "Dylan",
    "Caleb", "Isaac", "Connor", "Adrian", "Hunter", "Eli", "Thomas", "Aaron",
    "Olivia", "Emma", "Charlotte", "Amelia", "Sophia", "Isabella", "Mia",
    "Evelyn", "Harper", "Luna", "Camila", "Sofia", "Scarlett", "Elizabeth",
    "Eleanor", "Emily", "Chloe", "Mila", "Avery", "Riley", "Aria", "Layla",
    "Nora", "Lily", "Hannah", "Hazel", "Zoey", "Stella", "Aurora", "Natalie",
    "Emilia", "Zoe", "Lucy", "Lillian", "Addison", "Willow", "Ivy", "Violet",
]

_FAMILY_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Hill", "Scott", "Green",
    "Adams", "Baker", "Nelson", "Carter", "Mitchell", "Roberts", "Turner",
    "Phillips", "Campbell", "Parker", "Evans", "Edwards", "Collins", "Stewart",
    "Morris", "Murphy", "Cook", "Rogers", "Morgan", "Cooper", "Peterson",
    "Reed", "Bailey", "Kelly", "Howard", "Ward", "Watson", "Brooks", "Bennett",
    "Gray", "Price", "Hughes", "Sanders", "Long", "Foster", "Powell", "Perry",
    "Russell", "Sullivan", "Bell", "Coleman", "Butler", "Henderson", "Barnes",
]

# 浏览器指纹列表 (来自 codex_register.py)
_BROWSER_PROFILES = [
    'edge99', 'edge101', 'chrome99', 'chrome100', 'chrome101', 'chrome104',
    'chrome107', 'chrome110', 'chrome116', 'chrome119', 'chrome120', 'chrome123',
    'chrome124', 'chrome131', 'chrome133a', 'chrome136', 'chrome142',
    'safari153', 'safari155', 'safari170', 'safari180', 'safari184',
    'safari260', 'safari2601', 'firefox133', 'firefox135', 'firefox144',
    'safari15_3', 'safari15_5', 'safari17_0', 'safari17_2_ios', 'safari18_0',
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en,en-US;q=0.9,en-GB;q=0.8",
    "zh-CN,zh;q=0.9",
    "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "es-ES,es;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
]


def random_name() -> str:
    """生成随机英文姓名"""
    return f"{random.choice(_GIVEN_NAMES)} {random.choice(_FAMILY_NAMES)}"


def random_birthday() -> str:
    """生成随机生日（18~40岁之间）"""
    y = random.randint(1986, 2006)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


def pick_browser_profile() -> tuple:
    """随机选择浏览器指纹"""
    profile = random.choice(_BROWSER_PROFILES)
    lang = random.choice(_ACCEPT_LANGUAGES)
    return profile, lang


# ==========================================
# MailFree 邮箱 API
# ==========================================


def _mailfree_headers() -> Dict[str, Any]:
    """构建请求头"""
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Admin-Token": JWT_TOKEN,
    }


def get_domains(proxies: Any = None) -> List[str]:
    """获取可用域名列表"""
    resp = requests.get(
        f"{MAILFREE_BASE}/api/domains",
        headers=_mailfree_headers(),
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"获取域名失败，状态码: {resp.status_code}")
    return resp.json()


def create_email(local: str, domain_index: int = 0, proxies: Any = None) -> Dict[str, Any]:
    """创建自定义邮箱"""
    resp = requests.post(
        f"{MAILFREE_BASE}/api/create",
        headers=_mailfree_headers(),
        json={"local": local, "domainIndex": domain_index},
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"创建邮箱失败: {resp.status_code}: {resp.text}")
    return resp.json()


def get_emails(mailbox: str, limit: int = 20, proxies: Any = None) -> List[Dict[str, Any]]:
    """获取邮件列表"""
    resp = requests.get(
        f"{MAILFREE_BASE}/api/emails",
        headers=_mailfree_headers(),
        params={"mailbox": mailbox, "limit": limit},
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        return []
    return resp.json()


def get_email_detail(email_id: int, proxies: Any = None) -> Dict[str, Any]:
    """获取邮件详情"""
    resp = requests.get(
        f"{MAILFREE_BASE}/api/email/{email_id}",
        headers=_mailfree_headers(),
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


def delete_mailbox(address: str, proxies: Any = None) -> bool:
    """删除邮箱"""
    resp = requests.delete(
        f"{MAILFREE_BASE}/api/mailboxes",
        headers=_mailfree_headers(),
        params={"address": address},
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    return resp.status_code == 200


def reset_mailbox_password(address: str, new_password: str, proxies: Any = None) -> bool:
    """设置邮箱密码"""
    resp = requests.post(
        f"{MAILFREE_BASE}/api/mailboxes/change-password",
        headers=_mailfree_headers(),
        json={"address": address, "new_password": new_password},
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    return resp.status_code == 200


# ==========================================
# 验证码轮询 (优化版：支持重发)
# ==========================================


def poll_verification_code(
    email: str,
    proxies: Any = None,
    timeout: int = MAIL_POLL_TIMEOUT,
    used_codes: Optional[set] = None,
    resend_fn: Optional[Callable] = None,
    otp_sent_at: Optional[float] = None,
) -> str:
    """轮询获取 OpenAI 6 位验证码，支持定时重发"""
    regex = r"(?<!\d)(\d{6})(?!\d)"
    used = used_codes or set()
    seen_ids: set = set()
    start = time.time()
    last_resend = 0.0
    intervals = [3, 4, 5, 6, 8, 10]
    idx = 0

    log.info(f"    📧 等待验证码 ({email})...")

    while time.time() - start < timeout:
        try:
            resp = requests.get(
                f"{MAILFREE_BASE}/api/emails",
                headers=_mailfree_headers(),
                params={"mailbox": email, "limit": 10},
                proxies=proxies,
                impersonate="chrome",
                timeout=15,
            )

            if resp.status_code == 200:
                emails_data = resp.json()
                if isinstance(emails_data, list):
                    for mail in emails_data:
                        msg_id = mail.get("id")
                        if not msg_id or msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)

                        sender = str(mail.get("sender") or "").lower()
                        subject = str(mail.get("subject") or "")
                        preview = str(mail.get("preview") or "")
                        verification_code = mail.get("verification_code")

                        # 检查是否来自 OpenAI
                        if "openai" not in sender and "openai" not in subject.lower():
                            # 获取详情进一步检查
                            detail = get_email_detail(msg_id, proxies)
                            content = "\n".join([
                                subject, preview,
                                str(detail.get("content") or ""),
                                str(detail.get("html_content") or ""),
                            ])
                            if "openai" not in content.lower():
                                continue
                            m = re.search(regex, content)
                        else:
                            if verification_code:
                                m = re.match(regex, str(verification_code))
                            else:
                                m = re.search(regex, preview)

                        if m:
                            code = m.group(1)
                            if code not in used:
                                used.add(code)
                                elapsed = int(time.time() - start)
                                log.info(f"    ✅ 验证码: {code} (耗时 {elapsed}s)")
                                return code

        except Exception as e:
            log.warning(f"    MailAPI 查询失败: {e}")

        # 定时重发 OTP
        elapsed_now = time.time() - start
        if resend_fn and elapsed_now > 20 and (elapsed_now - last_resend) > OTP_RESEND_INTERVAL:
            try:
                resend_fn()
                last_resend = elapsed_now
                log.info("    🔄 已重发 OTP")
            except Exception:
                pass

        wait = intervals[min(idx, len(intervals) - 1)]
        idx += 1
        time.sleep(wait)

    raise TimeoutError(f"验证码超时 ({timeout}s)")


# ==========================================
# OAuth 工具
# ==========================================


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    return _decode_jwt_segment(payload_b64)


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _generate_password(length: int = 16) -> str:
    """生成符合 OpenAI 要求的随机强密码"""
    import string
    upper = random.choices(string.ascii_uppercase, k=2)
    lower = random.choices(string.ascii_lowercase, k=2)
    digits = random.choices(string.digits, k=2)
    specials = random.choices("!@#$%&*", k=2)
    rest_len = length - 8
    pool = string.ascii_letters + string.digits + "!@#$%&*"
    rest = random.choices(pool, k=rest_len)
    chars = upper + lower + digits + specials + rest
    random.shuffle(chars)
    return "".join(chars)


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    """从回调 URL 兑换 token"""
    # 解析回调 URL
    parsed = urlparse(callback_url)
    query = parse_qs(parsed.query)
    
    code = (query.get("code") or [""])[0]
    returned_state = (query.get("state") or [""])[0]
    error = (query.get("error") or [""])[0]
    error_desc = (query.get("error_description") or [""])[0]

    if error:
        raise RuntimeError(f"OAuth error: {error}: {error_desc}")
    if not code:
        raise ValueError("callback url missing ?code=")
    if returned_state != expected_state:
        raise ValueError("state mismatch")

    # 兑换 token
    token_data = urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    })
    
    resp = requests.post(
        TOKEN_URL,
        data=token_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        impersonate="chrome",
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Token 兑换失败: {resp.status_code}: {resp.text}")

    token_resp = resp.json()
    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# Sentinel PoW (功能 1)
# ==========================================


def build_sentinel_token(device_id: str, user_agent: str, flow: str = "authorize_continue") -> str:
    """构建 Sentinel token，优先使用真实 PoW"""
    if HAS_SENTINEL_POW:
        try:
            pow_token = build_sentinel_pow_token(user_agent)
            log.info(f"      PoW token 已生成")
            body = json.dumps({
                "p": pow_token,
                "id": device_id,
                "flow": flow,
            }, separators=(",", ":"))
        except SentinelPOWError as e:
            log.warning(f"PoW 求解失败，使用空 PoW: {e}")
            body = f'{{"p":"","id":"{device_id}","flow":"{flow}"}}'
    else:
        body = f'{{"p":"","id":"{device_id}","flow":"{flow}"}}'

    return body


def get_sentinel_header(device_id: str, user_agent: str, flow: str = "authorize_continue", proxies: Any = None) -> str:
    """获取完整的 Sentinel header"""
    req_body = build_sentinel_token(device_id, user_agent, flow)
    
    resp = requests.post(
        SENTINEL_URL,
        data=req_body,
        headers={
            "Origin": "https://sentinel.openai.com",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
            "Content-Type": "text/plain;charset=UTF-8",
        },
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )

    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"Sentinel 失败: {resp.status_code} {resp.text[:200]}")

    token = resp.json()["token"]
    header = json.dumps({
        "p": "", "t": "", "c": token,
        "id": device_id, "flow": flow,
    })
    return header


# ==========================================
# HTTP 会话类 (支持随机指纹)
# ==========================================


class RegSession:
    """注册会话，支持随机浏览器指纹"""

    def __init__(self, proxies: Any = None):
        self.profile, self.lang = pick_browser_profile()
        self.proxies = proxies
        self._session = requests.Session(
            proxies=proxies,
            impersonate=self.profile,
        )
        self._session.headers.update({
            "Accept-Language": self.lang,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        log.info(f"    🎭 浏览器指纹: {self.profile}")

    def get(self, url: str, **kwargs) -> Any:
        return self._session.get(url, timeout=30, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        return self._session.post(url, timeout=30, **kwargs)

    def post_json(self, url: str, data: dict, headers: Optional[dict] = None) -> Any:
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        return self._session.post(url, data=json.dumps(data), headers=hdrs, timeout=30)

    def get_cookie(self, name: str) -> Optional[str]:
        return self._session.cookies.get(name)

    def follow_redirects(self, url: str, max_hops: int = 12) -> Optional[str]:
        """跟随重定向链，返回包含 code 的回调 URL"""
        current = url
        for _ in range(max_hops):
            resp = self._session.get(current, allow_redirects=False, timeout=30)
            location = resp.headers.get("Location")
            if not location:
                return None
            if "code=" in location:
                return location
            current = urljoin(current, location)
        return None

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ==========================================
# 核心注册逻辑
# ==========================================


def register_account(
    email: str,
    openai_password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    """
    核心注册逻辑
    返回包含 token 信息的字典
    """
    codes = used_codes or set()

    with RegSession(proxies) as s:
        # --- 1. 发起 OAuth ---
        oauth = generate_oauth_url()
        log.info(f"  [1] 发起 OAuth...")
        resp = s.get(oauth.auth_url)
        log.info(f"      状态: {resp.status_code}")

        device_id = s.get_cookie("oai-did") or ""
        if device_id:
            log.info(f"      设备ID: {device_id[:16]}...")

        time.sleep(random.uniform(0.8, 2.0))

        # --- 2. 获取 Sentinel (功能 1: 真实 PoW) ---
        log.info(f"  [2] 求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"      Sentinel token OK")

        time.sleep(random.uniform(0.5, 1.5))

        # --- 3. 提交邮箱 ---
        log.info(f"  [3] 提交邮箱: {email}")
        signup_resp = s.post_json(
            "https://auth.openai.com/api/accounts/authorize/continue",
            {"username": {"value": email, "kind": "email"}, "screen_hint": "signup"},
            headers={
                "Referer": "https://auth.openai.com/create-account",
                "openai-sentinel-token": sentinel,
            },
        )
        if signup_resp.status_code < 200 or signup_resp.status_code >= 300:
            raise RuntimeError(f"提交邮箱失败: {signup_resp.status_code} {signup_resp.text[:300]}")

        # 解析响应判断账号状态 (功能 4: 已注册账号识别)
        try:
            step3_data = signup_resp.json()
            page_type = step3_data.get("page", {}).get("type", "")
        except Exception:
            step3_data = {}
            page_type = ""

        log.info(f"      页面类型: {page_type}")

        # 已注册账号判断
        is_existing_account = (page_type == "email_otp_verification")

        time.sleep(random.uniform(0.5, 1.5))

        name = ""

        if is_existing_account:
            # 已注册账号：OTP 已自动发送
            log.info(f"  [4] 检测到已注册账号，OTP 已自动发送")
            otp_sent_at = time.time()
        else:
            # --- 4. 设置密码 ---
            log.info(f"  [4] 设置密码...")
            pwd_resp = s.post_json(
                "https://auth.openai.com/api/accounts/user/register",
                {"password": openai_password, "username": email},
                headers={
                    "Referer": "https://auth.openai.com/create-account/password",
                    "openai-sentinel-token": sentinel,
                },
            )
            if pwd_resp.status_code < 200 or pwd_resp.status_code >= 300:
                raise RuntimeError(f"设置密码失败: {pwd_resp.status_code} {pwd_resp.text[:300]}")
            log.info(f"      密码已设置")

            # --- 5. 发送 OTP ---
            time.sleep(random.uniform(0.5, 1.0))
            otp_sent_at = time.time()
            log.info(f"  [5] 发送 OTP...")
            otp_resp = s.post_json(
                "https://auth.openai.com/api/accounts/email-otp/send",
                {},
                headers={"Referer": "https://auth.openai.com/create-account/password"},
            )
            if otp_resp.status_code < 200 or otp_resp.status_code >= 300:
                raise RuntimeError(f"发送 OTP 失败: {otp_resp.status_code} {otp_resp.text[:300]}")
            log.info(f"      验证码已发送")

        # --- 6. 获取并验证 OTP (功能 5: 支持重发) ---
        def _resend():
            r = s.post_json(
                "https://auth.openai.com/api/accounts/email-otp/resend",
                {},
                headers={"Referer": "https://auth.openai.com/email-verification"},
            )
            return r.status_code >= 200 and r.status_code < 300

        code = poll_verification_code(
            email, proxies,
            used_codes=codes,
            resend_fn=_resend,
            otp_sent_at=otp_sent_at,
        )

        time.sleep(random.uniform(0.3, 1.0))

        # --- 7. 验证 OTP ---
        log.info(f"  [7] 验证 OTP: {code}")
        # 获取 email_otp_validate 的 sentinel
        otp_sentinel = get_sentinel_header(device_id, ua, "email_otp_validate", proxies)
        verify_resp = s.post_json(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            {"code": code},
            headers={
                "Referer": "https://auth.openai.com/email-verification",
                "openai-sentinel-token": otp_sentinel,
            },
        )
        if verify_resp.status_code < 200 or verify_resp.status_code >= 300:
            raise RuntimeError(f"OTP 验证失败: {verify_resp.status_code} {verify_resp.text[:300]}")
        log.info(f"      OK")

        time.sleep(random.uniform(0.5, 1.5))

        # --- 8. 创建账号 (功能 3: 随机姓名/生日) ---
        if is_existing_account:
            log.info(f"  [8] 跳过创建账号（已存在）")
        else:
            name = random_name()
            birthday = random_birthday()
            log.info(f"  [8] 创建账号: {name}, {birthday}")
            create_resp = s.post_json(
                "https://auth.openai.com/api/accounts/create_account",
                {"name": name, "birthdate": birthday},
                headers={"Referer": "https://auth.openai.com/about-you"},
            )
            if create_resp.status_code < 200 or create_resp.status_code >= 300:
                raise RuntimeError(f"创建账号失败: {create_resp.status_code} {create_resp.text[:300]}")
            log.info(f"      OK")

            # 检查是否需要手机验证
            try:
                create_data = create_resp.json()
                create_page_type = create_data.get("page", {}).get("type", "")
            except Exception:
                create_page_type = ""

            if create_page_type == "add_phone":
                log.info(f"      需要手机验证，尝试密码登录绕过...")
                return _login_for_token(s, oauth, email, openai_password, device_id, ua, codes, proxies)

        # 新注册账号需要重新登录 (功能 2)
        needs_relogin = not is_existing_account

        if not needs_relogin:
            # 已注册账号，直接完成 token 获取
            return _complete_token_exchange(s, oauth, email, name, proxies)

    # --- 新注册账号：重新发起登录流程 ---
    log.info(f"  [8.5] 注册完成，重新发起登录流程...")
    time.sleep(random.uniform(1.0, 2.0))

    return _relogin_for_token(email, openai_password, proxies, codes)


def _relogin_for_token(
    email: str,
    password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    """重新登录获取 token (功能 2)"""
    codes = used_codes or set()

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()
        log.info(f"  [8.5a] 重新发起 OAuth (登录)...")
        resp = s.get(oauth.auth_url)
        log.info(f"         状态: {resp.status_code}")

        device_id = s.get_cookie("oai-did") or ""
        if device_id:
            log.info(f"         设备ID: {device_id[:16]}...")

        time.sleep(random.uniform(0.8, 2.0))

        # Sentinel
        log.info(f"  [8.5b] 重新求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"         Sentinel token OK")

        time.sleep(random.uniform(0.5, 1.5))

        # 提交邮箱
        log.info(f"  [8.5c] 提交邮箱 (登录): {email}")
        login_resp = s.post_json(
            "https://auth.openai.com/api/accounts/authorize/continue",
            {"username": {"value": email, "kind": "email"}, "screen_hint": "login"},
            headers={
                "Referer": "https://auth.openai.com/log-in",
                "openai-sentinel-token": sentinel,
            },
        )
        if login_resp.status_code < 200 or login_resp.status_code >= 300:
            raise RuntimeError(f"重新登录提交邮箱失败: {login_resp.status_code}")

        try:
            login_page_type = login_resp.json().get("page", {}).get("type", "")
        except Exception:
            login_page_type = ""

        log.info(f"         页面类型: {login_page_type}")

        if login_page_type != "login_password":
            raise RuntimeError(f"重新登录未进入密码页面: {login_page_type}")

        time.sleep(random.uniform(0.5, 1.5))

        # 提交密码
        pwd_sentinel = get_sentinel_header(device_id, ua, "password_verify", proxies)
        log.info(f"  [8.5d] 提交登录密码...")
        pwd_resp = s.post_json(
            "https://auth.openai.com/api/accounts/password/verify",
            {"password": password},
            headers={
                "Referer": "https://auth.openai.com/log-in/password",
                "openai-sentinel-token": pwd_sentinel,
            },
        )
        if pwd_resp.status_code < 200 or pwd_resp.status_code >= 300:
            raise RuntimeError(f"重新登录提交密码失败: {pwd_resp.status_code}")

        try:
            pwd_page_type = pwd_resp.json().get("page", {}).get("type", "")
        except Exception:
            pwd_page_type = ""

        log.info(f"         页面类型: {pwd_page_type}")

        if pwd_page_type != "email_otp_verification":
            raise RuntimeError(f"重新登录未进入验证码页面: {pwd_page_type}")

        otp_sent_at = time.time()
        log.info(f"         密码校验通过，等待验证码...")

        time.sleep(random.uniform(0.5, 1.5))

        # 获取并验证登录 OTP
        def _resend():
            r = s.post_json(
                "https://auth.openai.com/api/accounts/email-otp/resend",
                {},
                headers={"Referer": "https://auth.openai.com/email-verification"},
            )
            return r.status_code >= 200 and r.status_code < 300

        code = poll_verification_code(
            email, proxies,
            used_codes=codes,
            resend_fn=_resend,
            otp_sent_at=otp_sent_at,
        )

        log.info(f"  [8.5f] 验证登录 OTP: {code}")
        otp_sentinel = get_sentinel_header(device_id, ua, "email_otp_validate", proxies)
        verify_resp = s.post_json(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            {"code": code},
            headers={
                "Referer": "https://auth.openai.com/email-verification",
                "openai-sentinel-token": otp_sentinel,
            },
        )
        if verify_resp.status_code < 200 or verify_resp.status_code >= 300:
            raise RuntimeError(f"重新登录 OTP 验证失败: {verify_resp.status_code}")

        log.info(f"         OK")

        time.sleep(random.uniform(0.5, 1.5))

        return _complete_token_exchange(s, oauth, email, "", proxies)


def _login_for_token(
    s: "RegSession",
    oauth: OAuthStart,
    email: str,
    password: str,
    device_id: str,
    ua: str,
    used_codes: Optional[set] = None,
    proxies: Any = None,
) -> dict:
    """通过账号密码登录获取 token（绕过手机验证），复用现有会话保持指纹一致"""
    codes = used_codes or set()
    log.info("[*] ===== 尝试密码登录获取 token =====")

    # 复用传入的会话，保持指纹一致
    log.info("[*] 使用当前会话初始化登录...")
    sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)

    # 提交用户名
    log.info("[*] 提交用户名...")
    login_resp = s.post_json(
        "https://auth.openai.com/api/accounts/authorize/continue",
        {"username": {"value": email, "kind": "email"}},
        headers={
            "Referer": "https://auth.openai.com/log-in",
            "openai-sentinel-token": sentinel,
        },
    )

    if login_resp.status_code < 200 or login_resp.status_code >= 300:
        raise RuntimeError(f"用户名提交失败: {login_resp.status_code}")

    # 提交密码
    log.info("[*] 提交密码...")
    pwd_sentinel = get_sentinel_header(device_id, ua, "password_verify", proxies)
    pwd_resp = s.post_json(
        "https://auth.openai.com/api/accounts/password/verify",
        {"password": password},
        headers={
            "Referer": "https://auth.openai.com/log-in/password",
            "openai-sentinel-token": pwd_sentinel,
        },
    )

    if pwd_resp.status_code < 200 or pwd_resp.status_code >= 300:
        raise RuntimeError(f"密码提交失败: {pwd_resp.status_code}")

    try:
        pwd_json = pwd_resp.json()
        login_continue_url = pwd_json.get("continue_url", "")
        login_page_type = pwd_json.get("page", {}).get("type", "")
    except Exception:
        login_continue_url = ""
        login_page_type = ""

    log.info(f"[*] 页面类型: {login_page_type}")

    # 检查是否需要邮箱验证
    need_login_otp = (
        "email_otp" in login_page_type
        or "email-verification" in login_continue_url
    )

    if need_login_otp:
        log.info("[*] 需要邮箱验证...")
        time.sleep(3)
        
        def _resend():
            r = s.post_json(
                "https://auth.openai.com/api/accounts/email-otp/resend",
                {},
                headers={"Referer": "https://auth.openai.com/email-verification"},
            )
            return r.status_code >= 200 and r.status_code < 300

        code = poll_verification_code(email, proxies, used_codes=codes, resend_fn=_resend)
        if not code:
            raise RuntimeError("未获取到登录验证码")

        log.info(f"[*] 验证码: {code}")
        otp_sentinel = get_sentinel_header(device_id, ua, "email_otp_validate", proxies)
        otp_resp = s.post_json(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            {"code": code},
            headers={
                "Referer": "https://auth.openai.com/email-verification",
                "openai-sentinel-token": otp_sentinel,
            },
        )

        if otp_resp.status_code < 200 or otp_resp.status_code >= 300:
            raise RuntimeError(f"验证码校验失败: {otp_resp.status_code}")

        log.info("[*] 验证码验证成功")

    return _complete_token_exchange(s, oauth, email, "", proxies)


def _complete_token_exchange(
    s: RegSession,
    oauth: OAuthStart,
    email: str,
    name: str,
    proxies: Any = None,
) -> dict:
    """完成 workspace 选择和 token 兑换"""

    # --- 选择 Workspace ---
    auth_cookie = s.get_cookie("oai-client-auth-session")
    if not auth_cookie:
        raise RuntimeError("未获取到 oai-client-auth-session cookie")

    try:
        cookie_data = _decode_jwt_segment(auth_cookie.split(".")[0])
        workspaces = cookie_data.get("workspaces", [])
        workspace_id = workspaces[0]["id"] if workspaces else None
    except Exception as e:
        raise RuntimeError(f"解析 workspace 失败: {e}")

    if not workspace_id:
        raise RuntimeError("未找到 workspace_id")

    log.info(f"  [9] 选择 Workspace: {workspace_id[:20]}...")
    select_resp = s.post_json(
        "https://auth.openai.com/api/accounts/workspace/select",
        {"workspace_id": workspace_id},
        headers={"Referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"},
    )

    if select_resp.status_code < 200 or select_resp.status_code >= 300:
        raise RuntimeError(f"选择 workspace 失败: {select_resp.status_code}")

    continue_url = select_resp.json().get("continue_url")
    if not continue_url:
        raise RuntimeError("未获取到 continue_url")

    # --- 跟随重定向获取 token ---
    log.info(f"  [10] 跟随重定向获取 Token...")
    callback_url = s.follow_redirects(continue_url)
    if not callback_url:
        raise RuntimeError("重定向失败，未获取到回调 URL")

    token_json = submit_callback_url(
        callback_url=callback_url,
        code_verifier=oauth.code_verifier,
        redirect_uri=oauth.redirect_uri,
        expected_state=oauth.state,
    )

    log.info(f"  🎉 注册成功！")
    
    return {
        "token": token_json,
        "email": email,
        "name": name,
    }


# ==========================================
# 单次注册执行
# ==========================================


def run_one(proxy: Optional[str], domain_index: int = DEFAULT_DOMAIN_INDEX) -> Optional[dict]:
    """执行单次注册"""
    proxies = {"http": proxy, "https": proxy} if proxy else None

    # IP 检查
    try:
        trace_resp = requests.get(
            "https://cloudflare.com/cdn-cgi/trace",
            proxies=proxies,
            impersonate="chrome",
            timeout=10,
        )
        loc_match = re.search(r"^loc=(.+)$", trace_resp.text, re.MULTILINE)
        loc = loc_match.group(1) if loc_match else None
        log.info(f"[*] 当前 IP 所在地: {loc}")
        if loc in ("CN", "HK"):
            raise RuntimeError("IP 所在地不支持，请检查代理")
    except Exception as e:
        log.error(f"[Error] 网络连接检查失败: {e}")
        return None

    # 创建邮箱
    try:
        domains = get_domains(proxies)
        if not domains:
            log.error("[Error] 没有可用域名")
            return None

        local = f"oc{secrets.token_hex(5)}"
        email_info = create_email(local, domain_index=domain_index, proxies=proxies)
        email = email_info.get("address") or email_info.get("email")

        if not email:
            log.error("[Error] 创建邮箱失败")
            return None

        email_password = secrets.token_urlsafe(12)
        if reset_mailbox_password(email, email_password, proxies=proxies):
            log.info(f"[*] 成功创建邮箱: {email}")
        else:
            email_password = "默认密码"
            log.info(f"[*] 创建邮箱: {email} (密码设置失败)")

    except Exception as e:
        log.error(f"[Error] 邮箱创建失败: {e}")
        return None

    # 生成 OpenAI 密码
    openai_password = _generate_password()
    log.info(f"[*] OpenAI 密码: {openai_password[:4]}****")

    # 执行注册
    try:
        result = register_account(email, openai_password, proxies)
        result["openai_password"] = openai_password
        return result
    except Exception as e:
        log.error(f"[Error] 注册失败: {e}")
        return {"email": email, "openai_password": openai_password, "error": str(e)}


def save_result(result: dict):
    """保存注册结果"""
    if not result or not result.get("token"):
        return

    token_json = result.get("token", "{}")
    email = result.get("email", "unknown")
    password = result.get("openai_password", "")

    try:
        t_data = json.loads(token_json)
        fname_email = t_data.get("email", email).replace("@", "_")
    except Exception:
        fname_email = email.replace("@", "_")

    os.makedirs(TOKENS_DIR, exist_ok=True)
    file_name = os.path.join(TOKENS_DIR, f"token_{fname_email}_{int(time.time())}.json")

    with open(file_name, "w", encoding="utf-8") as f:
        f.write(token_json)

    log.info(f"[*] Token 已保存至: {file_name}")

    # 保存账号信息
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {email} | {password}\n")


# ==========================================
# 主入口
# ==========================================


def main():
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本 V3 - 优化版")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("--domain-index", type=int, default=DEFAULT_DOMAIN_INDEX, help="邮箱域名索引")
    parser.add_argument("--count", type=int, default=1, help="注册数量")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=30, help="循环模式最长等待秒数")
    parser.add_argument("--save-account", action="store_true", help="保存账号密码")
    args = parser.parse_args()

    log.info("=" * 55)
    log.info(" OpenAI 自动注册 V3 - 优化版")
    log.info("=" * 55)

    if not HAS_SENTINEL_POW:
        log.warning("⚠️ sentinel_pow 模块未安装，将使用空 PoW（可能被风控）")

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None

    if args.once or args.count == 1:
        # 单次模式
        result = run_one(args.proxy, args.domain_index)
        if result and result.get("token"):
            save_result(result)
        # 清理邮箱
        if result and result.get("email"):
            delete_mailbox(result["email"], proxies)
        return

    # 批量模式
    total = args.count
    workers = min(args.workers, total)
    stats = {"ok": 0, "fail": 0}
    lock = threading.Lock()

    def _do_one(idx: int, delay: float = 0):
        if delay > 0:
            time.sleep(delay)
        
        start_t = time.time()
        log.info(f"\n{'─'*50}")
        log.info(f"[{idx}/{total}] 开始注册...")
        log.info(f"{'─'*50}")

        result = run_one(args.proxy, args.domain_index)
        
        if result and result.get("token"):
            save_result(result)
            with lock:
                stats["ok"] += 1
            elapsed = round(time.time() - start_t, 1)
            log.info(f"  ✅ 成功 ({elapsed}s)")
        else:
            with lock:
                stats["fail"] += 1
            log.info(f"  ❌ 失败")

        # 清理邮箱
        if result and result.get("email"):
            delete_mailbox(result["email"], proxies)

    if workers <= 1:
        # 串行
        for i in range(1, total + 1):
            _do_one(i)
            if i < total:
                wait = random.randint(args.sleep_min, args.sleep_max)
                log.info(f"[*] 休息 {wait} 秒...")
                time.sleep(wait)
    else:
        # 并行
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {}
            for i in range(1, total + 1):
                wave_pos = (i - 1) % workers
                delay = wave_pos * random.uniform(1.0, 2.5) if wave_pos > 0 else 0
                fut = pool.submit(_do_one, i, delay)
                futs[fut] = i

            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:
                    log.error(f"线程异常: {e}")

    log.info(f"\n{'='*55}")
    log.info(f"  注册完成")
    log.info(f"{'='*55}")
    log.info(f"  ✅ 成功: {stats['ok']}")
    log.info(f"  ❌ 失败: {stats['fail']}")
    log.info(f"  📁 结果: {TOKENS_DIR}")


if __name__ == "__main__":
    main()
