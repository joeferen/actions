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
  --cf-api-token      Cloudflare API Token；传全 CF 参数后，脚本启动时会先执行子域轮换
  --cf-worker-name    Cloudflare Worker 名称，用于读取/更新 MAIL_DOMAIN 环境变量
  --cf-base-domain    Cloudflare 主域名，如 example.com
  --mail-url          邮箱服务地址（默认 https://mailfree.smanx.xx.kg）
  --mail-token        邮箱服务授权令牌（默认 auto）
  --timeout           脚本最大运行时长（秒，默认 18000）
  --domain-ready-timeout Cloudflare 新域名等待邮箱服务生效超时（秒，默认 10）
  --domain-ready-interval Cloudflare 新域名生效轮询间隔（秒，默认 5）
  --worker-verify-timeout Worker 环境变量更新校验超时（秒，默认 5）
  --worker-verify-interval Worker 环境变量更新校验间隔（秒，默认 2）

Cloudflare 子域轮换说明：
  1. 至少需要 --cf-api-token / --cf-worker-name / --cf-base-domain
  2. 脚本第一步会打印当前 Cloudflare 主域名下的子域列表
  3. 从 Worker 配置中读取 MAIL_DOMAIN，并按逗号分隔取第一个域名
  4. 若第一个域名存在于当前子域列表，则删除该子域对应 DNS 记录
  5. 从当前主域名下已存在的邮箱子域中挑选一个样板子域，复制其 MX/TXT 等 DNS 到新子域
  6. 全流程仅使用标准 DNS Records API，不再依赖 Email Routing 专用接口权限
  7. DNS 复制成功后，将新子域写回 Worker 的 MAIL_DOMAIN 第一位，并作为本次运行的 --domain

Cloudflare API Token 最小权限建议：
  - Zone / Zone / Read
  - Zone / DNS / Edit
  - Zone / Email Routing / Edit
  - Account / Workers Scripts / Edit
  - Token 资源范围建议仅限目标 Zone 和目标 Account

注册模式参数：
  --count             注册数量（默认 1）
  --workers           并发线程数（默认 1）
  --sleep-min         循环模式最短等待秒数（默认 5）
  --sleep-max         循环模式最长等待秒数（默认 30）
  --register-interval 注册间隔时间（秒，默认 60）

维护模式参数：
  --min-accounts      账号数量阈值（默认 100）
  --quota-threshold   额度不足删除阈值百分比（默认 20）
  --concurrency       检测账号并发数（默认 50）
  --maintenance-interval 账号充足时的维护检测间隔（秒，默认 60）
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
python openai_register_v5.py --mode maintenance --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100

# 6. 综合模式 - 同时支持注册和维护
python openai_register_v5.py --mode both --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100

# 7. 维护模式 - 指定每次注册数量
python openai_register_v5.py --mode maintenance --target-url https://api.example.com --target-token YOUR_TOKEN --min-accounts 100 --count 5

# 8. 使用环境变量设置目标服务器地址和令牌
set TARGET_URL=https://api.example.com
set TARGET_TOKEN=your_token
python openai_register_v5.py --mode maintenance --min-accounts 100

# 9. 自定义参数
python openai_register_v5.py --mode both --target-url https://api.example.com --target-token YOUR_TOKEN --quota-threshold 15 --timeout 3600 --concurrency 30

# 10. 维护模式 - 自定义检测间隔
python openai_register_v5.py --mode maintenance --min-accounts 50 --maintenance-interval 120

# 11. 启用 Cloudflare 子域轮换后再继续注册
python openai_register_v5.py --mode both --cf-api-token YOUR_CF_TOKEN --cf-worker-name YOUR_WORKER_NAME --cf-base-domain example.com

# 12. 自定义 Cloudflare 域名生效和 Worker 校验等待时间
python openai_register_v5.py --mode both --cf-api-token YOUR_CF_TOKEN --cf-worker-name YOUR_WORKER_NAME --cf-base-domain example.com --domain-ready-timeout 300 --domain-ready-interval 10 --worker-verify-timeout 60 --worker-verify-interval 3

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
    from curl_cffi import CurlMime, requests
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


# ===== FlowState 状态机 (codex-console 风格) =====
FLOW_STATE_SIGNATURES = {
    "login_email_entry": ["email_entry", "Email Address Entry"],
    "login_password": ["login_password", "Password Entry"],
    "login_otp_email": ["email_otp_verification", "Email OTP"],
    "signup_email_entry": ["signup_email_entry", "Signup Email Entry"],
    "signup_password": ["signup_password", "Create Password"],
    "signup_otp_email": ["email_otp_verification", "Email Verification"],
    "add_phone": ["add_phone", "Add Phone Number"],
    "phone_verification": ["phone_verification", "Phone Verification"],
    "interstitial_consent": ["interstitial_consent", "Interstitial Consent"],
    "chatgpt_onboarding": ["chatgpt_onboarding", "ChatGPT Onboarding"],
    "idle": ["idle", "Idle"],
}

def _extract_state_signature(page_type: str) -> str:
    for sig, types in FLOW_STATE_SIGNATURES.items():
        if page_type in types:
            return sig
    return page_type

def _state_description(page_type: str) -> str:
    sig = _extract_state_signature(page_type)
    for key, descs in FLOW_STATE_SIGNATURES.items():
        if sig == key and len(descs) > 1:
            return descs[1]
    return page_type

def _is_critical_state(page_type: str) -> bool:
    critical = {"add_phone", "phone_verification", "interstitial_consent", "chatgpt_onboarding"}
    return page_type in critical

def _should_retry_state(page_type: str) -> bool:
    non_retry = {"idle", "interstitial_consent", "chatgpt_onboarding"}
    return page_type not in non_retry


def out(message: str, prefix: Optional[str] = None, ts: bool = False, indent: int = 0, flush: bool = True) -> None:
    parts: List[str] = []
    if ts:
        parts.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if prefix:
        parts.append(prefix)
    text = " ".join(parts + [str(message)]) if parts else str(message)
    if indent > 0:
        text = ("  " * indent) + text
    print(text, flush=flush)


def section(title: str) -> None:
    line = '=' * 60
    out(f"\n{line}", flush=True)
    out(title, flush=True)
    out(line, flush=True)

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
CF_API_BASE = "https://api.cloudflare.com/client/v4"
FATAL_REGISTRATION_ERRORS = [
    "未找到 workspace_id",
    "选择 workspace 失败",
    "未获取到 continue_url",
    "重定向失败，未获取到回调 URL",
]

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
    return ",".join(parts)


def _match_fatal_registration_error(error_text: str) -> Optional[str]:
    text = str(error_text or "")
    for item in FATAL_REGISTRATION_ERRORS:
        if item in text:
            return item
    return None


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


