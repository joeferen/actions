"""
OpenAI 自动注册脚本 V5 - 集成版
基于 V3 整合 codex_maintenance.js 的维护功能

功能列表：
1. 真实 Sentinel PoW 计算
2. 新注册后重新登录获取 token
3. 随机姓名/生日生成
4. 已注册账号识别处理
5. OTP 定时重发策略
6. 账号状态检测（401 + 额度检测）
7. 失效账号自动删除
8. Token 文件上传与验证
9. 维护循环模式

================================================================================
使用方法与示例
================================================================================

基础参数：
  --target-url         目标服务器地址（账号管理服务）
  --target-token       目标服务器认证令牌
  --proxy             代理地址，如 http://127.0.0.1:10808
  --domain-index      邮箱域名索引（默认 0，与 --domain 二选一）
  --domain            指定邮箱域名（如 mail.example.com），优先级高于 --domain-index
  --mail-url          邮箱服务地址（默认 https://mailfree.smanx.xx.kg）
  --mail-token        邮箱服务授权令牌（默认 auto）

注册模式参数：
  --count             注册数量（默认 1）
  --workers           并发线程数（默认 1）
  --sleep-min         循环模式最短等待秒数（默认 5）
  --sleep-max         循环模式最长等待秒数（默认 30）

维护模式参数：
  --min-accounts      账号数量阈值（默认 100）
  --quota-threshold   额度不足删除阈值百分比（默认 20）
  --register-timeout  注册循环总时长限制（秒，默认 18000）
  --concurrency       检测账号并发数（默认 50）
  --register-count    注册模式：
                       0 = 检测账号状态，补充不足数量
                       1 = 默认每轮注册 1 个账号
                       N = 批量直接注册 N 个账号
  --mode              运行模式：
                       register = 仅注册
                       maintenance = 仅维护
                       both = 注册+维护（默认）

================================================================================
运行示例
================================================================================

# 1. 单独注册模式 - 注册 1 个账号
python openai_register_v5.py --count 1

# 2. 单独注册模式 - 使用代理注册
python openai_register_v5.py --proxy http://127.0.0.1:10808

# 3. 单独注册模式 - 指定邮箱域名
python openai_register_v5.py --domain mail.example.com

# 4. 单独注册模式 - 批量注册 10 个账号
python openai_register_v5.py --count 10 --workers 5

# 5. 维护模式 - 检测并补充账号至阈值
python openai_register_v5.py --mode maintenance --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100 --register-count 0

# 6. 维护模式 - 默认每轮注册 1 个账号
python openai_register_v5.py --mode maintenance --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100

# 7. 维护模式 - 批量直接注册 10 个账号
python openai_register_v5.py --mode maintenance --target-url https://api.example.com --target-token YOUR_TOKEN --register-count 10

# 7. 综合模式 - 同时支持注册和维护
python openai_register_v5.py --mode both --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100

# 8. 使用环境变量设置目标服务器地址和令牌
set TARGET_URL=https://api.example.com
set TARGET_TOKEN=your_token
python openai_register_v5.py --mode maintenance --min-accounts 100 --register-count 0

# 9. 自定义参数
python openai_register_v5.py --mode both --target-url https://api.example.com --target-token YOUR_TOKEN --quota-threshold 15 --register-timeout 3600 --concurrency 30

================================================================================
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
import subprocess
import ssl
import socket
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, urljoin
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from curl_cffi import requests
except ImportError:
    print("[Error] 需要安装 curl_cffi: pip install curl_cffi")
    sys.exit(1)

try:
    from sentinel_pow import build_sentinel_pow_token, SentinelPOWError
    HAS_SENTINEL_POW = True
except ImportError:
    HAS_SENTINEL_POW = False
    print("[Warn] sentinel_pow 模块未安装，将使用空 PoW")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(SCRIPT_DIR, "openai_register_v5.log")

def _setup_logger():
    logger = logging.getLogger("reg_v5")
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

DEFAULT_MAILFREE_BASE = "https://mailfree.smanx.xx.kg"
DEFAULT_JWT_TOKEN = "auto"
MAILFREE_BASE = DEFAULT_MAILFREE_BASE
JWT_TOKEN = DEFAULT_JWT_TOKEN
DEFAULT_DOMAIN_INDEX = 0

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SENTINEL_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "email_accounts_v5.txt")
TOKENS_DIR = SCRIPT_DIR

MAIL_POLL_TIMEOUT = 180
OTP_RESEND_INTERVAL = 25
MAX_RETRY_PER_ACCOUNT = 3

DEFAULT_MIN_ACCOUNTS = 100
DEFAULT_QUOTA_THRESHOLD_PERCENT = 20
DEFAULT_REGISTER_TIMEOUT = 18000
DEFAULT_CONCURRENCY = 50
DEFAULT_TARGET_BASE_URL = ""
DEFAULT_TARGET_TOKEN = ""
NOTIFY_BASE_URL = "https://api.day.app/xxxxxxxx"

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

_BROWSER_PROFILES = (
    _BROWSER_PROFILES_CHROME +
    _BROWSER_PROFILES_EDGE +
    _BROWSER_PROFILES_SAFARI +
    _BROWSER_PROFILES_FIREFOX
)

_LANG_CODES = ['en', 'zh', 'es', 'fr', 'de', 'ja', 'ko', 'pt', 'it', 'ru', 'nl', 'pl']
_REGION_CODES = ['US', 'GB', 'CN', 'TW', 'ES', 'MX', 'FR', 'DE', 'JP', 'KR', 'BR', 'IT', 'RU', 'NL', 'PL']


def _generate_accept_language() -> str:
    primary_lang = random.choice(_LANG_CODES)
    primary_region = random.choice(_REGION_CODES)
    main_tag = f"{primary_lang}-{primary_region}"
    parts = [f"{main_tag},*;q=0.9" if random.random() < 0.3 else main_tag]
    if primary_lang != 'en' and random.random() < 0.8:
        en_variant = random.choice(['en-US', 'en-GB', 'en'])
        parts.append(f"{en_variant};q=0.8")
    if random.random() < 0.5:
        parts.append(f"{primary_lang};q=0.9")
    if random.random() < 0.4:
        other_lang = random.choice([l for l in _LANG_CODES if l != primary_lang])
        parts.append(f"{other_lang};q=0.7")
    if len(parts) == 1:
        if random.random() < 0.3:
            return f"{parts[0]},*;q=0.5"
        return parts[0]
    else:
        return ",".join(parts)


def random_name() -> str:
    return f"{random.choice(_GIVEN_NAMES)} {random.choice(_FAMILY_NAMES)}"


def random_birthday() -> str:
    y = random.randint(1986, 2006)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


def pick_browser_profile() -> tuple:
    profile = random.choice(_BROWSER_PROFILES)
    lang = _generate_accept_language()
    return profile, lang


def human_delay(action: str = "default") -> float:
    delay_profiles = {
        "read": (1.5, 4.0),
        "think": (0.8, 2.5),
        "type_email": (2.0, 5.0),
        "type_password": (1.5, 3.5),
        "type_code": (2.5, 6.0),
        "click": (0.3, 1.2),
        "form_submit": (0.5, 1.5),
        "network": (0.5, 1.5),
        "redirect": (0.3, 0.8),
        "default": (0.5, 2.0),
    }
    lo, hi = delay_profiles.get(action, delay_profiles["default"])
    mean = (lo + hi) / 2
    std = (hi - lo) / 4
    delay = random.gauss(mean, std)
    return max(lo, min(hi, delay))


def human_sleep(action: str = "default") -> None:
    delay = human_delay(action)
    time.sleep(delay)


def generate_sec_ch_ua(profile: str) -> str:
    if profile.startswith("chrome"):
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
        return '"Safari";v="605.1.15"'
    elif profile.startswith("firefox"):
        return ""
    return ""


def get_browser_platform(profile: str) -> str:
    if profile.startswith("safari"):
        return random.choice(["macOS", "iPhone", "iPad"])
    elif profile.startswith("chrome"):
        return random.choice(["Windows", "macOS", "Linux", "Chrome OS"])
    elif profile.startswith("edge"):
        return random.choice(["Windows", "macOS"])
    elif profile.startswith("firefox"):
        return random.choice(["Windows", "macOS", "Linux"])
    return "Windows"


def generate_client_hints(profile: str, lang: str) -> Dict[str, str]:
    hints = {}
    sec_ch_ua = generate_sec_ch_ua(profile)
    if sec_ch_ua:
        hints["Sec-CH-UA"] = sec_ch_ua
        hints["Sec-CH-UA-Mobile"] = "?0"
        hints["Sec-CH-UA-Platform"] = f'"{get_browser_platform(profile)}"'
    hints["Sec-Fetch-Dest"] = "document"
    hints["Sec-Fetch-Mode"] = "navigate"
    hints["Sec-Fetch-Site"] = "same-origin"
    hints["Sec-Fetch-User"] = "?1"
    if random.random() < 0.6:
        hints["DNT"] = "1"
    hints["Connection"] = "keep-alive"
    return hints


def _mailfree_headers() -> Dict[str, Any]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Admin-Token": JWT_TOKEN,
    }


def get_domains(proxies: Any = None) -> List[str]:
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


def get_domain_by_name(proxies: Any = None, domain_name: str = None) -> tuple:
    domains = get_domains(proxies)
    if not domains:
        raise RuntimeError("没有可用域名")
    if domain_name:
        domain_lower = domain_name.lower()
        for idx, domain in enumerate(domains):
            if domain.lower() == domain_lower:
                return idx, domain
        available = ", ".join(domains)
        raise RuntimeError(f"域名 '{domain_name}' 不存在，可用域名: {available}")
    return 0, domains[0]


def create_email(local: str, domain_index: int = 0, proxies: Any = None) -> Dict[str, Any]:
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
    resp = requests.post(
        f"{MAILFREE_BASE}/api/mailboxes/change-password",
        headers=_mailfree_headers(),
        json={"address": address, "new_password": new_password},
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    return resp.status_code == 200


def poll_verification_code(
    email: str,
    proxies: Any = None,
    timeout: int = MAIL_POLL_TIMEOUT,
    used_codes: Optional[set] = None,
    resend_fn: Optional[Callable] = None,
    otp_sent_at: Optional[float] = None,
) -> str:
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

                        if "openai" not in sender and "openai" not in subject.lower():
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


def build_sentinel_token(device_id: str, user_agent: str, flow: str = "authorize_continue") -> str:
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


class RegSession:
    def __init__(self, proxies: Any = None):
        self.profile, self.lang = pick_browser_profile()
        self.proxies = proxies
        self._session = requests.Session(
            proxies=proxies,
            impersonate=self.profile,
        )

        base_headers = {
            "Accept-Language": self.lang,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": random.choice(["max-age=0", "no-cache", ""]),
            "Pragma": random.choice(["no-cache", ""]),
            "Upgrade-Insecure-Requests": "1",
        }

        client_hints = generate_client_hints(self.profile, self.lang)
        base_headers.update({k: v for k, v in client_hints.items() if v})

        self._session.headers.update(base_headers)

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


def register_account(
    email: str,
    openai_password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    codes = used_codes or set()

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()
        log.info(f"  [1] 发起 OAuth...")
        resp = s.get(oauth.auth_url)
        log.info(f"      状态: {resp.status_code}")

        device_id = s.get_cookie("oai-did") or ""
        if device_id:
            log.info(f"      设备ID: {device_id[:16]}...")

        human_sleep("read")

        log.info(f"  [2] 求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"      Sentinel token OK")

        human_sleep("think")

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

        try:
            step3_data = signup_resp.json()
            page_type = step3_data.get("page", {}).get("type", "")
        except Exception:
            step3_data = {}
            page_type = ""

        log.info(f"      页面类型: {page_type}")

        is_existing_account = (page_type == "email_otp_verification")

        human_sleep("network")

        name = ""

        if is_existing_account:
            log.info(f"  [4] 检测到已注册账号，OTP 已自动发送")
            otp_sent_at = time.time()
        else:
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

            human_sleep("click")
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

        human_sleep("type_code")

        log.info(f"  [7] 验证 OTP: {code}")
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

        human_sleep("network")

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

            try:
                create_data = create_resp.json()
                create_page_type = create_data.get("page", {}).get("type", "")
            except Exception:
                create_page_type = ""

            if create_page_type == "add_phone":
                log.info(f"      需要手机验证，尝试密码登录绕过...")
                return _login_for_token(email, openai_password, proxies)

        needs_relogin = not is_existing_account

        if not needs_relogin:
            return _complete_token_exchange(s, oauth, email, name, proxies)

    log.info(f"  [8.5] 注册完成，重新发起登录流程...")
    human_sleep("think")

    return _relogin_for_token(email, openai_password, proxies, codes)


def _relogin_for_token(
    email: str,
    password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    codes = used_codes or set()

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()
        log.info(f"  [8.5a] 重新发起 OAuth (登录)...")
        resp = s.get(oauth.auth_url)
        log.info(f"         状态: {resp.status_code}")

        device_id = s.get_cookie("oai-did") or ""
        if device_id:
            log.info(f"         设备ID: {device_id[:16]}...")

        human_sleep("read")

        log.info(f"  [8.5b] 重新求解 Sentinel PoW...")
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)
        log.info(f"         Sentinel token OK")

        human_sleep("think")

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

        human_sleep("network")

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

        human_sleep("network")

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

        human_sleep("network")

        return _complete_token_exchange(s, oauth, email, "", proxies)


def _login_for_token(
    email: str,
    password: str,
    proxies: Any = None,
) -> dict:
    log.info("[*] ===== 尝试密码登录获取 token =====")

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()

        log.info("[*] 初始化 OAuth 会话...")
        s.get(oauth.auth_url)
        device_id = s.get_cookie("oai-did") or ""

        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)

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

        need_login_otp = (
            "email_otp" in login_page_type
            or "email-verification" in login_continue_url
        )

        if need_login_otp:
            log.info("[*] 需要邮箱验证...")
            human_sleep("network")

            def _resend():
                r = s.post_json(
                    "https://auth.openai.com/api/accounts/email-otp/resend",
                    {},
                    headers={"Referer": "https://auth.openai.com/email-verification"},
                )
                return r.status_code >= 200 and r.status_code < 300

            code = poll_verification_code(email, proxies, resend_fn=_resend)
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


def run_one(proxy: Optional[str], domain_index: int = DEFAULT_DOMAIN_INDEX, domain_name: str = None) -> Optional[dict]:
    proxies = {"http": proxy, "https": proxy} if proxy else None

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

    try:
        idx, used_domain = get_domain_by_name(proxies, domain_name)
        local = f"oc{secrets.token_hex(5)}"
        email_info = create_email(local, domain_index=idx, proxies=proxies)
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

    openai_password = _generate_password()
    log.info(f"[*] OpenAI 密码: {openai_password[:4]}****")

    try:
        result = register_account(email, openai_password, proxies)
        result["openai_password"] = openai_password
        return result
    except Exception as e:
        log.error(f"[Error] 注册失败: {e}")
        return {"email": email, "openai_password": openai_password, "error": str(e)}


def save_result(result: dict):
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

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {email} | {password}\n")


def snapshot_token_files() -> Dict[str, dict]:
    m = {}
    files = scan_token_files()
    for filePath in files:
        try:
            st = os.stat(filePath)
            name = os.path.basename(filePath)
            m[name] = {
                "path": filePath,
                "mtimeMs": st.st_mtimeMs,
                "size": st.st_size
            }
        except:
            pass
    return m


def scan_token_files() -> List[str]:
    try:
        return [f for f in os.listdir(TOKENS_DIR) if re.match(r'^token.*\.json$', f, re.I)]
    except:
        return []


def diff_token_files(beforeSnap: Dict, afterSnap: Dict) -> List[str]:
    changed = []
    for name, meta in afterSnap.items():
        prev = beforeSnap.get(name)
        if not prev:
            changed.append(meta["path"])
            continue
        if prev["mtimeMs"] != meta["mtimeMs"] or prev["size"] != meta["size"]:
            changed.append(meta["path"])
    return changed


def normalize_url(url: str) -> str:
    s = (url or '').strip() \
        .replace('：', ':') \
        .replace('／', '/') \
        .replace('。', '.') \
        .replace('，', ',') \
        .replace('；', ';') \
        .replace('　', ' ') \
        .strip()
    if s.endswith('/'):
        s = s[:-1]
    return s


class HttpClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = normalize_url(base_url)
        self.token = token

    def _create_connection(self, url: str):
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if parsed.scheme == 'https':
            context = ssl.create_default_context()
            sock = socket.create_connection((host, port), timeout=30)
            return context.wrap_socket(sock, server_hostname=host)
        else:
            return socket.create_connection((host, port), timeout=30)

    def request(self, path: str, method: str = "GET", body: str = None, headers: dict = None) -> Tuple[int, Any]:
        url = self.base_url + path
        parsed = urlparse(url)

        request_headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        if headers:
            request_headers.update(headers)

        try:
            sock = self._create_connection(url)

            request_line = f"{method} {parsed.path}{'?' + parsed.query if parsed.query else ''} HTTP/1.1\r\n"
            header_str = ''.join(f"{k}: {v}\r\n" for k, v in request_headers.items())
            header_str += f"Host: {parsed.hostname}\r\n"
            header_str += "Connection: close\r\n"
            header_str += "\r\n"

            sock.sendall(header_str.encode())
            if body:
                sock.sendall(body.encode())

            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk

            sock.close()

            header_end = response.find(b'\r\n\r\n')
            if header_end == -1:
                return 500, "Invalid response"

            header_part = response[:header_end].decode('utf-8', errors='ignore')
            body_part = response[header_end + 4:]

            status_line = header_part.split('\r\n')[0]
            status_code = int(status_line.split()[1])

            try:
                body_json = json.loads(body_part.decode('utf-8', errors='ignore'))
                return status_code, body_json
            except:
                return status_code, body_part.decode('utf-8', errors='ignore')

        except Exception as e:
            return 500, str(e)


def send_notification(title: str, content: str, notify_base_url: str = NOTIFY_BASE_URL):
    try:
        encoded_title = requests.utils.quote(title)
        encoded_content = requests.utils.quote(content)
        url = f"{notify_base_url}/{encoded_title}/{encoded_content}"
        resp = requests.get(url, timeout=10)
        return resp.status_code == 200
    except:
        return False


def get_accounts(client: HttpClient) -> List[dict]:
    status, data = client.request('/v0/management/auth-files')
    print(f"  get_accounts HTTP状态: {status}, 数据类型: {type(data)}, 数据: {str(data)[:200]}")  # 调试
    if status >= 200 and status < 300:
        if isinstance(data, dict):
            files = data.get('files', [])
            print(f"  get_accounts files数量: {len(files)}")  # 调试
            return files
        elif isinstance(data, list):
            return data
        return []
    print(f"  get_accounts 请求失败: HTTP {status}")  # 调试
    return []


def parse_percent(v: Any) -> Optional[float]:
    if v is None or isinstance(v, float):
        return v
    try:
        s = str(v).strip().replace('%', '')
        return float(s) if s else None
    except:
        return None


async def check_account(client: HttpClient, item: dict, quota_threshold: float) -> dict:
    auth_index = item.get('auth_index')
    name = item.get('name') or item.get('id')
    chatgpt_account_id = item.get('chatgpt_account_id') or item.get('chatgptAccountId') or item.get('account_id') or item.get('accountId')

    if not auth_index:
        return {'name': name, 'error': 'missing auth_index', 'invalid_401': False, 'low_quota': False}

    try:
        payload = {
            'authIndex': auth_index,
            'method': 'GET',
            'url': 'https://chatgpt.com/backend-api/wham/usage',
            'header': {
                'Authorization': 'Bearer $TOKEN$',
                'Content-Type': 'application/json',
                'User-Agent': 'codex_cli_rs/0.76.0',
            }
        }
        if chatgpt_account_id:
            payload['header']['Chatgpt-Account-Id'] = chatgpt_account_id

        status, data = client.request('/v0/management/api-call', 'POST', json.dumps(payload))

        is_401 = status == 401 or (isinstance(data, dict) and data.get('status_code') == 401)

        used_percent = None
        remaining_percent = None
        quota_source = None
        primary_used_percent = None
        primary_reset_at = None
        individual_used_percent = None
        individual_reset_at = None

        if status == 200 or (isinstance(data, dict) and data.get('status_code') == 200):
            usage_data = {}
            body = data.get('body', data) if isinstance(data, dict) else data
            if isinstance(body, str):
                try:
                    usage_data = json.loads(body)
                except:
                    usage_data = {}
            elif isinstance(body, dict):
                usage_data = body

            rate_limit = usage_data.get('rate_limit', {}) or usage_data.get('rateLimit', {})

            windows = []
            for key in ['primary_window', 'secondary_window', 'individual_window', 'primaryWindow', 'secondaryWindow', 'individualWindow']:
                win = rate_limit.get(key) if isinstance(rate_limit, dict) else None
                if win and isinstance(win, dict):
                    windows.append({
                        'name': key,
                        'used_percent': parse_percent(win.get('used_percent') or win.get('usedPercent') or win.get('used_percentage')),
                        'reset_at': win.get('reset_at') or win.get('resetAt'),
                        'limit_window_seconds': win.get('limit_window_seconds') or win.get('limitWindowSeconds') or win.get('window_seconds') or win.get('windowSeconds'),
                        'remaining': win.get('remaining'),
                        'limit_reached': win.get('limit_reached') or win.get('limitReached')
                    })

            weekly_window = next((w for w in windows if 'individual' in w['name'].lower()), None)
            short_window = next((w for w in windows if 'secondary' in w['name'].lower()), None)

            if not weekly_window and windows:
                weekly_window = max(windows, key=lambda w: w.get('limit_window_seconds') or 0, default=None)
            if not short_window and windows:
                sorted_windows = sorted(windows, key=lambda w: w.get('limit_window_seconds') or 0)
                short_window = next((w for w in sorted_windows if w != weekly_window), sorted_windows[0] if sorted_windows else None)

            if weekly_window:
                individual_used_percent = weekly_window.get('used_percent')
                individual_reset_at = weekly_window.get('reset_at')
            if short_window:
                primary_used_percent = short_window.get('used_percent')
                primary_reset_at = short_window.get('reset_at')

            if weekly_window and weekly_window.get('used_percent') is not None:
                used_percent = weekly_window['used_percent']
                quota_source = 'weekly'
            elif short_window and short_window.get('used_percent') is not None:
                used_percent = short_window['used_percent']
                quota_source = 'short'

            if used_percent is not None and not isinstance(used_percent, (int, float)):
                try:
                    used_percent = float(used_percent)
                except:
                    used_percent = None

            if used_percent is not None:
                remaining_percent = 100 - used_percent

        low_quota = remaining_percent is not None and remaining_percent < quota_threshold

        return {
            'name': name,
            'status_code': status,
            'invalid_401': is_401,
            'low_quota': low_quota,
            'used_percent': used_percent,
            'remaining_percent': remaining_percent,
            'quota_source': quota_source,
            'primary_used_percent': primary_used_percent,
            'primary_reset_at': primary_reset_at,
            'individual_used_percent': individual_used_percent,
            'individual_reset_at': individual_reset_at,
            'error': None
        }
    except Exception as e:
        return {'name': name, 'error': str(e), 'invalid_401': False, 'low_quota': False}


def delete_account(client: HttpClient, name: str) -> bool:
    encoded = requests.utils.quote(name)
    status, _ = client.request(f'/v0/management/auth-files?name={encoded}', 'DELETE')
    return 200 <= status < 300


async def upload_file(client: HttpClient, file_path: str, retry_count: int = 3) -> Tuple[bool, str]:
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
    except:
        return False, "Cannot read file"

    url = f"{client.base_url}/v0/management/auth-files?name={requests.utils.quote(file_name)}"

    for attempt in range(1, retry_count + 1):
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            port = parsed.port or 443
            context = ssl.create_default_context()
            sock = socket.create_connection((host, port), timeout=30)
            ssock = context.wrap_socket(sock, server_hostname=host)

            body = file_content.encode('utf-8')
            request_headers = (
                f"POST {parsed.path}?{parsed.query} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}\r\n"
                f"Authorization: Bearer {client.token}\r\n"
                f"Accept: application/json\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            ssock.sendall(request_headers.encode() + body)

            response = b""
            while True:
                chunk = ssock.recv(4096)
                if not chunk:
                    break
                response += chunk
            ssock.close()

            header_end = response.find(b'\r\n\r\n')
            if header_end == -1:
                return False, "Invalid response"

            status_line = response[:header_end].decode('utf-8', errors='ignore').split('\r\n')[0]
            status_code = int(status_line.split()[1])
            response_body = response[header_end + 4:].decode('utf-8', errors='ignore')

            if 200 <= status_code < 300:
                try:
                    resp_data = json.loads(response_body) if response_body.strip() else {}
                    if resp_data.get('status') in ('ok', 'success') or not resp_data:
                        return True, file_name
                    elif resp_data.get('error'):
                        if attempt < retry_count:
                            time.sleep(1)
                            continue
                        return False, f"服务端错误: {resp_data.get('error')}"
                    else:
                        return True, file_name
                except json.JSONDecodeError:
                    return True, file_name
            else:
                if attempt < retry_count:
                    time.sleep(1)
                    continue
                return False, f"HTTP {status_code}"

        except Exception as e:
            if attempt < retry_count:
                time.sleep(1)
                continue
            return False, str(e)

    return False, "Max retries exceeded"


def verify_upload(client: HttpClient, file_name: str) -> bool:
    try:
        accounts = get_accounts(client)
        return any(acc.get('name') == file_name for acc in accounts)
    except:
        return False


async def run_concurrent(items: List, fn: Callable, concurrency: int, client: HttpClient, quota_threshold: float) -> List:
    results = []
    for i in range(0, len(items), concurrency):
        chunk = items[i:i + concurrency]
        chunk_results = [await fn(client, item, quota_threshold) for item in chunk]
        results.extend(chunk_results)
        print(f"\r进度: {min(i + concurrency, len(items))}/{len(items)}", end='', flush=True)
    print()
    return results


async def register_accounts_maintenance(
    need_count: int,
    register_timeout: int,
    domain_index: int,
    domain_name: str,
    concurrency: int,
    client: HttpClient,
    base_url: str,
    token: str,
    proxy: str = None
) -> Tuple[int, int]:
    print(f"开始注册 {need_count} 个账号...")
    print(f"注册总时长限制: {register_timeout / 1000} 秒")

    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    max_consecutive_fails = 5
    register_interval = 1000
    start_time = time.time()
    generated_token_files = {}

    while success_count + fail_count < need_count:
        elapsed = int(time.time() - start_time)
        if elapsed >= register_timeout:
            print(f"\n[Warn] 注册总时长已达 {register_timeout / 1000} 秒，停止注册")
            break

        if consecutive_fails >= max_consecutive_fails:
            print(f"\n[Error] 连续失败 {consecutive_fails} 次，停止注册")
            break

        total_count = success_count + fail_count + 1
        remaining_time = max(0, register_timeout - elapsed)
        print(f"\n--- 注册第 {total_count}/{need_count} 次 (成功: {success_count}, 失败: {fail_count}, 剩余时间: {remaining_time / 1000}秒) ---")

        before_snap = snapshot_token_files()

        result = run_one(proxy, domain_index, domain_name)

        after_snap = snapshot_token_files()
        new_token_files = diff_token_files(before_snap, after_snap)

        if result and result.get('token') and new_token_files:
            success_count += 1
            consecutive_fails = 0
            print(f"  ✓ 注册成功，生成 {len(new_token_files)} 个 token 文件")

            for file_path in new_token_files:
                file_name = os.path.basename(file_path)
                generated_token_files[file_name] = file_path

                upload_success = False
                upload_err = ""
                for retry in range(3):
                    upload_success, upload_err = await upload_file(client, file_path)
                    if upload_success:
                        break
                    print(f"  ⚠ 上传重试 {retry + 1}/3: {file_name}")
                    await asyncio.sleep(60)

                if upload_success:
                    print(f"  ✓ 上传新账号: {file_name}")
                    try:
                        os.unlink(file_path)
                        print(f"  ✓ 已删除本地文件: {file_name}")
                        del generated_token_files[file_name]
                    except:
                        pass
                else:
                    print(f"  ✗ 上传失败: {file_name} ({upload_err})")
                    raise RuntimeError(f"上传失败，已重试3次: {file_name}")

            if result.get('email'):
                proxies = {"http": proxy, "https": proxy} if proxy else None
                if delete_mailbox(result['email'], proxies):
                    print(f"  ✓ 已删除邮箱: {result['email']}")
                else:
                    print(f"  ✗ 删除邮箱失败: {result['email']}")
        elif result and result.get('token'):
            save_result(result)
            success_count += 1
            consecutive_fails = 0
            print(f"  ✓ 注册成功")

            token_files = [f for f in os.listdir(TOKENS_DIR) if re.match(r'^token.*\.json$', f) and result.get('email', '').replace('@', '_') in f]
            for file_name in token_files:
                file_path = os.path.join(TOKENS_DIR, file_name)
                
                upload_success = False
                upload_err = ""
                for retry in range(3):
                    upload_success, upload_err = await upload_file(client, file_path)
                    if upload_success:
                        break
                    print(f"  ⚠ 上传重试 {retry + 1}/3: {file_name}")
                    await asyncio.sleep(60)
                
                if upload_success:
                    print(f"  ✓ 上传新账号: {file_name}")
                    try:
                        os.unlink(file_path)
                        print(f"  ✓ 已删除本地文件: {file_name}")
                    except:
                        pass
                else:
                    print(f"  ✗ 上传失败: {file_name} ({upload_err})")
                    raise RuntimeError(f"上传失败，已重试3次: {file_name}")

            if result.get('email'):
                proxies = {"http": proxy, "https": proxy} if proxy else None
                if delete_mailbox(result['email'], proxies):
                    print(f"  ✓ 已删除邮箱: {result['email']}")
                else:
                    print(f"  ✗ 删除邮箱失败: {result['email']}")
        else:
            fail_count += 1
            consecutive_fails += 1
            print(f"  ✗ 注册失败 (连续失败 {consecutive_fails}/{max_consecutive_fails})")

        if success_count + fail_count < need_count:
            print(f"  休息 {register_interval / 1000} 秒后继续...")
            time.sleep(register_interval / 1000)

    print(f"\n注册完成: 成功 {success_count} 个, 失败 {fail_count} 个")

    pending = [p for f, p in generated_token_files.items() if os.path.exists(p)]
    if pending:
        print(f"\n检测到 {len(pending)} 个未上传的 token 文件，开始补传...")
        for file_path in pending:
            file_name = os.path.basename(file_path)
            upload_success, _ = await upload_file(client, file_path)
            if upload_success:
                print(f"  ✓ 补传成功: {file_name}")
                try:
                    os.unlink(file_path)
                    print(f"  ✓ 已删除本地文件: {file_name}")
                except:
                    pass
            else:
                print(f"  ✗ 补传失败: {file_name}")

    return success_count, fail_count


async def check_and_clean_accounts(client: HttpClient, quota_threshold: float, concurrency: int) -> int:
    print('\n--- 检测账号状态 ---')

    accounts = get_accounts(client)
    print(f"  原始账号数据: {accounts[:2] if accounts else '空'}...")  # 调试
    if not accounts:
        print("获取账号列表失败")
        return 0

    codex_accounts = [acc for acc in accounts if acc.get('provider') == 'codex']
    print(f"当前 codex 账号: {len(codex_accounts)} 个")

    if not codex_accounts:
        return 0

    check_results = await run_concurrent(codex_accounts, check_account, concurrency, client, quota_threshold)

    invalid_401 = [r for r in check_results if r.get('invalid_401')]
    low_quota = [r for r in check_results if r.get('low_quota')]
    ok = [r for r in check_results if not r.get('invalid_401') and not r.get('low_quota') and not r.get('error')]

    print(f"  - 401 失效: {len(invalid_401)} 个")
    print(f"  - 额度不足: {len(low_quota)} 个")
    print(f"  - 正常: {len(ok)} 个")

    to_delete = []

    for acc in invalid_401:
        to_delete.append({'name': acc['name'], 'reason': '401'})

    for acc in low_quota:
        if not any(d['name'] == acc['name'] for d in to_delete):
            remain = acc.get('remaining_percent')
            remain_str = f"{round(remain * 10) / 10}%" if remain is not None else 'unknown'
            to_delete.append({'name': acc['name'], 'reason': f'quota<{quota_threshold}% (remain={remain_str})'})

    print(f"\n删除 {len(to_delete)} 个失效账号...")
    for acc in to_delete:
        try:
            if delete_account(client, acc['name']):
                print(f"  ✓ 删除: {acc['name']} ({acc['reason']})")
            else:
                print(f"  ✗ 删除失败: {acc['name']}")
        except Exception as e:
            print(f"  ✗ 删除异常: {acc['name']} - {e}")

    return len(ok)


async def main():
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本 V5 - 集成版")
    parser.add_argument("--proxy", default=None, help="代理地址")
    parser.add_argument("--domain-index", type=int, default=DEFAULT_DOMAIN_INDEX, help="邮箱域名索引")
    parser.add_argument("--domain", default=None, help="指定邮箱域名（如 mail.example.com），优先级高于 --domain-index")
    parser.add_argument("--mail-url", default=DEFAULT_MAILFREE_BASE, help="邮箱服务地址（默认 https://mailfree.smanx.xx.kg）")
    parser.add_argument("--mail-token", default=DEFAULT_JWT_TOKEN, help="邮箱服务授权令牌（默认 auto）")
    parser.add_argument("--count", type=int, default=1, help="注册数量")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=30, help="循环模式最长等待秒数")
    parser.add_argument("--save-account", action="store_true", help="保存账号密码")
    parser.add_argument("--target-url", default=DEFAULT_TARGET_BASE_URL, help="目标服务器地址（账号管理服务）")
    parser.add_argument("--target-token", default=DEFAULT_TARGET_TOKEN, help="目标服务器认证令牌")
    parser.add_argument("--min-accounts", type=int, default=DEFAULT_MIN_ACCOUNTS, help="账号数量阈值")
    parser.add_argument("--quota-threshold", type=float, default=DEFAULT_QUOTA_THRESHOLD_PERCENT, help="额度不足删除阈值")
    parser.add_argument("--register-timeout", type=int, default=DEFAULT_REGISTER_TIMEOUT, help="注册循环总时长限制(秒)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发数")
    parser.add_argument("--register-count", type=int, default=1, help="注册模式: 0=补充不足数量, 1=默认注册1个, N=批量注册N个")
    parser.add_argument("--notify-url", default=NOTIFY_BASE_URL, help="通知服务地址")
    parser.add_argument("--mode", choices=["register", "maintenance", "both"], default="both", help="运行模式")

    args = parser.parse_args()

    log.info("=" * 60)
    log.info(" OpenAI 自动注册 V5 - 集成版")
    log.info("=" * 60)

    if not HAS_SENTINEL_POW:
        log.warning("⚠️ sentinel_pow 模块未安装，将使用空 PoW（可能被风控）")

    global MAILFREE_BASE, JWT_TOKEN
    MAILFREE_BASE = args.mail_url
    JWT_TOKEN = args.mail_token

    base_url = args.target_url or os.environ.get("TARGET_URL", DEFAULT_TARGET_BASE_URL)
    token = args.target_token or os.environ.get("TARGET_TOKEN", DEFAULT_TARGET_TOKEN)
    client = HttpClient(base_url, token) if base_url and token else None

    if base_url and token:
        if args.mode == "register":
            args.mode = "both"
            log.info("[*] 检测到 --target-url 和 --target-token，自动启用维护功能")

    if args.mode == "maintenance" or args.mode == "both":
        if not client:
            log.error("[Error] 维护模式需要 --target-url 和 --target-token")
            if args.mode == "maintenance":
                return
            else:
                log.warning("[*] 缺少 --target-token，仅执行注册")
                pass
        else:
            register_count = args.register_count
            sleep_duration = 60 * 1000
            round_num = 0
            ever_registered = False

            while True:
                round_num += 1
                print('\n' + '=' * 60)
                print(f"第 {round_num} 轮维护")
                print('=' * 60)

                need_count = 0
                valid_count = 0

                if register_count == 1:
                    print(f"\n默认模式: 先检测清理，再注册 1 个账号")
                    valid_count = await check_and_clean_accounts(client, args.quota_threshold, args.concurrency)
                    print(f"\n当前有效 codex 账号: {valid_count} 个，阈值: {args.min_accounts}")
                    need_count = 1
                elif register_count == 0:
                    valid_count = await check_and_clean_accounts(client, args.quota_threshold, args.concurrency)
                    print(f"\n当前有效 codex 账号: {valid_count} 个，阈值: {args.min_accounts}")
                    need_count = args.min_accounts - valid_count
                    if need_count <= 0:
                        print(f"\n账号充足 (>= {args.min_accounts})，无需补充")
                        if ever_registered:
                            print("曾注册过账号，维护结束")
                            print('\n' + '=' * 60)
                            print('维护完成')
                            print('=' * 60)
                            break
                        print(f"从未注册过账号，{sleep_duration / 1000} 秒后继续检测...")
                        time.sleep(sleep_duration / 1000)
                        continue
                    print(f"\n需要补充 {need_count} 个账号...")
                else:
                    need_count = register_count
                    print(f"\n批量注册模式: 本轮维护注册 {need_count} 个账号")

                success_count, fail_count = await register_accounts_maintenance(
                    need_count,
                    args.register_timeout * 1000,
                    args.domain_index,
                    args.domain,
                    args.concurrency,
                    client,
                    base_url,
                    token,
                    args.proxy
                )

                ever_registered = True

                print('\n' + '=' * 60)
                print('维护完成')
                print('=' * 60)
                print(f"成功: {success_count} 个, 失败: {fail_count} 个")
                break

    if args.mode == "register":
        total = args.count
        workers = min(args.workers, total)
        stats = {"ok": 0, "fail": 0}
        lock = threading.Lock()

        async def do_one_async(idx, delay=0):
            if delay > 0:
                await asyncio.sleep(delay)

            start_t = time.time()
            log.info(f"\n{'─'*50}")
            log.info(f"[{idx}/{total}] 开始注册...")
            log.info(f"{'─'*50}")

            result = run_one(args.proxy, args.domain_index, args.domain)

            if result and result.get("token"):
                save_result(result)
                if client:
                    for f in scan_token_files():
                        file_path = os.path.join(TOKENS_DIR, f)
                        upload_success, _ = await upload_file(client, file_path)
                        if upload_success:
                            log.info(f"  ✓ 上传: {f}")
                            try:
                                os.unlink(file_path)
                            except:
                                pass
                with lock:
                    stats["ok"] += 1
                elapsed = round(time.time() - start_t, 1)
                log.info(f"  ✅ 成功 ({elapsed}s)")
            else:
                with lock:
                    stats["fail"] += 1
                log.info(f"  ❌ 失败")

            if result and result.get("email"):
                proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
                delete_mailbox(result["email"], proxies)

        if workers <= 1:
            for i in range(1, total + 1):
                await do_one_async(i)
                if i < total:
                    wait = random.randint(args.sleep_min, args.sleep_max)
                    log.info(f"[*] 休息 {wait} 秒...")
                    time.sleep(wait)
        else:
            async def run_all():
                tasks = []
                for i in range(1, total + 1):
                    wave_pos = (i - 1) % workers
                    delay = wave_pos * random.uniform(1.0, 2.5) if wave_pos > 0 else 0
                    tasks.append(do_one_async(i, delay))
                await asyncio.gather(*tasks)
            asyncio.run(run_all())

        log.info(f"\n{'='*60}")
        log.info(f"  注册完成")
        log.info(f"{'='*60}")
        log.info(f"  ✅ 成功: {stats['ok']}")
        log.info(f"  ❌ 失败: {stats['fail']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
