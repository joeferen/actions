"""
OpenAI 自动注册脚本 V4 - TempMail.lol 版
基于 V3，将邮箱服务替换为 TempMail.lol：
1. 使用 TempMail.lol API 创建临时邮箱
2. 真实 Sentinel PoW 计算
3. 新注册后重新登录获取 token
4. 随机姓名/生日生成
5. 已注册账号识别处理
6. OTP 定时重发策略

TempMail.lol API 文档: https://tempmail.lol/api

注意：TempMail.lol 免费层级对部分国家/地区（如中国）有访问限制
- 解决方案1: 使用代理访问 (--proxy 参数)
- 解决方案2: 购买 TempMail Plus/Ultra 订阅，设置 TEMPMAIL_API_KEY 环境变量
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
# 脚本目录（提前定义，供后续使用）
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ==========================================
# 日志配置
# ==========================================
LOG_FILE = os.path.join(SCRIPT_DIR, "openai_register_v4.log")

def _setup_logger():
    logger = logging.getLogger("reg_v4")
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

# TempMail.lol API 配置
# 注意：API v2 端点
TEMPMAIL_API_BASE = "https://api.tempmail.lol/v2"
# 可选: TempMail API Key (用于自定义域名等功能)
TEMPMAIL_API_KEY = os.environ.get("TEMPMAIL_API_KEY", "")

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SENTINEL_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "email_accounts_v4.txt")
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

# 浏览器指纹列表 - curl_cffi 官方支持的版本（按浏览器分组）
_BROWSER_PROFILES_CHROME = [
    'chrome99', 'chrome100', 'chrome101', 'chrome104', 'chrome107',
    'chrome110', 'chrome116', 'chrome119', 'chrome120', 'chrome123',
    'chrome124', 'chrome131', 'chrome133a', 'chrome136', 'chrome142',
]

_BROWSER_PROFILES_EDGE = ['edge99', 'edge101']

_BROWSER_PROFILES_SAFARI = [
    'safari15_3', 'safari15_5', 'safari17_0', 'safari17_2_ios', 'safari18_0',
    'safari153', 'safari155', 'safari170', 'safari180', 'safari184',
    'safari260', 'safari2601',
]

_BROWSER_PROFILES_FIREFOX = ['firefox133', 'firefox135', 'firefox144']

# 所有的浏览器指纹组合
_BROWSER_PROFILES = (
    _BROWSER_PROFILES_CHROME +
    _BROWSER_PROFILES_EDGE +
    _BROWSER_PROFILES_SAFARI +
    _BROWSER_PROFILES_FIREFOX
)

# 语言-地区组合，用于动态生成 Accept-Language
_LANG_CODES = ['en', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'pt', 'it', 'ru', 'nl', 'pl']
_REGION_CODES = ['US', 'GB', 'CN', 'TW', 'ES', 'MX', 'FR', 'DE', 'JP', 'KR', 'BR', 'IT', 'RU', 'NL', 'PL']


def _generate_accept_language() -> str:
    """动态生成随机但合理的 Accept-Language 头"""
    # 随机选择主要语言
    primary_lang = random.choice(_LANG_CODES)
    primary_region = random.choice(_REGION_CODES)
    
    # 生成主语言标签
    if primary_lang in ['en', 'zh', 'es', 'pt']:
        main_tag = f"{primary_lang}-{primary_region}"
    else:
        main_tag = f"{primary_lang}-{primary_region}"
    
    # 随机决定是否添加次要语言
    parts = [f"{main_tag},*;q=0.9" if random.random() < 0.3 else main_tag]
    
    # 添加英文作为备选（非英文主语言时）
    if primary_lang != 'en' and random.random() < 0.8:
        en_variant = random.choice(['en-US', 'en-GB', 'en'])
        parts.append(f"{en_variant};q=0.8")
    
    # 添加原生语言标签
    if random.random() < 0.5:
        parts.append(f"{primary_lang};q=0.9")
    
    # 随机添加其他语言
    if random.random() < 0.4:
        other_lang = random.choice([l for l in _LANG_CODES if l != primary_lang])
        parts.append(f"{other_lang};q=0.7")
    
    # 构建最终的 Accept-Language 字符串
    if len(parts) == 1:
        # 单语言，可能加上质量因子
        if random.random() < 0.3:
            return f"{parts[0]},*;q=0.5"
        return parts[0]
    else:
        return ",".join(parts)


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
    """随机选择浏览器指纹，动态生成 Accept-Language"""
    profile = random.choice(_BROWSER_PROFILES)
    lang = _generate_accept_language()
    return profile, lang


# ==========================================
# 人类行为模拟 - 反检测措施
# ==========================================

def human_delay(action: str = "default") -> float:
    """
    模拟人类操作延迟，返回延迟秒数
    不同操作类型有不同的延迟特征
    """
    delay_profiles = {
        # 页面加载后阅读/思考
        "read": (1.5, 4.0),
        "think": (0.8, 2.5),
        # 表单操作
        "type_email": (2.0, 5.0),  # 输入邮箱需要时间
        "type_password": (1.5, 3.5),  # 输入密码
        "type_code": (2.5, 6.0),  # 输入验证码（需要查看）
        "click": (0.3, 1.2),
        "form_submit": (0.5, 1.5),
        # 网络等待
        "network": (0.5, 1.5),
        "redirect": (0.3, 0.8),
        # 默认
        "default": (0.5, 2.0),
    }
    lo, hi = delay_profiles.get(action, delay_profiles["default"])
    
    # 使用正态分布让延迟更自然（大多数人操作在平均值附近）
    mean = (lo + hi) / 2
    std = (hi - lo) / 4
    delay = random.gauss(mean, std)
    
    # 限制在合理范围内
    return max(lo, min(hi, delay))


def human_sleep(action: str = "default") -> None:
    """执行人类行为模拟延迟"""
    delay = human_delay(action)
    time.sleep(delay)


def generate_sec_ch_ua(profile: str) -> str:
    """根据浏览器指纹生成 Sec-CH-UA 头"""
    if profile.startswith("chrome"):
        # 提取版本号
        version = profile.replace("chrome", "").replace("a", "")
        if version.isdigit():
            v = int(version)
            return f'"Chromium";v="{v}", "Google Chrome";v="{v}", "Not?A_Brand";v="99"'
        return '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="99"'
    
    elif profile.startswith("edge"):
        version = profile.replace("edge", "")
        if version.isdigit():
            v = int(version)
            return f'"Chromium";v="{v}", "Microsoft Edge";v="{v}", "Not?A_Brand";v="99"'
        return '"Chromium";v="120", "Microsoft Edge";v="120", "Not?A_Brand";v="99"'
    
    elif profile.startswith("safari"):
        # Safari 通常不发送 Sec-CH-UA，或发送简短版本
        return '"Safari";v="605.1.15"'
    
    elif profile.startswith("firefox"):
        # Firefox 不发送 Sec-CH-UA
        return ""
    
    return ""


def get_browser_platform(profile: str) -> str:
    """根据浏览器类型返回可能的平台"""
    if profile.startswith("safari"):
        # Safari 通常是 macOS 或 iOS
        return random.choice(["macOS", "iPhone", "iPad"])
    elif profile.startswith("chrome"):
        return random.choice(["Windows", "macOS", "Linux", "Chrome OS"])
    elif profile.startswith("edge"):
        return random.choice(["Windows", "macOS"])
    elif profile.startswith("firefox"):
        return random.choice(["Windows", "macOS", "Linux"])
    return "Windows"


def generate_client_hints(profile: str, lang: str) -> Dict[str, str]:
    """生成现代浏览器的客户端提示头"""
    hints = {}
    sec_ch_ua = generate_sec_ch_ua(profile)
    
    if sec_ch_ua:
        hints["Sec-CH-UA"] = sec_ch_ua
        hints["Sec-CH-UA-Mobile"] = "?0"
        hints["Sec-CH-UA-Platform"] = f'"{get_browser_platform(profile)}"'
    
    # Sec-Fetch 系列头
    hints["Sec-Fetch-Dest"] = "document"
    hints["Sec-Fetch-Mode"] = "navigate"
    hints["Sec-Fetch-Site"] = "same-origin"
    hints["Sec-Fetch-User"] = "?1"
    
    # DNT (Do Not Track) - 随机化
    if random.random() < 0.6:
        hints["DNT"] = "1"
    
    # Connection
    hints["Connection"] = "keep-alive"
    
    return hints


# ==========================================
# TempMail.lol 邮箱 API
# ==========================================
# API 文档: https://tempmail.lol/api
#
# 基础 URL: https://api.tempmail.lol/v2
#
# 创建邮箱: POST /inbox/create
#   请求体: {"domain": "可选", "prefix": "可选"}
#   响应: {"address": "邮箱地址", "token": "访问令牌"}
#
# 获取邮件: GET /inbox?token={token}
#   响应: {"emails": [...], "expired": boolean}
#   邮件字段: from, to, subject, body, html, date
# ==========================================


def _tempmail_headers() -> Dict[str, str]:
    """构建 TempMail.lol 请求头"""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if TEMPMAIL_API_KEY:
        headers["Authorization"] = f"Bearer {TEMPMAIL_API_KEY}"
    return headers


@dataclass
class TempMailInbox:
    """TempMail 收件箱信息"""
    address: str
    token: str


def create_tempmail(
    prefix: Optional[str] = None,
    domain: Optional[str] = None,
    proxies: Any = None,
) -> TempMailInbox:
    """
    创建 TempMail.lol 临时邮箱
    
    Args:
        prefix: 邮箱前缀（@之前的部分），不指定则随机
        domain: 邮箱域名，不指定则随机
        proxies: 代理设置
    
    Returns:
        TempMailInbox: 包含 address 和 token
    """
    body = {}
    if prefix:
        body["prefix"] = prefix
    if domain:
        body["domain"] = domain
    
    # 正确端点: POST /v2/inbox/create
    resp = requests.post(
        f"{TEMPMAIL_API_BASE}/inbox/create",
        headers=_tempmail_headers(),
        json=body if body else None,
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    
    if resp.status_code not in (200, 201):
        error_text = resp.text[:300]
        # 检查是否是地区限制错误
        if "not allowed to use the API free tier" in error_text:
            raise RuntimeError(
                f"TempMail.lol 地区限制: 您的 IP 所在地不允许使用免费 API。\n"
                f"解决方案:\n"
                f"  1. 使用代理访问 (--proxy 参数)\n"
                f"  2. 购买 TempMail Plus/Ultra 订阅并设置 TEMPMAIL_API_KEY 环境变量\n"
                f"原始错误: {error_text}"
            )
        raise RuntimeError(f"创建 TempMail 失败: {resp.status_code}: {error_text}")
    
    data = resp.json()
    address = data.get("address", "")
    token = data.get("token", "")
    
    if not address or not token:
        raise RuntimeError(f"创建 TempMail 返回数据无效: {data}")
    
    return TempMailInbox(address=address, token=token)


def get_tempmail_emails(
    token: str,
    proxies: Any = None,
) -> Dict[str, Any]:
    """
    获取 TempMail 邮件列表
    
    Args:
        token: 收件箱访问令牌
        proxies: 代理设置
    
    Returns:
        dict: {"emails": [...], "expired": boolean}
    """
    # 正确端点: GET /v2/inbox?token=xxx
    resp = requests.get(
        f"{TEMPMAIL_API_BASE}/inbox?token={token}",
        headers=_tempmail_headers(),
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    
    if resp.status_code != 200:
        return {"emails": [], "expired": False}
    
    return resp.json()


# ==========================================
# 验证码轮询 (TempMail.lol 版)
# ==========================================


def poll_verification_code(
    email: str,
    inbox_token: str,
    proxies: Any = None,
    timeout: int = MAIL_POLL_TIMEOUT,
    used_codes: Optional[set] = None,
    resend_fn: Optional[Callable] = None,
    otp_sent_at: Optional[float] = None,
) -> str:
    """
    轮询获取 OpenAI 6 位验证码 (TempMail.lol 版)
    
    Args:
        email: 邮箱地址
        inbox_token: TempMail 收件箱访问令牌
        proxies: 代理设置
        timeout: 超时时间（秒）
        used_codes: 已使用的验证码集合
        resend_fn: 重发 OTP 的函数
        otp_sent_at: OTP 发送时间
    
    Returns:
        str: 6位验证码
    """
    regex = r"(?<!\d)(\d{6})(?!\d)"
    used = used_codes or set()
    start = time.time()
    last_resend = 0.0
    intervals = [3, 4, 5, 6, 8, 10]
    idx = 0

    log.info(f"    📧 等待验证码 ({email})...")

    while time.time() - start < timeout:
        try:
            data = get_tempmail_emails(inbox_token, proxies)
            
            # 检查收件箱是否过期
            if data.get("expired"):
                log.warning("    ⚠️ 收件箱已过期")
                break
            
            emails = data.get("emails", [])
            
            for mail in emails:
                sender = str(mail.get("from") or "").lower()
                subject = str(mail.get("subject") or "")
                body = str(mail.get("body") or "")
                html = str(mail.get("html") or "")
                
                # 检查是否来自 OpenAI
                content = f"{sender} {subject} {body} {html}".lower()
                if "openai" not in content:
                    continue
                
                # 从邮件内容中提取验证码
                # 优先检查 body，然后检查 html
                for text in [body, html, subject]:
                    m = re.search(regex, text)
                    if m:
                        code = m.group(1)
                        if code not in used:
                            used.add(code)
                            elapsed = int(time.time() - start)
                            log.info(f"    ✅ 验证码: {code} (耗时 {elapsed}s)")
                            return code

        except Exception as e:
            log.warning(f"    TempMail API 查询失败: {e}")

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
    """注册会话，支持随机浏览器指纹和反检测措施"""

    def __init__(self, proxies: Any = None):
        self.profile, self.lang = pick_browser_profile()
        self.proxies = proxies
        self._session = requests.Session(
            proxies=proxies,
            impersonate=self.profile,
        )
        
        # 基础请求头
        base_headers = {
            "Accept-Language": self.lang,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": random.choice(["max-age=0", "no-cache", ""]),
            "Pragma": random.choice(["no-cache", ""]),
            "Upgrade-Insecure-Requests": "1",
        }
        
        # 添加客户端提示头
        client_hints = generate_client_hints(self.profile, self.lang)
        base_headers.update({k: v for k, v in client_hints.items() if v})
        
        self._session.headers.update(base_headers)
        
        # 记录信息
        platform = get_browser_platform(self.profile)
        log.info(f"    🎭 浏览器指纹: {self.profile} | 平台: {platform}")

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
    inbox_token: str,
    openai_password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    """
    核心注册逻辑
    
    Args:
        email: 邮箱地址
        inbox_token: TempMail 收件箱访问令牌
        openai_password: OpenAI 账号密码
        proxies: 代理设置
        used_codes: 已使用的验证码集合
    
    Returns:
        dict: 包含 token 信息的字典
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

        human_sleep("read")  # 页面加载后阅读

        # --- 2. 获取 Sentinel (功能 1: 真实 PoW) ---
        log.info(f"  [2] 求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"      Sentinel token OK")

        human_sleep("think")  # 思考时间

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

        human_sleep("network")  # 网络响应等待

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
            human_sleep("click")  # 点击发送按钮
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
            email, inbox_token, proxies,
            used_codes=codes,
            resend_fn=_resend,
            otp_sent_at=otp_sent_at,
        )

        human_sleep("type_code")  # 模拟输入验证码的时间

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

        human_sleep("network")  # 等待网络响应

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
                return _login_for_token(email, openai_password, inbox_token, proxies)

        # 新注册账号需要重新登录 (功能 2)
        needs_relogin = not is_existing_account

        if not needs_relogin:
            # 已注册账号，直接完成 token 获取
            return _complete_token_exchange(s, oauth, email, name, proxies)

    # --- 新注册账号：重新发起登录流程 ---
    log.info(f"  [8.5] 注册完成，重新发起登录流程...")
    human_sleep("think")  # 思考时间

    return _relogin_for_token(email, openai_password, inbox_token, proxies, codes)