def _cf_headers(api_token: str) -> Dict[str, str]:
    token = str(api_token or "").strip()
    if not token:
        raise RuntimeError("未提供有效的 Cloudflare API Token，请使用 --cf-api-token")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _cf_request(
    method: str,
    path: str,
    api_token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    resp = requests.request(
        method,
        f"{CF_API_BASE}{path}",
        headers=_cf_headers(api_token),
        params=params,
        json=payload,
        impersonate="chrome",
        timeout=timeout,
    )
    try:
        data = resp.json()
    except Exception:
        preview = (resp.text or "")[:300]
        raise RuntimeError(f"Cloudflare API 响应异常: HTTP {resp.status_code} {preview}")
    if resp.status_code < 200 or resp.status_code >= 300 or not data.get("success", False):
        errors = data.get("errors") or []
        err_msg = "; ".join(str(x.get("message") or x) for x in errors) if errors else (resp.text or "")[:300]
        raise RuntimeError(f"Cloudflare API 调用失败: HTTP {resp.status_code} {err_msg}")
    return data


def _cf_find_zone(base_domain: str, api_token: str) -> Dict[str, Any]:
    data = _cf_request(
        "GET",
        "/zones",
        api_token,
        params={"name": base_domain, "per_page": 50},
    )
    zones = data.get("result") or []
    normalized = base_domain.strip().lower()
    exact = [z for z in zones if str(z.get("name") or "").strip().lower() == normalized]
    if not exact:
        raise RuntimeError(f"未找到主域名对应的 Cloudflare Zone: {base_domain}")
    if len(exact) > 1:
        raise RuntimeError(f"找到多个同名 Zone，无法自动确定: {base_domain}")
    return exact[0]


def _parse_mail_domain_list(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _random_subdomain_prefix(length: int = 12) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "oc" + "".join(secrets.choice(alphabet) for _ in range(max(4, length)))


def _cf_list_dns_records(zone_id: str, api_token: str, per_page: int = 1000) -> List[Dict[str, Any]]:
    page = 1
    all_records: List[Dict[str, Any]] = []
    while True:
        data = _cf_request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            api_token,
            params={"page": page, "per_page": per_page},
        )
        all_records.extend(data.get("result") or [])
        result_info = data.get("result_info") or {}
        total_pages = int(result_info.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    return all_records


def _cf_print_subdomains(zone_id: str, base_domain: str, api_token: str) -> List[str]:
    records = _cf_list_dns_records(zone_id, api_token)
    suffix = f".{base_domain.strip('.').lower()}"
    names = sorted({
        str(record.get("name") or "").lower()
        for record in records
        if str(record.get("name") or "").lower().endswith(suffix)
        and str(record.get("name") or "").lower() != base_domain.strip('.').lower()
    })
    out("当前子域列表:", prefix="[CF]")
    if names:
        for item in names:
            out(f"- {item}", indent=1)
    else:
        out("- (空)", indent=1)
    return names


def _cf_get_binding_text(settings: Dict[str, Any], binding_name: str) -> str:
    bindings = settings.get("bindings") or []
    binding = next((b for b in bindings if b.get("name") == binding_name), None)
    return str((binding or {}).get("text") or "").strip()


def _cf_build_worker_settings_payload(settings: Dict[str, Any], plain_text_updates: Dict[str, str]) -> Dict[str, Any]:
    bindings = list(settings.get("bindings") or [])
    for name, value in plain_text_updates.items():
        updated = False
        for binding in bindings:
            if binding.get("name") == name:
                binding["text"] = value
                updated = True
                break
        if not updated:
            bindings.append({"type": "plain_text", "name": name, "text": value})

    payload: Dict[str, Any] = {"bindings": bindings}
    for key in (
        "compatibility_date",
        "compatibility_flags",
        "usage_model",
        "limits",
        "placement",
        "tail_consumers",
        "logpush",
        "observability",
    ):
        if key in settings:
            payload[key] = settings[key]
    return payload


def _cf_patch_worker_settings_multipart(account_id: str, worker_name: str, payload: Dict[str, Any], api_token: str) -> None:
    mime = CurlMime()
    mime.addpart(
        "settings",
        content_type="application/json",
        filename="settings.json",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    resp = requests.request(
        "PATCH",
        f"{CF_API_BASE}/accounts/{account_id}/workers/scripts/{worker_name}/settings",
        headers={"Authorization": f"Bearer {api_token}"},
        multipart=mime,
        impersonate="chrome",
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception:
        preview = (resp.text or "")[:300]
        raise RuntimeError(f"Cloudflare Worker 设置更新响应异常: HTTP {resp.status_code} {preview}")
    if resp.status_code < 200 or resp.status_code >= 300 or not data.get("success", False):
        errors = data.get("errors") or []
        err_msg = "; ".join(str(x.get("message") or x) for x in errors) if errors else (resp.text or "")[:300]
        raise RuntimeError(f"Cloudflare Worker 设置更新失败: HTTP {resp.status_code} {err_msg}")


def _cf_wait_for_worker_binding(account_id: str, worker_name: str, binding_name: str, expected_value: str, api_token: str, timeout: int = 30, interval: int = 2) -> None:
    start = time.time()
    verify_value = ""
    while time.time() - start < timeout:
        verify = _cf_get_worker_settings(account_id, worker_name, api_token)
        verify_value = _cf_get_binding_text(verify, binding_name)
        if verify_value == expected_value:
            return
        time.sleep(interval)
    raise RuntimeError(f"Worker {binding_name} 校验失败，当前值仍为: {verify_value or '(空)'}")


def _cf_delete_domain_dns(zone_id: str, full_domain: str, api_token: str) -> int:
    records = _cf_list_dns_records(zone_id, api_token)
    target = full_domain.strip().lower()
    matched = [record for record in records if str(record.get("name") or "").lower() == target]
    for record in matched:
        _cf_request(
            "DELETE",
            f"/zones/{zone_id}/dns_records/{record['id']}",
            api_token,
        )
    return len(matched)


def _cf_create_dns_record(zone_id: str, record: Dict[str, Any], api_token: str) -> None:
    payload: Dict[str, Any] = {
        "type": record.get("type"),
        "name": record.get("name"),
        "content": record.get("content"),
    }
    if record.get("ttl") is not None:
        payload["ttl"] = record.get("ttl")
    if record.get("priority") is not None:
        payload["priority"] = record.get("priority")
    if record.get("proxied") is not None:
        payload["proxied"] = record.get("proxied")

    _cf_request(
        "POST",
        f"/zones/{zone_id}/dns_records",
        api_token,
        payload=payload,
    )


def _cf_clone_domain_dns(zone_id: str, source_domain: str, target_domain: str, api_token: str) -> int:
    existing_records = _cf_list_dns_records(zone_id, api_token)
    existing_keys = {
        (
            str(item.get("type") or "").upper(),
            str(item.get("name") or "").lower(),
            str(item.get("content") or "").strip().lower(),
            int(item.get("priority") or 0),
        )
        for item in existing_records
    }

    source_lower = source_domain.strip().lower()
    target_lower = target_domain.strip().lower()
    source_records = []
    for item in existing_records:
        name = str(item.get("name") or "").strip().lower()
        if name != source_lower:
            continue
        record_type = str(item.get("type") or "").upper()
        if record_type not in {"MX", "TXT"}:
            continue
        source_records.append(item)

    if not source_records:
        raise RuntimeError(f"未找到可复制的样板 DNS 记录: {source_domain}")

    created = 0
    for raw_record in source_records:
        record = {
            "type": raw_record.get("type"),
            "name": target_lower,
            "content": raw_record.get("content"),
            "ttl": raw_record.get("ttl"),
            "priority": raw_record.get("priority"),
            "proxied": raw_record.get("proxied"),
        }
        key = (
            str(record.get("type") or "").upper(),
            str(record.get("name") or "").lower(),
            str(record.get("content") or "").strip().lower(),
            int(record.get("priority") or 0),
        )
        if key in existing_keys:
            out(f"已存在 DNS，跳过: {record.get('type')} {record.get('name')} -> {record.get('content')}", prefix="[CF]")
            continue
        _cf_create_dns_record(zone_id, record, api_token)
        created += 1
        out(f"已创建 DNS: {record.get('type')} {record.get('name')} -> {record.get('content')}", prefix="[CF]")
    return created


def _cf_get_worker_settings(account_id: str, worker_name: str, api_token: str) -> Dict[str, Any]:
    data = _cf_request(
        "GET",
        f"/accounts/{account_id}/workers/scripts/{worker_name}/settings",
        api_token,
    )
    return data.get("result") or {}


def _cf_update_worker_mail_domain(account_id: str, worker_name: str, new_value: str, api_token: str, verify_timeout: int = 30, verify_interval: int = 2) -> None:
    settings = _cf_get_worker_settings(account_id, worker_name, api_token)
    payload = _cf_build_worker_settings_payload(settings, {"MAIL_DOMAIN": new_value})
    _cf_patch_worker_settings_multipart(account_id, worker_name, payload, api_token)
    _cf_wait_for_worker_binding(account_id, worker_name, "MAIL_DOMAIN", new_value, api_token, timeout=verify_timeout, interval=verify_interval)


def _prepare_cloudflare_mail_domain(args: Any) -> Optional[str]:
    cf_token = str(args.cf_api_token or "").strip()

    required = [
        args.cf_worker_name,
        args.cf_base_domain,
    ]
    if not cf_token or not all(required):
        out("Cloudflare 邮箱域名配置参数不完整，跳过 CF 邮箱配置", prefix="[CF]")
        return None

    try:
        zone = _cf_find_zone(args.cf_base_domain, cf_token)
        zone_id = str(zone.get("id") or "").strip()
        if not zone_id:
            raise RuntimeError("自动查询 Zone ID 失败")
        out(f"自动获取 Zone ID 成功: {zone_id}", prefix="[CF]")

        account_id = str((zone.get("account") or {}).get("id") or "").strip()
        if not account_id:
            raise RuntimeError("自动查询 Account ID 失败")
        out(f"自动获取 Account ID 成功: {account_id}", prefix="[CF]")

        subdomains = _cf_print_subdomains(zone_id, args.cf_base_domain, cf_token)
        settings = _cf_get_worker_settings(account_id, args.cf_worker_name, cf_token)
        bindings = settings.get("bindings") or []
        mail_domain_binding = next((b for b in bindings if b.get("name") == "MAIL_DOMAIN"), None)
        if not mail_domain_binding:
            raise RuntimeError("Worker 中未找到 MAIL_DOMAIN 环境变量")

        current_mail_domain = str(mail_domain_binding.get("text") or "").strip()
        if not current_mail_domain:
            raise RuntimeError("Worker 的 MAIL_DOMAIN 为空，无法执行轮换")

        domains = _parse_mail_domain_list(current_mail_domain)
        if not domains:
            raise RuntimeError("MAIL_DOMAIN 中没有可用域名")

        first_domain = domains[0]
        out(f"当前 Worker MAIL_DOMAIN: {current_mail_domain}", prefix="[CF]")
        out(f"MAIL_DOMAIN 首个域名: {first_domain}", prefix="[CF]")

        clone_source = next((item for item in domains if item.lower().endswith(f".{args.cf_base_domain.strip('.').lower()}") and item.lower() in set(subdomains)), None)
        if not clone_source:
            clone_source = next((item for item in subdomains if not item.startswith("_") and not item.split(".", 1)[0].startswith("cf2024-")), None)
        if not clone_source:
            raise RuntimeError("未找到可用于复制 DNS 的样板子域")
        out(f"DNS 样板子域: {clone_source}", prefix="[CF]")

        new_prefix = _random_subdomain_prefix()
        new_domain = f"{new_prefix}.{args.cf_base_domain.strip('.')}"
        out(f"开始复制样板 DNS 到新子域: {new_domain}", prefix="[CF]")
        created_count = _cf_clone_domain_dns(zone_id, clone_source, new_domain, cf_token)
        out(f"新子域 DNS 复制完成: {new_domain} (新建 {created_count} 条)", prefix="[CF]")

        base_suffix = f".{args.cf_base_domain.strip('.').lower()}"
        if first_domain.lower().endswith(base_suffix):
            if first_domain.lower() in set(subdomains):
                deleted_count = _cf_delete_domain_dns(zone_id, first_domain, cf_token)
                out(f"已删除被替换旧子域 DNS: {first_domain} ({deleted_count} 条)", prefix="[CF]")
            updated_domains = [new_domain] + domains[1:]
        else:
            updated_domains = [new_domain] + domains
        updated_mail_domain = ",".join(updated_domains)
        try:
            out("开始更新 Worker MAIL_DOMAIN", prefix="[CF]")
            _cf_update_worker_mail_domain(
                account_id,
                args.cf_worker_name,
                updated_mail_domain,
                cf_token,
                verify_timeout=max(1, int(args.worker_verify_timeout)),
                verify_interval=max(1, int(args.worker_verify_interval)),
            )
            out(f"已更新 Worker MAIL_DOMAIN: {updated_mail_domain}", prefix="[CF]")
            out("Worker 配置已完成更新并通过校验", prefix="[CF]")
        except Exception as worker_err:
            out(f"Worker MAIL_DOMAIN 更新失败，请手动更新为: {updated_mail_domain}", prefix="[Warn] [CF]")
            out(f"Worker 更新错误: {worker_err}", prefix="[Warn] [CF]")

        domain_ready_timeout = max(1, int(args.domain_ready_timeout))
        domain_ready_interval = max(1, int(args.domain_ready_interval))
        out(f"等待邮箱服务识别新域名: {new_domain} (超时 {domain_ready_timeout}s, 间隔 {domain_ready_interval}s)", prefix="[MAIL]")
        if not wait_for_domain_available(new_domain, timeout=domain_ready_timeout, interval=domain_ready_interval):
            raise RuntimeError(f"邮箱服务在 {domain_ready_timeout} 秒内仍未识别新域名: {new_domain}")
        out(f"邮箱服务已识别新域名: {new_domain}", prefix="[MAIL]")
        return new_domain
    except Exception as e:
        out(f"Cloudflare 邮箱配置失败，跳过本步骤: {e}", prefix="[Warn] [CF]")
        return None


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


def wait_for_domain_available(domain_name: str, proxies: Any = None, timeout: int = 180, interval: int = 5) -> bool:
    start = time.time()
    target = str(domain_name or "").strip().lower()
    while time.time() - start < timeout:
        try:
            domains = get_domains(proxies)
            if any(str(item).strip().lower() == target for item in domains):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


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
    try:
        resp = requests.delete(
            f"{MAILFREE_BASE}/api/mailboxes",
            headers=_mailfree_headers(),
            params={"address": address},
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


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
    flow_name: str = "通用流程",
) -> str:
    regex = r"(?<!\d)(\d{6})(?!\d)"
    used: set = set(used_codes or set())
    seen_ids: set = set()
    start = time.time()
    last_resend = 0.0
    intervals = [3, 4, 5, 6, 8, 10]
    idx = 0

    log.info(f"    📧 [{flow_name}] 等待验证码 ({email})...")

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
                                log.info(f"    ✅ [{flow_name}] 验证码: {code} (耗时 {elapsed}s)")
                                return code

        except Exception as e:
            log.warning(f"    MailAPI 查询失败: {e}")

        elapsed_now = time.time() - start
        if resend_fn and elapsed_now > 20 and (elapsed_now - last_resend) > OTP_RESEND_INTERVAL:
            try:
                resend_fn()
                last_resend = elapsed_now
                log.info(f"    🔄 [{flow_name}] 已重发 OTP")
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

    try:
        token = resp.json()["token"]
    except Exception as e:
        raise RuntimeError(f"Sentinel 解析失败: {resp.text[:200]}, error: {e}")
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


# ===== ChatGPTClient 注册状态机 (codex-console 风格) =====
class ChatGPTClient:
    SESSION_URL = "https://auth.openai.com/api/auth/session"
    AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize/continue"
    REGISTER_URL = "https://auth.openai.com/api/accounts/user/register"
    EMAIL_OTP_SEND_URL = "https://auth.openai.com/api/accounts/email-otp/send"
    EMAIL_OTP_VALIDATE_URL = "https://auth.openai.com/api/accounts/email-otp/validate"
    EMAIL_OTP_RESEND_URL = "https://auth.openai.com/api/accounts/email-otp/resend"
    PASSWORD_VERIFY_URL = "https://auth.openai.com/api/accounts/password/verify"
    WORKSPACE_SELECT_URL = "https://auth.openai.com/api/accounts/workspace/select"
    CREATE_ACCOUNT_URL = "https://auth.openai.com/api/accounts/create_account"

    def __init__(self, proxies: Any = None, profile: Optional[str] = None, lang: Optional[str] = None):
        self.proxies = proxies
        self.profile = profile or pick_browser_profile()[0]
        self.lang = lang or pick_browser_profile()[1]
        self._session = None
        self._device_id: Optional[str] = None
        self._oauth: Optional[OAuthStart] = None
        self._last_response_data: Dict[str, Any] = {}
        self._token_json: Optional[str] = None
        self._email: Optional[str] = None
        self._password: Optional[str] = None

    def _build_session(self) -> requests.Session:
        session = requests.Session(
            proxies=self.proxies,
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
        session.headers.update(base_headers)
        return session

    def _init_session(self) -> None:
        if self._session is None:
            self._session = self._build_session()

    @property
    def session(self) -> requests.Session:
        self._init_session()
        return self._session

    @property
    def device_id(self) -> str:
        if not self._device_id:
            self._device_id = self.session.cookies.get("oai-did") or ""
        return self._device_id

    @property
    def user_agent(self) -> str:
        return self.session.headers.get("User-Agent", "Mozilla/5.0")

    def _sentinel_headers(self, flow: str = "authorize_continue") -> Dict[str, str]:
        sentinel_body = build_sentinel_token(self.device_id, self.user_agent, flow)
        resp = requests.post(
            SENTINEL_URL,
            data=sentinel_body,
            headers={
                "Origin": "https://sentinel.openai.com",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "Content-Type": "text/plain;charset=UTF-8",
            },
            proxies=self.proxies,
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"Sentinel 失败: {resp.status_code}")
        try:
            token = resp.json()["token"]
        except Exception as e:
            raise RuntimeError(f"Sentinel 解析失败: {resp.text[:200]}, error: {e}")
        return {
            "openai-sentinel-token": json.dumps({
                "p": "", "t": "", "c": token,
                "id": self.device_id, "flow": flow,
            }),
        }

    def _continue_authorize(self, extra_data: Dict[str, Any], screen_hint: str = "signup") -> Dict[str, Any]:
        data = {"username": {"value": self._email, "kind": "email"}, "screen_hint": screen_hint}
        data.update(extra_data)
        headers = {
            "Referer": "https://auth.openai.com/create-account",
            **self._sentinel_headers("authorize_continue"),
        }
        resp = self.session.post(self.AUTHORIZE_URL, json=data, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"authorize 失败: {resp.status_code}, resp: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"authorize 解析失败: {resp.text[:200]}, error: {e}")

    def _send_otp(self) -> None:
        headers = {"Referer": "https://auth.openai.com/create-account/password"}
        resp = self.session.post(self.EMAIL_OTP_SEND_URL, json={}, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"发送 OTP 失败: {resp.status_code}")

    def _validate_otp(self, code: str) -> Dict[str, Any]:
        headers = {
            "Referer": "https://auth.openai.com/email-verification",
            **self._sentinel_headers("email_otp_validate"),
        }
        resp = self.session.post(self.EMAIL_OTP_VALIDATE_URL, json={"code": code}, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"验证 OTP 失败: {resp.status_code}")
        return resp.json()

    def _verify_password(self, password: str) -> Dict[str, Any]:
        headers = {
            "Referer": "https://auth.openai.com/log-in/password",
            **self._sentinel_headers("password_verify"),
        }
        resp = self.session.post(self.PASSWORD_VERIFY_URL, json={"password": password}, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"验证密码失败: {resp.status_code}")
        return resp.json()

    def _create_account(self, name: str, birthday: str) -> Dict[str, Any]:
        headers = {"Referer": "https://auth.openai.com/about-you"}
        resp = self.session.post(self.CREATE_ACCOUNT_URL, json={"name": name, "birthdate": birthday}, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"创建账号失败: {resp.status_code}")
        return resp.json()

    def _select_workspace(self, workspace_id: str) -> Dict[str, Any]:
        headers = {"Referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"}
        resp = self.session.post(self.WORKSPACE_SELECT_URL, json={"workspace_id": workspace_id}, headers=headers, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"选择 workspace 失败: {resp.status_code}")
        return resp.json()

    def _get_session_token(self) -> Optional[str]:
        try:
            resp = self.session.get(self.SESSION_URL, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("access_token")
        except Exception:
            pass
        return None

    def _continue_oauth(self) -> str:
        self._oauth = generate_oauth_url()
        self.session.get(self._oauth.auth_url, timeout=30)
        return self.session.cookies.get("oai-did") or ""

    def _follow_redirects(self, url: str, max_hops: int = 12) -> Optional[str]:
        current = url
        for _ in range(max_hops):
            resp = self.session.get(current, allow_redirects=False, timeout=30)
            location = resp.headers.get("Location")
            if not location:
                return None
            if "code=" in location:
                return location
            current = urljoin(current, location)
        return None

    def _complete_token_exchange(self) -> str:
        if not self._oauth:
            raise RuntimeError("OAuth 未初始化")
        auth_cookie = self.session.cookies.get("oai-client-auth-session")
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
        select_resp = self._select_workspace(workspace_id)
        continue_url = select_resp.get("continue_url")
        if not continue_url:
            raise RuntimeError("未获取到 continue_url")
        callback_url = self._follow_redirects(continue_url)
        if not callback_url:
            raise RuntimeError("重定向失败，未获取到回调 URL")
        self._token_json = submit_callback_url(
            callback_url=callback_url,
            code_verifier=self._oauth.code_verifier,
            redirect_uri=self._oauth.redirect_uri,
            expected_state=self._oauth.state,
        )
        return self._token_json

    def register_complete_flow(
        self,
        email: str,
        password: str,
        otp_poll_fn: Callable[[], str],
        name: Optional[str] = None,
        birthday: Optional[str] = None,
    ) -> str:
        self._email = email
        self._password = password

        self._continue_oauth()
        human_sleep("read")

        page_data = self._continue_authorize({})
        page_type = page_data.get("page", {}).get("type", "")
        is_existing = page_type == "email_otp_verification"

        human_sleep("network")

        if not is_existing:
            reg_data = self._continue_authorize({"password": password}, "signup")
            reg_type = reg_data.get("page", {}).get("type", "")
            human_sleep("click")

            if reg_type == "add_phone":
                self._continue_authorize({}, "login")
                return self._oauth_login_flow(password, otp_poll_fn)

            self._send_otp()
        else:
            self._continue_authorize({}, "login")

        otp_sent_at = time.time()
        code = otp_poll_fn()

        self._validate_otp(code)
        human_sleep("network")

        if not is_existing:
            name = name or random_name()
            birthday = birthday or random_birthday()
            create_data = self._create_account(name, birthday)
            create_type = create_data.get("page", {}).get("type", "")
            if create_type == "add_phone":
                return self._oauth_login_flow(password, otp_poll_fn)

        return self._complete_token_exchange()

    def _oauth_login_flow(self, password: str, otp_poll_fn: Callable[[], str]) -> str:
        login_data = self._continue_authorize({}, "login")
        login_type = login_data.get("page", {}).get("type", "")
        if login_type != "login_password":
            raise RuntimeError(f"未进入密码页面: {login_type}")

        pwd_data = self._verify_password(password)
        pwd_type = pwd_data.get("page", {}).get("type", "")
        if pwd_type != "email_otp_verification":
            raise RuntimeError(f"未进入验证码页面: {pwd_type}")

        code = otp_poll_fn()
        self._validate_otp(code)
        human_sleep("network")

        return self._complete_token_exchange()

    def reuse_session_and_get_tokens(self) -> Optional[str]:
        token = self._get_session_token()
        if token:
            self._token_json = json.dumps({
                "access_token": token,
                "token_type": "bearer",
            })
        return self._token_json

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None


# ===== OAuthClient 回退流程 (codex-console 风格) =====
class OAuthClient:
    def __init__(self, proxies: Any = None, profile: Optional[str] = None, lang: Optional[str] = None):
        self.proxies = proxies
        self.profile = profile or pick_browser_profile()[0]
        self.lang = lang or pick_browser_profile()[1]
        self._session: Optional[requests.Session] = None
        self._oauth: Optional[OAuthStart] = None
        self._device_id: Optional[str] = None
        self._token_json: Optional[str] = None

    def _build_session(self) -> requests.Session:
        session = requests.Session(
            proxies=self.proxies,
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
        session.headers.update(base_headers)
        return session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self._build_session()
        return self._session

    @property
    def device_id(self) -> str:
        if not self._device_id:
            self._device_id = self.session.cookies.get("oai-did") or ""
        return self._device_id

    @property
    def user_agent(self) -> str:
        return self.session.headers.get("User-Agent", "Mozilla/5.0")

    def _sentinel_headers(self, flow: str = "authorize_continue") -> Dict[str, str]:
        sentinel_body = build_sentinel_token(self.device_id, self.user_agent, flow)
        resp = requests.post(
            SENTINEL_URL,
            data=sentinel_body,
            headers={
                "Origin": "https://sentinel.openai.com",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "Content-Type": "text/plain;charset=UTF-8",
            },
            proxies=self.proxies,
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"Sentinel 失败: {resp.status_code}")
        try:
            token = resp.json()["token"]
        except Exception as e:
            raise RuntimeError(f"Sentinel 解析失败: {resp.text[:200]}, error: {e}")
        return {
            "openai-sentinel-token": json.dumps({
                "p": "", "t": "", "c": token,
                "id": self.device_id, "flow": flow,
            }),
        }

    def _follow_redirects(self, url: str, max_hops: int = 12) -> Optional[str]:
        current = url
        for _ in range(max_hops):
            resp = self.session.get(current, allow_redirects=False, timeout=30)
            location = resp.headers.get("Location")
            if not location:
                return None
            if "code=" in location:
                return location
            current = urljoin(current, location)
        return None

    def _complete_token_exchange(self) -> str:
        if not self._oauth:
            raise RuntimeError("OAuth 未初始化")
        auth_cookie = self.session.cookies.get("oai-client-auth-session")
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
        headers = {"Referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"}
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/workspace/select",
            json={"workspace_id": workspace_id},
            headers=headers,
            timeout=30,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"选择 workspace 失败: {resp.status_code}")
        continue_url = resp.json().get("continue_url")
        if not continue_url:
            raise RuntimeError("未获取到 continue_url")
        callback_url = self._follow_redirects(continue_url)
        if not callback_url:
            raise RuntimeError("重定向失败，未获取到回调 URL")
        self._token_json = submit_callback_url(
            callback_url=callback_url,
            code_verifier=self._oauth.code_verifier,
            redirect_uri=self._oauth.redirect_uri,
            expected_state=self._oauth.state,
        )
        return self._token_json

    def login_and_get_tokens(
        self,
        email: str,
        password: str,
        otp_poll_fn: Callable[[], str],
    ) -> str:
        self._oauth = generate_oauth_url()
        self.session.get(self._oauth.auth_url, timeout=30)
        human_sleep("read")

        sentinel = self._sentinel_headers("authorize_continue")
        headers = {
            "Referer": "https://auth.openai.com/log-in",
            **sentinel,
        }
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            json={"username": {"value": email, "kind": "email"}},
            headers=headers,
            timeout=30,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"提交邮箱失败: {resp.status_code}, resp: {resp.text[:200]}")

        pwd_sentinel = self._sentinel_headers("password_verify")
        headers = {
            "Referer": "https://auth.openai.com/log-in/password",
            **pwd_sentinel,
        }
        pwd_resp = self.session.post(
            "https://auth.openai.com/api/accounts/password/verify",
            json={"password": password},
            headers=headers,
            timeout=30,
        )
        if pwd_resp.status_code < 200 or pwd_resp.status_code >= 300:
            raise RuntimeError(f"验证密码失败: {pwd_resp.status_code}")

        try:
            pwd_data = pwd_resp.json()
            login_continue_url = pwd_data.get("continue_url", "")
            login_page_type = pwd_data.get("page", {}).get("type", "")
        except Exception:
            login_continue_url = ""
            login_page_type = ""

        need_login_otp = (
            "email_otp" in login_page_type
            or "email-verification" in login_continue_url
        )

        if need_login_otp:
            human_sleep("network")
            code = otp_poll_fn()
            if not code:
                raise RuntimeError("未获取到登录验证码")

            otp_sentinel = self._sentinel_headers("email_otp_validate")
            headers = {
                "Referer": "https://auth.openai.com/email-verification",
                **otp_sentinel,
            }
            otp_resp = self.session.post(
                "https://auth.openai.com/api/accounts/email-otp/validate",
                json={"code": code},
                headers=headers,
                timeout=30,
            )
            if otp_resp.status_code < 200 or otp_resp.status_code >= 300:
                raise RuntimeError(f"验证码校验失败: {otp_resp.status_code}")

        return self._complete_token_exchange()

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None


def register_account(
    email: str,
    openai_password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    codes = used_codes or set()

    client = ChatGPTClient(proxies=proxies)

    def _make_poll_fn(poll_email: str, flow_name: str):
        def _poll():
            return poll_verification_code(
                poll_email, proxies,
                used_codes=codes,
                resend_fn=None,
                otp_sent_at=None,
                flow_name=flow_name,
            )
        return _poll

    try:
        token_json = client.register_complete_flow(
            email=email,
            password=openai_password,
            otp_poll_fn=_make_poll_fn(email, "注册流程"),
        )
        return {"token": token_json}
    except Exception as e:
        str_error = str(e).lower()
        if "add_phone" in str_error or "phone" in str_error:
            oauth_client = OAuthClient(proxies=proxies)
            try:
                token_json = oauth_client.login_and_get_tokens(
                    email=email,
                    password=openai_password,
                    otp_poll_fn=_make_poll_fn(email, "登录补token"),
                )
                return {"token": token_json}
            finally:
                oauth_client.close()
        raise
    finally:
        client.close()


def _relogin_for_token(
    email: str,
    password: str,
    proxies: Any = None,
    used_codes: Optional[set] = None,
) -> dict:
    codes = used_codes or set()

    with RegSession(proxies) as s:
        oauth = generate_oauth_url()
        resp = s.get(oauth.auth_url)

        device_id = s.get_cookie("oai-did") or ""
        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)

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

        if login_page_type != "login_password":
            raise RuntimeError(f"重新登录未进入密码页面: {login_page_type}")

        human_sleep("network")

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
            raise RuntimeError(f"重新登录提交密码失败: {pwd_resp.status_code}")

        try:
            pwd_page_type = pwd_resp.json().get("page", {}).get("type", "")
        except Exception:
            pwd_page_type = ""

        if pwd_page_type != "email_otp_verification":
            raise RuntimeError(f"重新登录未进入验证码页面: {pwd_page_type}")

        otp_sent_at = time.time()

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
            flow_name="重新登录流程",
        )

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

        human_sleep("network")

        return _complete_token_exchange(s, oauth, email, "", proxies)


def _login_for_token(
    email: str,
    password: str,
    proxies: Any = None,
) -> dict:
    with RegSession(proxies) as s:
        oauth = generate_oauth_url()

        s.get(oauth.auth_url)
        device_id = s.get_cookie("oai-did") or ""

        ua = s._session.headers.get("User-Agent", "Mozilla/5.0")
        sentinel = get_sentinel_header(device_id, ua, "authorize_continue", proxies)

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

        need_login_otp = (
            "email_otp" in login_page_type
            or "email-verification" in login_continue_url
        )

        if need_login_otp:
            human_sleep("network")

            def _resend():
                r = s.post_json(
                    "https://auth.openai.com/api/accounts/email-otp/resend",
                    {},
                    headers={"Referer": "https://auth.openai.com/email-verification"},
                )
                return r.status_code >= 200 and r.status_code < 300

            code = poll_verification_code(
                email,
                proxies,
                resend_fn=_resend,
                flow_name="登录补 token 流程",
            )
            if not code:
                raise RuntimeError("未获取到登录验证码")

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

    callback_url = s.follow_redirects(continue_url)
    if not callback_url:
        raise RuntimeError("重定向失败，未获取到回调 URL")

    token_json = submit_callback_url(
        callback_url=callback_url,
        code_verifier=oauth.code_verifier,
        redirect_uri=oauth.redirect_uri,
        expected_state=oauth.state,
    )

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
        reset_mailbox_password(email, email_password, proxies=proxies)

    except Exception as e:
        log.error(f"[Error] 邮箱创建失败: {e}")
        return None

    openai_password = _generate_password()

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

        request_headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        if headers:
            request_headers.update(headers)

        try:
            if method == "GET":
                resp = requests.get(url, headers=request_headers, impersonate="chrome", timeout=30)
            elif method == "DELETE":
                resp = requests.delete(url, headers=request_headers, impersonate="chrome", timeout=30)
            else:
                resp = requests.post(url, headers=request_headers, data=body, impersonate="chrome", timeout=30)

            try:
                data = resp.json()
                return resp.status_code, data
            except:
                return resp.status_code, resp.text

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
    start_time = time.time()

    if not auth_index:
        return {
            'name': name,
            'error': 'missing auth_index',
            'invalid_401': False,
            'low_quota': False,
            'elapsed_seconds': round(time.time() - start_time, 3),
        }

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

        status, data = await asyncio.to_thread(client.request, '/v0/management/api-call', 'POST', json.dumps(payload))

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
            'error': None,
            'elapsed_seconds': round(time.time() - start_time, 3),
        }
    except Exception as e:
        return {
            'name': name,
            'error': str(e),
            'invalid_401': False,
            'low_quota': False,
            'elapsed_seconds': round(time.time() - start_time, 3),
        }


def delete_account(client: HttpClient, name: str) -> bool:
    encoded = requests.utils.quote(name)
    status, _ = client.request(f'/v0/management/auth-files?name={encoded}', 'DELETE')
    return 200 <= status < 300


async def upload_file(client: HttpClient, file_path: str) -> Tuple[bool, str]:
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
    except:
        return False, "Cannot read file"

    url = f"{client.base_url}/v0/management/auth-files?name={requests.utils.quote(file_name)}"

    attempt = 0
    while True:
        attempt += 1
        try:
            out(f"↗ 开始上传: {file_name} (尝试 {attempt})", indent=1, prefix="[UPLOAD]")
            resp = requests.post(
                url,
                headers={
                    'Authorization': f'Bearer {client.token}',
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                data=file_content.encode('utf-8'),
                impersonate="chrome",
                timeout=60,
            )
            status_code = resp.status_code
            response_body = resp.text or ""

            if 200 <= status_code < 300:
                try:
                    resp_data = json.loads(response_body) if response_body.strip() else {}
                    if resp_data.get('status') in ('ok', 'success') or not resp_data:
                        return True, file_name
                    elif resp_data.get('error'):
                        out(f"⚠ 上传响应异常: 服务端错误: {resp_data.get('error')}，60秒后重试", indent=1, prefix="[UPLOAD]")
                        time.sleep(60)
                        continue
                    else:
                        return True, file_name
                except json.JSONDecodeError:
                    return True, file_name
            else:
                err_preview = response_body[:200].replace('\n', ' ')
                out(f"⚠ 上传响应异常: HTTP {status_code} {err_preview}，60秒后重试", indent=1, prefix="[UPLOAD]")
                time.sleep(60)
                continue

        except Exception as e:
            err_str = str(e).lower()
            if 'timeout' in err_str or 'timed out' in err_str:
                out("⚠ 上传超时，验证文件是否已上传...", indent=1, prefix="[UPLOAD]")
                accounts = get_accounts(client)
                if accounts:
                    for acc in accounts:
                        if acc.get('name') == file_name or file_name.startswith(acc.get('name', '').replace('.json', '')):
                            out(f"✓ 文件已存在，验证成功: {file_name}", indent=1, prefix="[UPLOAD]")
                            return True, file_name
                out("⚠ 上传超时且文件未找到，60秒后重试", indent=1, prefix="[UPLOAD]")
                time.sleep(60)
                continue
            out(f"⚠ 上传异常: {e}，60秒后重试", indent=1, prefix="[UPLOAD]")
            time.sleep(60)
            continue


def verify_upload(client: HttpClient, file_name: str) -> bool:
    try:
        accounts = get_accounts(client)
        return any(acc.get('name') == file_name for acc in accounts)
    except:
        return False


async def run_concurrent(items: List, fn: Callable, concurrency: int, client: HttpClient, quota_threshold: float) -> List:
    if not items:
        return []

    total = len(items)
    limit = max(1, min(concurrency, total))
    results = [None] * total
    completed = 0
    started = 0
    start_time = time.time()

    async def run_one(index: int, item: dict):
        result = await fn(client, item, quota_threshold)
        return index, result

    in_flight = set()

    while started < total and len(in_flight) < limit:
        in_flight.add(asyncio.create_task(run_one(started, items[started])))
        started += 1

    while in_flight:
        done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            index, result = await task
            results[index] = result
            completed += 1

            if started < total:
                in_flight.add(asyncio.create_task(run_one(started, items[started])))
                started += 1

    return [r for r in results if r is not None]


async def register_accounts_maintenance(
    need_count: int,
    register_timeout: float,
    domain_index: int,
    domain_name: str,
    concurrency: int,
    client: HttpClient,
    base_url: str,
    token: str,
    register_interval: int = 60,
    proxy: Optional[str] = None,
    global_start_time: Optional[float] = None,
    consecutive_fails: int = 0,
    consecutive_400_fails: int = 0,
) -> Tuple[int, int, bool, int, int]:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out(f"开始注册 {need_count} 个账号...", ts=True)
    out(f"总运行时长限制: {register_timeout:.1f} 秒", indent=1)

    success_count = 0
    fail_count = 0
    max_consecutive_fails = 3
    start_time = time.time()
    overall_start_time = global_start_time or start_time
    generated_token_files = {}
    stopped_by_consecutive_fails = False

    while success_count + fail_count < need_count:
        register_elapsed = int(time.time() - start_time)
        total_elapsed = int(time.time() - overall_start_time)
        remaining_time = max(0, int(register_timeout - register_elapsed))
        if register_elapsed >= register_timeout:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            out(f"注册阶段运行时已达 {register_elapsed}s (限制: {register_timeout}s)，停止注册", prefix="[Warn]", ts=True)
            break

        if consecutive_fails >= max_consecutive_fails:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            out(f"连续失败 {consecutive_fails} 次，停止注册", prefix="[Error]", ts=True)
            if consecutive_400_fails >= max_consecutive_fails:
                out(f"连续 {consecutive_400_fails} 次失败均为 400 错误 (registration_disallowed)，可能是 IP 或邮箱被风控，建议等待一段时间后重试", prefix="[Error]", ts=True)
            stopped_by_consecutive_fails = True
            break

        total_count = success_count + fail_count + 1
        out(f"\n--- 注册第 {total_count}/{need_count} 次 (成功: {success_count}, 失败: {fail_count}, 已运行: {total_elapsed}s, 剩余: {remaining_time}s) ---")

        before_snap = snapshot_token_files()

        result = run_one(proxy, domain_index, domain_name)

        after_snap = snapshot_token_files()
        new_token_files = diff_token_files(before_snap, after_snap)

        if result and result.get('token') and new_token_files:
            success_count += 1
            consecutive_fails = 0
            out(f"✓ 注册成功，生成 {len(new_token_files)} 个 token 文件", indent=1)
            consecutive_400_fails = 0

            for file_path in new_token_files:
                file_name = os.path.basename(file_path)
                generated_token_files[file_name] = file_path

                upload_success, upload_err = await upload_file(client, file_path)

                if upload_success:
                    out(f"✓ 上传新账号: {file_name}", indent=1)
                    try:
                        os.unlink(file_path)
                        out(f"✓ 已删除本地文件: {file_name}", indent=1)
                        del generated_token_files[file_name]
                    except:
                        pass

            if result.get('email'):
                proxies = {"http": proxy, "https": proxy} if proxy else None
                if delete_mailbox(result['email'], proxies):
                    out(f"✓ 已删除邮箱: {result['email']}", indent=1)
                else:
                    out(f"✗ 删除邮箱失败: {result['email']}", indent=1)
        elif result and result.get('token'):
            save_result(result)
            success_count += 1
            consecutive_fails = 0
            out("✓ 注册成功", indent=1)
            consecutive_400_fails = 0

            token_files = [f for f in os.listdir(TOKENS_DIR) if re.match(r'^token.*\.json$', f) and result.get('email', '').replace('@', '_') in f]
            for file_name in token_files:
                file_path = os.path.join(TOKENS_DIR, file_name)

                upload_success, upload_err = await upload_file(client, file_path)

                if upload_success:
                    out(f"✓ 上传新账号: {file_name}", indent=1)
                    try:
                        os.unlink(file_path)
                        out(f"✓ 已删除本地文件: {file_name}", indent=1)
                    except:
                        pass

            if result.get('email'):
                proxies = {"http": proxy, "https": proxy} if proxy else None
                if delete_mailbox(result['email'], proxies):
                    out(f"✓ 已删除邮箱: {result['email']}", indent=1)
                else:
                    out(f"✗ 删除邮箱失败: {result['email']}", indent=1)
        else:
            fail_count += 1
            consecutive_fails += 1
            raw_error_text = str((result or {}).get('error') or '') if isinstance(result, dict) else ''
            error_text = raw_error_text.lower()
            fatal_registration_error = _match_fatal_registration_error(raw_error_text)
            if 'registration_disallowed' in error_text or ' 400' in error_text or 'http 400' in error_text:
                consecutive_400_fails += 1
            else:
                consecutive_400_fails = 0
            out(f"✗ 注册失败 (累计连续失败 {consecutive_fails}/{max_consecutive_fails})", indent=1)
            if fatal_registration_error:
                out(f"检测到致命错误: {fatal_registration_error}，停止后续注册并正常退出", prefix="[Warn]", ts=True)
                stopped_by_consecutive_fails = True
                break
            if consecutive_fails >= max_consecutive_fails:
                out(f"连续失败达到阈值 {max_consecutive_fails}，停止注册并正常退出", prefix="[Warn]", ts=True)
                if consecutive_400_fails >= max_consecutive_fails:
                    out(
                        f"连续 {consecutive_400_fails} 次失败均为 400 错误 (registration_disallowed/类似风控)，建议等待一段时间后重试",
                        prefix="[Warn]",
                        ts=True,
                    )
                stopped_by_consecutive_fails = True
                break

        out(f"本次尝试结束，休息 {register_interval} 秒后继续...", ts=True)
        time.sleep(register_interval)

    out(f"\n注册完成: 成功 {success_count} 个, 失败 {fail_count} 个")

    pending = [p for f, p in generated_token_files.items() if os.path.exists(p)]
    if pending:
        out(f"\n检测到 {len(pending)} 个未上传的 token 文件，开始补传...", prefix="[UPLOAD]")
        for file_path in pending:
            file_name = os.path.basename(file_path)
            upload_success, _ = await upload_file(client, file_path)
            if upload_success:
                out(f"✓ 补传成功: {file_name}", indent=1, prefix="[UPLOAD]")
                try:
                    os.unlink(file_path)
                    out(f"✓ 已删除本地文件: {file_name}", indent=1, prefix="[UPLOAD]")
                except:
                    pass
            else:
                out(f"✗ 补传失败: {file_name}", indent=1, prefix="[UPLOAD]")

    return success_count, fail_count, stopped_by_consecutive_fails, consecutive_fails, consecutive_400_fails


async def check_and_clean_accounts(client: HttpClient, quota_threshold: float, concurrency: int) -> int:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out("--- 检测账号状态 ---", ts=True)
    check_start_time = time.time()

    accounts = get_accounts(client)
    if not accounts:
        out("获取账号列表失败", ts=True)
        return 0

    codex_accounts = [acc for acc in accounts if acc.get('provider') == 'codex']
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out(f"当前 codex 账号: {len(codex_accounts)} 个", ts=True)

    if not codex_accounts:
        return 0

    check_results = await run_concurrent(codex_accounts, check_account, concurrency, client, quota_threshold)

    invalid_401 = [r for r in check_results if r.get('invalid_401')]
    low_quota = [r for r in check_results if r.get('low_quota')]
    ok = [r for r in check_results if not r.get('invalid_401') and not r.get('low_quota') and not r.get('error')]
    elapsed_values = [r.get('elapsed_seconds') for r in check_results if isinstance(r.get('elapsed_seconds'), (int, float))]

    out(f"- 401 失效: {len(invalid_401)} 个", indent=1)
    out(f"- 额度不足: {len(low_quota)} 个", indent=1)
    out(f"- 正常: {len(ok)} 个", indent=1)
    if elapsed_values:
        avg_elapsed = sum(elapsed_values) / len(elapsed_values)
        max_elapsed = max(elapsed_values)
        min_elapsed = min(elapsed_values)
        out(f"- 单账号耗时: avg={avg_elapsed:.2f}s min={min_elapsed:.2f}s max={max_elapsed:.2f}s", indent=1)

    to_delete = []

    for acc in invalid_401:
        to_delete.append({'name': acc['name'], 'reason': '401'})

    for acc in low_quota:
        if not any(d['name'] == acc['name'] for d in to_delete):
            remain = acc.get('remaining_percent')
            remain_str = f"{round(remain * 10) / 10}%" if remain is not None else 'unknown'
            to_delete.append({'name': acc['name'], 'reason': f'quota<{quota_threshold}% (remain={remain_str})'})

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out(f"删除 {len(to_delete)} 个失效账号...", ts=True)
    for acc in to_delete:
        try:
            if delete_account(client, acc['name']):
                out(f"✓ 删除: {acc['name']} ({acc['reason']})", indent=1)
            else:
                out(f"✗ 删除失败: {acc['name']}", indent=1)
        except Exception as e:
            out(f"✗ 删除异常: {acc['name']} - {e}", indent=1)

    total_check_elapsed = time.time() - check_start_time
    out(f"- 检测总耗时: {total_check_elapsed:.1f}s", indent=1)

    return len(ok)


async def main():
    script_start_time = time.time()
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本 V5 - 集成版")
    parser.add_argument("--proxy", default=None, help="代理地址")
    parser.add_argument("--domain-index", type=int, default=DEFAULT_DOMAIN_INDEX, help="邮箱域名索引")
    parser.add_argument("--domain", default=None, help="指定邮箱域名（如 mail.example.com），优先级高于 --domain-index")
    parser.add_argument("--cf-api-token", default=None, help="Cloudflare API Token")
    parser.add_argument("--cf-worker-name", default=None, help="Cloudflare Worker 名称")
    parser.add_argument("--cf-base-domain", default=None, help="Cloudflare 主域名，例如 example.com")
    parser.add_argument("--mail-url", default=DEFAULT_MAILFREE_BASE, help="邮箱服务地址（默认 https://mailfree.smanx.xx.kg）")
    parser.add_argument("--mail-token", default=DEFAULT_JWT_TOKEN, help="邮箱服务授权令牌（默认 auto）")
    parser.add_argument("--count", type=int, default=1, help="注册数量")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=30, help="循环模式最长等待秒数")
    parser.add_argument("--register-interval", type=int, default=60, help="注册间隔时间(秒)")
    parser.add_argument("--domain-ready-timeout", type=int, default=10, help="Cloudflare 新域名等待邮箱服务生效超时(秒)")
    parser.add_argument("--domain-ready-interval", type=int, default=5, help="Cloudflare 新域名生效轮询间隔(秒)")
    parser.add_argument("--worker-verify-timeout", type=int, default=5, help="Worker 环境变量更新校验超时(秒)")
    parser.add_argument("--worker-verify-interval", type=int, default=2, help="Worker 环境变量更新校验间隔(秒)")
    parser.add_argument("--save-account", action="store_true", help="保存账号密码")
    parser.add_argument("--target-url", default=DEFAULT_TARGET_BASE_URL, help="目标服务器地址（账号管理服务）")
    parser.add_argument("--target-token", default=DEFAULT_TARGET_TOKEN, help="目标服务器认证令牌")
    parser.add_argument("--min-accounts", type=int, default=DEFAULT_MIN_ACCOUNTS, help="账号数量阈值")
    parser.add_argument("--quota-threshold", type=float, default=DEFAULT_QUOTA_THRESHOLD_PERCENT, help="额度不足删除阈值")
    parser.add_argument("--timeout", type=int, default=DEFAULT_REGISTER_TIMEOUT, help="脚本最大运行时长(秒)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发数")
    parser.add_argument("--maintenance-interval", type=int, default=60, help="账号充足时的维护检测间隔(秒)")
    parser.add_argument("--notify-url", default=NOTIFY_BASE_URL, help="通知服务地址")
    parser.add_argument("--mode", choices=["register", "maintenance", "both"], default="both", help="运行模式")

    args = parser.parse_args()

    if not HAS_SENTINEL_POW:
        log.warning("sentinel_pow 模块未安装，将使用空 PoW")

    global MAILFREE_BASE, JWT_TOKEN
    MAILFREE_BASE = args.mail_url
    JWT_TOKEN = args.mail_token

    prepared_domain = _prepare_cloudflare_mail_domain(args)
    if prepared_domain:
        args.domain = prepared_domain
        out(f"Cloudflare 邮箱域名配置完成，本次运行使用新域名: {args.domain}", prefix="[CF]")

    base_url = args.target_url or os.environ.get("TARGET_URL", DEFAULT_TARGET_BASE_URL)
    token = args.target_token or os.environ.get("TARGET_TOKEN", DEFAULT_TARGET_TOKEN)
    client = HttpClient(base_url, token) if base_url and token else None

    if base_url and token:
        if args.mode == "register":
            args.mode = "both"

    if args.mode == "maintenance" or args.mode == "both":
        if not client:
            if args.mode == "maintenance":
                return
            else:
                pass
        else:
            sleep_duration = max(1, int(args.maintenance_interval))
            round_num = 0
            ever_registered = False
            total_timeout = args.timeout
            maintenance_consecutive_fails = 0
            maintenance_consecutive_400_fails = 0

            while True:
                round_num += 1
                elapsed = int(time.time() - script_start_time)
                remaining = max(0, total_timeout - elapsed)
                section(f"第 {round_num} 轮维护 (总运行时长: {elapsed}s, 剩余: {remaining}s)")

                if elapsed >= total_timeout:
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    out(f"总运行时长已达 {total_timeout} 秒，正常退出", prefix="[Warn]", ts=True)
                    sys.exit(0)

                valid_count = await check_and_clean_accounts(client, args.quota_threshold, args.concurrency)
                need_count = args.min_accounts - valid_count
                out(f"\n当前有效 codex 账号: {valid_count} 个，阈值: {args.min_accounts}")

                if need_count < 1:
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    out(f"账号充足 (>= {args.min_accounts})，等待 {sleep_duration} 秒后继续检测...", ts=True)
                    time.sleep(sleep_duration)
                    continue

                out(f"\n需要注册 {need_count} 个账号...")

                success_count, fail_count, stopped_by_consecutive_fails, maintenance_consecutive_fails, maintenance_consecutive_400_fails = await register_accounts_maintenance(
                    need_count,
                    remaining,
                    args.domain_index,
                    args.domain,
                    args.concurrency,
                    client,
                    base_url,
                    token,
                    args.register_interval,
                    args.proxy,
                    script_start_time,
                    maintenance_consecutive_fails,
                    maintenance_consecutive_400_fails,
                )

                ever_registered = True

                section('本轮申请完成，返回检测流程')
                out(f"成功: {success_count} 个, 失败: {fail_count} 个")
                if stopped_by_consecutive_fails:
                    if maintenance_consecutive_fails >= 3:
                        out("连续失败达到阈值，退出维护循环")
                    else:
                        out("检测到致命注册错误，正常退出维护循环")
                    return
                continue

    if args.mode == "register":
        total = args.count
        workers = min(args.workers, total)
        stats = {"ok": 0, "fail": 0}
        lock = threading.Lock()

        async def do_one_async(idx, delay=0):
            if delay > 0:
                await asyncio.sleep(delay)

            start_t = time.time()
            result = run_one(args.proxy, args.domain_index, args.domain)

            if result and result.get("token"):
                save_result(result)
                if client:
                    for f in scan_token_files():
                        file_path = os.path.join(TOKENS_DIR, f)
                        upload_success, _ = await upload_file(client, file_path)
                        if upload_success:
                            try:
                                os.unlink(file_path)
                            except:
                                pass
                with lock:
                    stats["ok"] += 1
            else:
                with lock:
                    stats["fail"] += 1

            if result and result.get("email"):
                proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
                delete_mailbox(result["email"], proxies)

        if workers <= 1:
            for i in range(1, total + 1):
                await do_one_async(i)
                if i < total:
                    time.sleep(random.randint(args.sleep_min, args.sleep_max))
        else:
            async def run_all():
                tasks = []
                for i in range(1, total + 1):
                    wave_pos = (i - 1) % workers
                    delay = wave_pos * random.uniform(1.0, 2.5) if wave_pos > 0 else 0
                    tasks.append(do_one_async(i, delay))
                await asyncio.gather(*tasks)
            asyncio.run(run_all())

        log.info(f"注册完成: 成功 {stats['ok']}, 失败 {stats['fail']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
