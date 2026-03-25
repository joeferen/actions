/**
 * Node.js 脚本：检测 codex 提供商账号的 401 状态并删除失效账号
 * 
 * 使用方法：
 *   node codex_maintenance.js <min_accounts> [quota_threshold_percent] [base_url] [token] [domain_index] [register_timeout] [register_script] [concurrency] [--register-count N]
 * 
 * 参数：
 *   min_accounts     - 账号数量阈值，低于此值会上传 token*.json 文件并注册新账号
 *   quota_threshold_percent - 额度不足删除阈值（剩余额度百分比 < 该值则删除；可选，默认 20）
 *   base_url         - 服务地址（可选，有默认值）
 *   token            - 认证令牌（可选，有默认值）
 *   domain_index     - 邮箱域名索引（默认 0）
 *   register_timeout - 注册循环总时长限制（秒），超时后停止注册
 *   register_script  - 注册脚本名称（如 openai_register_v2.py）
 *   concurrency      - 并发数，默认 50
 *   --register-count N - 注册模式控制：
 *                        - 不指定：默认注册 1 个账号
 *                        - N = 0：检测账号状态，注册不足的数量
 *                        - N > 0：直接注册 N 个账号（跳过账号检测）
 * 
 * 运行示例：
 *   # 默认模式：注册 1 个账号
 *   node codex_maintenance.js 100
 * 
 *   # 补充模式：检测账号状态，注册不足的数量
 *   node codex_maintenance.js 100 --register-count 0
 * 
 *   # 批量注册模式：直接注册 10 个账号
 *   node codex_maintenance.js 100 --register-count 10
 * 
 *   # 完整参数：指定服务器地址和令牌
 *   node codex_maintenance.js 100 20 https://api.example.com your_token 0 1800 openai_register_v2.py 50
 * 
 *   # 使用环境变量设置服务器地址和令牌
 *   set BASE_URL=https://api.example.com
 *   set TOKEN=your_token
 *   node codex_maintenance.js 100
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// 默认配置
const DEFAULT_MIN_ACCOUNTS = 100;
const DEFAULT_DOMAIN_INDEX = 0;
const DEFAULT_REGISTER_TIMEOUT = 18000; // 注册循环总时长限制（秒）
const DEFAULT_REGISTER_SCRIPT = 'openai_register_v2.py';
const DEFAULT_BASE_URL = '';
const DEFAULT_TOKEN = '';
const DEFAULT_CONCURRENCY = 50;
const NOTIFY_BASE_URL = 'https://api.day.app/xxxxxxxx';

// 额度不足删除阈值（剩余额度百分比 < 该值则删除）
const DEFAULT_QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT = 20;
const FINAL_ACCOUNT_NOTIFY_THRESHOLD_RATIO = 0.9;

// 解析命令行参数
const args = process.argv.slice(2);

// 解析 --register-count 参数
// 默认值为 1；0 表示补充不足数量；> 1 表示每轮直接注册指定数量
let REGISTER_COUNT = 1;
let positionalArgs = [];
for (let i = 0; i < args.length; i++) {
    if (args[i] === '--register-count' && i + 1 < args.length) {
        const val = args[++i];
        REGISTER_COUNT = parseInt(val);
        if (isNaN(REGISTER_COUNT)) {
            REGISTER_COUNT = 1;
        }
    } else {
        positionalArgs.push(args[i]);
    }
}

const MIN_ACCOUNTS = parseInt(positionalArgs[0]) || DEFAULT_MIN_ACCOUNTS;

const QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT = (positionalArgs[1] !== undefined && positionalArgs[1] !== null && String(positionalArgs[1]).trim() !== '')
    ? Math.max(0, Math.min(100, parseFloat(String(positionalArgs[1]).trim().replace(/%$/, ''))))
    : DEFAULT_QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT;

// BASE_URL 和 TOKEN 优先从环境变量读取
const BASE_URL = normalizeUrl(positionalArgs[2] || process.env.BASE_URL || DEFAULT_BASE_URL);
const TOKEN = positionalArgs[3] || process.env.TOKEN || DEFAULT_TOKEN;
const DOMAIN_INDEX = parseInt(positionalArgs[4]) || DEFAULT_DOMAIN_INDEX;
const REGISTER_TIMEOUT = parseInt(positionalArgs[5]) * 1000 || DEFAULT_REGISTER_TIMEOUT * 1000;
const REGISTER_SCRIPT = positionalArgs[6] || DEFAULT_REGISTER_SCRIPT;
const CONCURRENCY = parseInt(positionalArgs[7]) || DEFAULT_CONCURRENCY;

// 标准化 URL
function normalizeUrl(url) {
    let s = (url || '').trim()
        .replace(/：/g, ':')
        .replace(/／/g, '/')
        .replace(/。/g, '.')
        .replace(/，/g, ',')
        .replace(/；/g, ';')
        .replace(/　/g, ' ')
        .trim();
    if (s.endsWith('/')) s = s.slice(0, -1);
    return s;
}

// 通用 HTTP 请求封装
function httpRequest(path, options = {}) {
    return new Promise((resolve, reject) => {
        const url = BASE_URL + path;
        const parsedUrl = new URL(url);
        
        const requestOptions = {
            hostname: parsedUrl.hostname,
            port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
            path: parsedUrl.pathname + parsedUrl.search,
            method: options.method || 'GET',
            headers: {
                'Authorization': `Bearer ${TOKEN}`,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                ...(options.headers || {})
            }
        };

        const client = parsedUrl.protocol === 'https:' ? https : http;
        
        const req = client.request(requestOptions, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    try {
                        resolve(JSON.parse(data));
                    } catch (e) {
                        resolve(data);
                    }
                } else {
                    reject(new Error(`HTTP ${res.statusCode}: ${data.substring(0, 200)}`));
                }
            });
        });

        req.on('error', reject);
        
        if (options.body) {
            req.write(options.body);
        }
        req.end();
    });
}

// 发送通知提醒
function sendNotification(title, content) {
    return new Promise((resolve, reject) => {
        const safeTitle = encodeURIComponent(title);
        const safeContent = encodeURIComponent(content);
        const url = `${NOTIFY_BASE_URL}/${safeTitle}/${safeContent}`;
        const parsedUrl = new URL(url);

        const requestOptions = {
            hostname: parsedUrl.hostname,
            port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
            path: parsedUrl.pathname + parsedUrl.search,
            method: 'GET'
        };

        const client = parsedUrl.protocol === 'https:' ? https : http;

        const req = client.request(requestOptions, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    resolve(data);
                } else {
                    reject(new Error(`HTTP ${res.statusCode}: ${data.substring(0, 200)}`));
                }
            });
        });

        req.on('error', reject);
        req.end();
    });
}

// 获取账号列表
async function getAccounts() {
    const data = await httpRequest('/v0/management/auth-files');
    return data.files || [];
}

// 解析百分比（参考 index.html）
function parsePercent(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return v;
    try {
        const s = String(v).trim().replace(/%$/, '');
        return s === '' ? null : parseFloat(s);
    } catch {
        return null;
    }
}

// 检测单个账号：401 + 额度（参考 index.html 的 wham/usage 解析逻辑）
async function checkAccount(item) {
    const authIndex = item.auth_index;
    const name = item.name || item.id;
    const chatgptAccountId = item.chatgpt_account_id || item.chatgptAccountId || item.account_id || item.accountId;

    if (!authIndex) {
        return { name, error: 'missing auth_index', invalid_401: false, low_quota: false };
    }

    try {
        const payload = {
            authIndex: authIndex,
            method: 'GET',
            url: 'https://chatgpt.com/backend-api/wham/usage',
            header: {
                'Authorization': 'Bearer $TOKEN$',
                'Content-Type': 'application/json',
                'User-Agent': 'codex_cli_rs/0.76.0',
                ...(chatgptAccountId ? { 'Chatgpt-Account-Id': chatgptAccountId } : {})
            }
        };

        const data = await httpRequest('/v0/management/api-call', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        const is401 = data.status_code === 401;

        let used_percent = null;
        let remaining_percent = null;
        let quota_source = null;
        let primary_used_percent = null;
        let primary_reset_at = null;
        let individual_used_percent = null;
        let individual_reset_at = null;

        if (data.status_code === 200) {
            let usageData = {};
            try {
                usageData = typeof data.body === 'string' ? JSON.parse(data.body) : (data.body || {});
            } catch {
                usageData = {};
            }

            const rateLimit = usageData.rate_limit || usageData.rateLimit || {};

            const windows = [];
            ['primary_window', 'secondary_window', 'individual_window', 'primaryWindow', 'secondaryWindow', 'individualWindow'].forEach(key => {
                const win = rateLimit[key];
                if (win && typeof win === 'object') {
                    windows.push({
                        name: key,
                        used_percent: parsePercent(win.used_percent ?? win.usedPercent ?? win.used_percentage),
                        reset_at: win.reset_at ?? win.resetAt,
                        limit_window_seconds: win.limit_window_seconds ?? win.limitWindowSeconds ?? win.window_seconds ?? win.windowSeconds,
                        remaining: win.remaining,
                        limit_reached: win.limit_reached ?? win.limitReached
                    });
                }
            });

            let weeklyWindow = windows.find(w => String(w.name || '').toLowerCase().includes('individual'));
            let shortWindow = windows.find(w => String(w.name || '').toLowerCase().includes('secondary'));

            // 按窗口时长兜底选择
            const withSeconds = windows.filter(w => w.limit_window_seconds);
            if (!weeklyWindow && withSeconds.length) {
                weeklyWindow = withSeconds.reduce((a, b) => a.limit_window_seconds > b.limit_window_seconds ? a : b);
            }
            if (!shortWindow && withSeconds.length) {
                const sorted = [...withSeconds].sort((a, b) => a.limit_window_seconds - b.limit_window_seconds);
                shortWindow = sorted.find(w => w !== weeklyWindow) || sorted[0];
            }

            if (weeklyWindow) {
                individual_used_percent = weeklyWindow.used_percent;
                individual_reset_at = weeklyWindow.reset_at;
            }
            if (shortWindow) {
                primary_used_percent = shortWindow.used_percent;
                primary_reset_at = shortWindow.reset_at;
            }

            // 优先用周窗口（individual），否则用短窗口
            if (weeklyWindow && weeklyWindow.used_percent !== null && weeklyWindow.used_percent !== undefined) {
                used_percent = weeklyWindow.used_percent;
                quota_source = 'weekly';
            } else if (shortWindow && shortWindow.used_percent !== null && shortWindow.used_percent !== undefined) {
                used_percent = shortWindow.used_percent;
                quota_source = 'short';
            }

            if (used_percent !== null && used_percent !== undefined && !Number.isNaN(Number(used_percent))) {
                remaining_percent = 100 - Number(used_percent);
            }
        }

        const lowQuota = (remaining_percent !== null && remaining_percent < QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT);
        return {
            name,
            status_code: data.status_code,
            invalid_401: is401,
            low_quota: lowQuota,
            used_percent,
            remaining_percent,
            quota_source,
            primary_used_percent,
            primary_reset_at,
            individual_used_percent,
            individual_reset_at,
            error: null
        };
    } catch (e) {
        return { name, error: e.message, invalid_401: false, low_quota: false };
    }
}

// 删除账号
async function deleteAccount(name) {
    const encoded = encodeURIComponent(name);
    await httpRequest(`/v0/management/auth-files?name=${encoded}`, {
        method: 'DELETE'
    });
    return true;
}

// 扫描目录下的 token*.json 文件
function scanTokenFiles() {
    const dir = __dirname;
    const files = fs.readdirSync(dir);
    return files.filter(f => /^token.*\.json$/i.test(f)).map(f => path.join(dir, f));
}

// 快照 token 文件元信息，用于识别“新建或被覆盖”的文件
function snapshotTokenFiles() {
    const m = new Map();
    const files = scanTokenFiles();
    for (const filePath of files) {
        try {
            const st = fs.statSync(filePath);
            const name = path.basename(filePath);
            m.set(name, {
                filePath,
                mtimeMs: st.mtimeMs,
                size: st.size
            });
        } catch {
            // 忽略瞬时不存在/无权限等异常
        }
    }
    return m;
}

function diffTokenFiles(beforeSnap, afterSnap) {
    const changed = [];
    for (const [name, meta] of afterSnap.entries()) {
        const prev = beforeSnap.get(name);
        if (!prev) {
            changed.push(meta.filePath);
            continue;
        }
        if (prev.mtimeMs !== meta.mtimeMs || prev.size !== meta.size) {
            changed.push(meta.filePath);
        }
    }
    return changed;
}

// 上传单个文件（带重试和验证）
async function uploadFile(filePath, retryCount = 3) {
    const fileName = path.basename(filePath);
    const fileContent = fs.readFileSync(filePath, 'utf-8');
    
    const url = BASE_URL + `/v0/management/auth-files?name=${encodeURIComponent(fileName)}`;
    const parsedUrl = new URL(url);
    
    for (let attempt = 1; attempt <= retryCount; attempt++) {
        try {
            const result = await new Promise((resolve, reject) => {
                const requestOptions = {
                    hostname: parsedUrl.hostname,
                    port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
                    path: parsedUrl.pathname + parsedUrl.search,
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${TOKEN}`,
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    }
                };

                const client = parsedUrl.protocol === 'https:' ? https : http;
                
                const req = client.request(requestOptions, (res) => {
                    let data = '';
                    res.on('data', chunk => data += chunk);
                    res.on('end', () => {
                        if (res.statusCode >= 200 && res.statusCode < 300) {
                            // 验证响应内容
                            try {
                                const respData = JSON.parse(data);
                                if (respData.status === 'ok' || respData.status === 'success' || Object.keys(respData).length === 0) {
                                    resolve({ success: true, fileName, response: respData });
                                } else if (respData.error) {
                                    reject(new Error(`服务端错误: ${respData.error}`));
                                } else {
                                    resolve({ success: true, fileName, response: respData });
                                }
                            } catch (e) {
                                // 无法解析 JSON，但状态码成功，可能就是纯文本成功
                                resolve({ success: true, fileName, response: data });
                            }
                        } else {
                            reject(new Error(`HTTP ${res.statusCode}: ${data.substring(0, 200)}`));
                        }
                    });
                });

                req.on('error', reject);
                req.write(fileContent);
                req.end();
            });
            
            // 上传成功后验证文件是否真的存在
            if (result.success) {
                const verified = await verifyUpload(fileName);
                if (verified) {
                    return result;
                } else {
                    console.log(`  [重试 ${attempt}/${retryCount}] 验证失败，文件可能未成功上传: ${fileName}`);
                    if (attempt < retryCount) {
                        await new Promise(r => setTimeout(r, 1000)); // 等待1秒后重试
                        continue;
                    }
                    throw new Error('上传后验证失败');
                }
            }
            
        } catch (e) {
            if (attempt < retryCount) {
                console.log(`  [重试 ${attempt}/${retryCount}] 上传失败: ${e.message}`);
                await new Promise(r => setTimeout(r, 1000)); // 等待1秒后重试
                continue;
            }
            throw e;
        }
    }
    
    throw new Error('上传重试次数用尽');
}

// 验证文件是否上传成功
async function verifyUpload(fileName) {
    try {
        const data = await httpRequest('/v0/management/auth-files');
        const files = data.files || [];
        return files.some(f => f.name === fileName);
    } catch (e) {
        console.log(`  验证请求失败: ${e.message}`);
        return false;
    }
}

// 并发执行
async function runConcurrent(items, fn, concurrency) {
    const results = [];
    for (let i = 0; i < items.length; i += concurrency) {
        const chunk = items.slice(i, i + concurrency);
        const chunkResults = await Promise.all(chunk.map(fn));
        results.push(...chunkResults);
        process.stdout.write(`\r进度: ${Math.min(i + concurrency, items.length)}/${items.length}`);
    }
    console.log();
    return results;
}

// 运行 Python 注册脚本一次
function runRegisterScript() {
    return new Promise((resolve, reject) => {
        const scriptPath = path.join(__dirname, REGISTER_SCRIPT);
        // 使用 shell: false 避免安全警告
        const proc = spawn('python', [scriptPath, '--domain-index', String(DOMAIN_INDEX), '--once'], {
            cwd: __dirname,
            stdio: 'inherit',
            shell: false
        });
        
        proc.on('close', (code) => {
            // 不再通过退出码判断成功，由调用方检查是否有新 token 文件
            resolve(code === 0);
        });
        
        proc.on('error', (err) => {
            console.error(`启动注册脚本失败: ${err.message}`);
            resolve(false);
        });
    });
}

// 注册账号（抽取的注册逻辑）
async function registerAccounts(needCount) {
    console.log(`开始注册 ${needCount} 个账号...`);
    console.log(`注册总时长限制: ${REGISTER_TIMEOUT / 1000} 秒`);
    
    let successCount = 0;
    let failCount = 0;
    let consecutiveFails = 0;  // 连续失败计数
    const MAX_CONSECUTIVE_FAILS = 5;  // 最大连续失败次数
    const startTime = Date.now();
    const generatedTokenFiles = new Map(); // name -> filePath（本轮注册产生/覆盖的 token 文件）
    
    while (successCount < needCount) {
        // 检查是否超时
        const elapsed = Date.now() - startTime;
        if (elapsed >= REGISTER_TIMEOUT) {
            console.log(`\n[Warn] 注册总时长已达 ${REGISTER_TIMEOUT / 1000} 秒，停止注册`);
            break;
        }
        
        // 检查是否连续失败次数达到上限
        if (consecutiveFails >= MAX_CONSECUTIVE_FAILS) {
            console.log(`\n[Error] 连续失败 ${consecutiveFails} 次，停止注册`);
            break;
        }
        
        console.log(`\n--- 注册第 ${successCount + 1}/${needCount} 个账号 (成功: ${successCount}, 失败: ${failCount}, 剩余时间: ${Math.round((REGISTER_TIMEOUT - elapsed) / 1000)}秒) ---`);
        
        // 记录注册前的 token 文件快照（用于识别新增/覆盖）
        const beforeSnap = snapshotTokenFiles();
         
        await runRegisterScript();
         
        // 检查是否有新生成或被覆盖的 token 文件
        const afterSnap = snapshotTokenFiles();
        const newTokenFiles = diffTokenFiles(beforeSnap, afterSnap);
         
        if (newTokenFiles.length > 0) {
            successCount++;
            consecutiveFails = 0;  // 成功后重置连续失败计数
            console.log(`  ✓ 注册成功，生成 ${newTokenFiles.length} 个 token 文件`);

            // 记录本轮注册产生/覆盖的 token 文件，注册完毕后兜底上传
            for (const filePath of newTokenFiles) {
                generatedTokenFiles.set(path.basename(filePath), filePath);
            }
             
            // 上传并删除新文件
            for (const filePath of newTokenFiles) {
                const fileName = path.basename(filePath);
                try {
                    await uploadFile(filePath);
                    console.log(`  ✓ 上传新账号: ${fileName}`);
                    fs.unlinkSync(filePath);
                    console.log(`  ✓ 已删除本地文件: ${fileName}`);
                    generatedTokenFiles.delete(fileName);
                } catch (e) {
                    console.log(`  ✗ 上传失败: ${fileName} - ${e.message}`);
                }
            }
        } else {
            failCount++;
            consecutiveFails++;  // 失败时增加连续失败计数
            console.log(`  ✗ 注册失败，未生成 token 文件 (连续失败 ${consecutiveFails}/${MAX_CONSECUTIVE_FAILS})`);
        }
    }
    
    console.log(`\n注册完成: 成功 ${successCount} 个, 失败 ${failCount} 个`);

    // 注册完毕后兜底：将本轮注册产生但尚未上传成功的 token 文件再尝试上传并删除
    const pending = Array.from(generatedTokenFiles.values()).filter(p => {
        try {
            return fs.existsSync(p);
        } catch {
            return false;
        }
    });
    if (pending.length > 0) {
        console.log(`\n注册完毕后检测到 ${pending.length} 个未上传的 token 文件，开始补传...`);
        for (const filePath of pending) {
            const fileName = path.basename(filePath);
            try {
                await uploadFile(filePath);
                console.log(`  ✓ 补传成功: ${fileName}`);
                fs.unlinkSync(filePath);
                console.log(`  ✓ 已删除本地文件: ${fileName}`);
                generatedTokenFiles.delete(fileName);
            } catch (e) {
                console.log(`  ✗ 补传失败: ${fileName} - ${e.message}`);
            }
        }
    }
    return { successCount, failCount };
}

// 检测并删除失效账号（返回当前有效 codex 账号数量）
async function checkAndCleanAccounts() {
    console.log('\n--- 检测账号状态 ---');
    
    let accounts;
    try {
        accounts = await getAccounts();
    } catch (e) {
        console.error(`获取账号列表失败: ${e.message}`);
        return 0;
    }
    
    const codexAccounts = accounts.filter(acc => acc.provider === 'codex');
    console.log(`当前 codex 账号: ${codexAccounts.length} 个`);
    
    if (codexAccounts.length === 0) {
        return 0;
    }
    
    // 检测账号状态
    const checkResults = await runConcurrent(codexAccounts, checkAccount, CONCURRENCY);
    
    // 统计结果
    const invalid401Accounts = checkResults.filter(r => r.invalid_401);
    const lowQuotaAccounts = checkResults.filter(r => r.low_quota);
    const okAccounts = checkResults.filter(r => !r.invalid_401 && !r.low_quota && !r.error);
    
    console.log(`  - 401 失效: ${invalid401Accounts.length} 个`);
    console.log(`  - 额度不足: ${lowQuotaAccounts.length} 个`);
    console.log(`  - 正常: ${okAccounts.length} 个`);
    
    // 删除失效账号
    const toDelete = [];
    invalid401Accounts.forEach(acc => toDelete.push({ name: acc.name, reason: '401' }));
    lowQuotaAccounts.forEach(acc => {
        if (!toDelete.find(d => d.name === acc.name)) {
            const remain = (acc.remaining_percent !== null && acc.remaining_percent !== undefined)
                ? `${Math.round(acc.remaining_percent * 10) / 10}%`
                : 'unknown';
            toDelete.push({ name: acc.name, reason: `quota<${QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT}% (remain=${remain})` });
        }
    });
    
    if (toDelete.length > 0) {
        console.log(`\n删除 ${toDelete.length} 个失效账号...`);
        for (const acc of toDelete) {
            try {
                await deleteAccount(acc.name);
                console.log(`  ✓ 删除: ${acc.name} (${acc.reason})`);
            } catch (e) {
                console.log(`  ✗ 删除失败: ${acc.name} - ${e.message}`);
            }
        }
    }
    
    // 返回有效账号数量
    return okAccounts.length;
}

// 主函数
async function main() {
    console.log('='.repeat(60));
    console.log('Codex 账号维护工具');
    console.log('='.repeat(60));
    console.log(`域名索引: ${DOMAIN_INDEX}`);
    console.log(`注册时长限制: ${REGISTER_TIMEOUT / 1000} 秒`);
    console.log(`注册脚本: ${REGISTER_SCRIPT}`);
    console.log(`账号阈值: ${MIN_ACCOUNTS}`);
    console.log(`服务地址: ${BASE_URL}`);
    console.log(`并发数: ${CONCURRENCY}`);
    console.log(`额度删除阈值: ${QUOTA_REMAINING_DELETE_THRESHOLD_PERCENT}%`);
    
    // 显示注册模式
    if (REGISTER_COUNT === 0) {
        console.log(`注册模式: 补充不足数量`);
    } else if (REGISTER_COUNT === 1) {
        console.log(`注册模式: 维护时默认注册 1 个账号`);
    } else {
        console.log(`注册模式: 批量注册 ${REGISTER_COUNT} 个账号`);
    }
    console.log();

    const SLEEP_DURATION = 60 * 1000; // 休眠 1 分钟
    let round = 0;

    while (true) {
        round++;
        console.log('\n' + '='.repeat(60));
        console.log(`第 ${round} 轮维护`);
        console.log('='.repeat(60));

        // 根据注册模式决定是否需要检测账号
        let needCount = 0;
        let validCount = 0;
        
        if (REGISTER_COUNT === 1) {
            // 默认模式：每轮维护注册 1 个
            needCount = 1;
            console.log(`\n默认模式: 本轮维护注册 1 个账号`);
        } else if (REGISTER_COUNT === 0) {
            // --register-count 0，补充不足数量
            validCount = await checkAndCleanAccounts();
            console.log(`\n当前有效 codex 账号: ${validCount} 个，阈值: ${MIN_ACCOUNTS}`);
            needCount = MIN_ACCOUNTS - validCount;
            if (needCount <= 0) {
                console.log(`\n账号充足 (>= ${MIN_ACCOUNTS})，无需补充`);
                console.log('\n' + '='.repeat(60));
                console.log('维护完成');
                console.log('='.repeat(60));
                break;
            }
            console.log(`\n需要补充 ${needCount} 个账号...`);
        } else {
            // --register-count N (N > 1)，每轮维护批量注册 N 个
            needCount = REGISTER_COUNT;
            console.log(`\n批量注册模式: 本轮维护注册 ${needCount} 个账号`);
        }
        
        // 执行注册
        const { successCount, failCount } = await registerAccounts(needCount);
        
        // 注册完成后退出
        console.log('\n' + '='.repeat(60));
        console.log('维护完成');
        console.log('='.repeat(60));
        console.log(`成功: ${successCount} 个, 失败: ${failCount} 个`);
        break;
    }
}

main().catch(e => {
    console.error('执行失败:', e.message);
    process.exit(1);
});