def _relogin_for_token(
    email: str,
    password: str,
    inbox_token: str,
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

        human_sleep("read")  # 页面加载后阅读

        # Sentinel
        log.info(f"  [8.5b] 重新求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"         Sentinel token OK")

        human_sleep("think")  # 思考时间

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

        human_sleep("network")  # 网络响应等待

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

        human_sleep("network")  # 网络响应等待

        # 获取并验证登录 OTP
        def _resend():
            r = s.post_json(
                "https://auth.openai.com/api/accounts/email-otp/resend",
                {},
                headers={"Referer": "https://auth.openai.com/email-verification"},
            )
            return r.status_code >= 200 and r.status_code < 300

        code = poll_verification_code(
            email, inbox_token, proxies,
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

        human_sleep("network")  # 网络响应等待

        return _complete_token_exchange(s, oauth, email, "", proxies)


def _login_for_token(
    email: str,
    password: str,
    inbox_token: str,
    proxies: Any = None,
) -> dict:
    """通过账号密码登录获取 token（绕过手机验证）"""
    log.info("[*] ===== 尝试密码登录获取 token =====")

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()
        
        log.info("[*] 初始化 OAuth 会话...")
        s.get(oauth.auth_url)
        device_id = s.get_cookie("oai-did") or ""

        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
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
            human_sleep("network")  # 等待验证码发送
            
            def _resend():
                r = s.post_json(
                    "https://auth.openai.com/api/accounts/email-otp/resend",
                    {},
                    headers={"Referer": "https://auth.openai.com/email-verification"},
                )
                return r.status_code >= 200 and r.status_code < 300

            code = poll_verification_code(email, inbox_token, proxies, resend_fn=_resend)
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


def run_one(proxy: Optional[str], domain: Optional[str] = None) -> Optional[dict]:
    """
    执行单次注册
    
    Args:
        proxy: 代理地址
        domain: TempMail 域名（可选）
    
    Returns:
        dict: 注册结果
    """
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

    # 创建 TempMail 邮箱
    try:
        # 生成随机前缀
        prefix = f"oc{secrets.token_hex(5)}"
        
        inbox = create_tempmail(prefix=prefix, domain=domain, proxies=proxies)
        email = inbox.address
        inbox_token = inbox.token

        if not email or not inbox_token:
            log.error("[Error] 创建邮箱失败")
            return None

        log.info(f"[*] 成功创建 TempMail: {email}")

    except Exception as e:
        log.error(f"[Error] 邮箱创建失败: {e}")
        return None

    # 生成 OpenAI 密码
    openai_password = _generate_password()
    log.info(f"[*] OpenAI 密码: {openai_password[:4]}****")

    # 执行注册
    try:
        result = register_account(email, inbox_token, openai_password, proxies)
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
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本 V4 - TempMail.lol 版")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("--domain", default=None, help="TempMail 域名（可选）")
    parser.add_argument("--count", type=int, default=1, help="注册数量")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=30, help="循环模式最长等待秒数")
    parser.add_argument("--save-account", action="store_true", help="保存账号密码")
    args = parser.parse_args()

    log.info("=" * 55)
    log.info(" OpenAI 自动注册 V4 - TempMail.lol 版")
    log.info("=" * 55)

    if not HAS_SENTINEL_POW:
        log.warning("⚠️ sentinel_pow 模块未安装，将使用空 PoW（可能被风控）")

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None

    if args.once or args.count == 1:
        # 单次模式
        result = run_one(args.proxy, args.domain)
        if result and result.get("token"):
            save_result(result)
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

        result = run_one(args.proxy, args.domain)
        
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
