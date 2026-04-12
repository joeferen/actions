/**
 * ak2.store 自动注册脚本
 * 
 * 支持两种邮箱服务：
 * 1. temp-mail: 使用 TempMail.lol（默认，无需配置）
 * 2. mailfree: 使用 MailFree
 * 
 * 使用方法：
 *   # 使用 TempMail.lol（默认，注册1个）
 *   node register.js
 * 
 *   # 使用代理
 *   node register.js --proxy http://127.0.0.1:7890
 * 
 *   # 注册5个账号
 *   node register.js --count 5 --proxy http://127.0.0.1:7890
 * 
 *   # 注册30分钟
 *   node register.js --duration 30 --proxy http://127.0.0.1:7890
 * 
 *   # 注册10个或60分钟，先到为止
 *   node register.js --count 10 --duration 60 --proxy http://127.0.0.1:7890
 * 
 *   # 使用 MailFree（通过环境变量）
 *   set MAIL_SERVICE=mailfree
 *   set MAILFREE_BASE=https://mailfree.smanx.xx.kg
 *   set MAILFREE_JWT_TOKEN=auto
 *   set MAILFREE_DOMAIN_INDEX=0
 *   set REGISTER_COUNT=5
 *   set REGISTER_DURATION=60
 *   set PROXY=http://127.0.0.1:7890
 *   node register.js
 * 
 *   # 使用 MailFree（通过命令行参数）
 *   node register.js --mail-service mailfree --mailfree-base https://mailfree.smanx.xx.kg --mailfree-jwt-token auto --mailfree-domain-index 0 --count 5 --duration 60 --proxy http://127.0.0.1:7890
 * 
 * 命令行参数：
 *   --mail-service            邮箱服务类型: temp-mail 或 mailfree
 *   --mailfree-base           MailFree 服务地址 (默认: https://mailfree.smanx.xx.kg)
 *   --mailfree-jwt-token      MailFree JWT Token (默认: auto)
 *   --mailfree-domain-index   MailFree 域名索引 (默认: 0)
 *   --count                   注册数量 (默认: 1)
 *   --duration                总时长(分钟) (默认: 60)
 *   --proxy                   代理地址 (例如: http://127.0.0.1:7890)
 * 
 * 数据保存位置：
 *   - data/accounts.txt: 一行一个，格式 "email|password|api_key"
 *   - data/keys.txt: 一行一个，仅保存 api_key
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const BASE_URL = 'ak2.store';
const TEMPMAIL_API_BASE = 'api.tempmail.lol';

function parseArgs() {
  const args = {
    mailService: process.env.MAIL_SERVICE || 'temp-mail',
    mailfreeBase: process.env.MAILFREE_BASE || 'https://mailfree.smanx.xx.kg',
    mailfreeJwtToken: process.env.MAILFREE_JWT_TOKEN || 'auto',
    mailfreeDomainIndex: parseInt(process.env.MAILFREE_DOMAIN_INDEX || '0', 10),
    count: parseInt(process.env.REGISTER_COUNT || '1', 10),
    duration: parseInt(process.env.REGISTER_DURATION || '60', 10),
    proxy: process.env.PROXY || null,
  };

  for (let i = 2; i < process.argv.length; i++) {
    const arg = process.argv[i];
    if (arg === '--mail-service' && i + 1 < process.argv.length) {
      args.mailService = process.argv[++i];
    } else if (arg === '--mailfree-base' && i + 1 < process.argv.length) {
      args.mailfreeBase = process.argv[++i];
    } else if (arg === '--mailfree-jwt-token' && i + 1 < process.argv.length) {
      args.mailfreeJwtToken = process.argv[++i];
    } else if (arg === '--mailfree-domain-index' && i + 1 < process.argv.length) {
      args.mailfreeDomainIndex = parseInt(process.argv[++i], 10);
    } else if (arg === '--count' && i + 1 < process.argv.length) {
      args.count = parseInt(process.argv[++i], 10);
    } else if (arg === '--duration' && i + 1 < process.argv.length) {
      args.duration = parseInt(process.argv[++i], 10);
    } else if (arg === '--proxy' && i + 1 < process.argv.length) {
      args.proxy = process.argv[++i];
    }
  }

  return args;
}

const args = parseArgs();
const MAIL_SERVICE = args.mailService;
const MAILFREE_BASE = args.mailfreeBase;
const MAILFREE_JWT_TOKEN = args.mailfreeJwtToken;
const MAILFREE_DOMAIN_INDEX = args.mailfreeDomainIndex;
const REGISTER_COUNT = args.count;
const REGISTER_DURATION = args.duration;
const PROXY = args.proxy;

let proxyConfig = null;
if (PROXY) {
  const proxyUrl = new URL(PROXY);
  proxyConfig = {
    host: proxyUrl.hostname,
    port: parseInt(proxyUrl.port || (proxyUrl.protocol === 'https:' ? 443 : 80), 10),
  };
  console.log('使用代理:', PROXY);
}

const DATA_DIR = path.join(__dirname, 'data');
const ACCOUNTS_FILE = path.join(DATA_DIR, 'accounts.txt');
const KEYS_FILE = path.join(DATA_DIR, 'keys.txt');

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function request(options, data = null) {
  return new Promise((resolve, reject) => {
    if (proxyConfig) {
      const isHttpsProxy = PROXY.startsWith('https://');
      
      const agentModule = isHttpsProxy ? https : http;
      
      const connectReq = agentModule.request({
        host: proxyConfig.host,
        port: proxyConfig.port,
        method: 'CONNECT',
        path: `${options.hostname}:${options.port || 443}`,
        rejectUnauthorized: false,
      });

      connectReq.on('connect', (res, socket) => {
        if (res.statusCode !== 200) {
          socket.destroy();
          reject(new Error(`Proxy CONNECT failed: ${res.statusCode}`));
          return;
        }

        const tls = require('tls');
        const secureSocket = tls.connect({
          socket: socket,
          servername: options.hostname,
          rejectUnauthorized: false,
          ALPNProtocols: ['http/1.1'],
        });

        secureSocket.on('error', (err) => {
          console.warn('TLS error, trying fallback...');
          socket.destroy();
          
          const fallbackOptions = {
            ...options,
            agent: false,
            rejectUnauthorized: false,
            host: proxyConfig.host,
            port: proxyConfig.port,
            path: `https://${options.hostname}${options.path}`,
            headers: {
              ...options.headers,
              Host: options.hostname,
            },
          };
          
          const fallbackReq = https.request(fallbackOptions, (res) => {
            let body = '';
            res.on('data', (chunk) => { body += chunk; });
            res.on('end', () => {
              try {
                const json = JSON.parse(body);
                resolve({ status: res.statusCode, data: json, body });
              } catch (e) {
                resolve({ status: res.statusCode, data: null, body });
              }
            });
          });
          
          fallbackReq.on('error', reject);
          if (data) fallbackReq.write(data);
          fallbackReq.end();
        });

        secureSocket.on('secureConnect', () => {
          const reqOptions = {
            hostname: options.hostname,
            port: options.port || 443,
            path: options.path,
            method: options.method || 'GET',
            headers: options.headers || {},
            socket: secureSocket,
            agent: false,
          };

          const req = https.request(reqOptions, (res) => {
            let body = '';
            res.on('data', (chunk) => { body += chunk; });
            res.on('end', () => {
              try {
                const json = JSON.parse(body);
                resolve({ status: res.statusCode, data: json, body });
              } catch (e) {
                resolve({ status: res.statusCode, data: null, body });
              }
            });
          });

          req.on('error', reject);
          if (data) req.write(data);
          req.end();
        });
      });

      connectReq.on('error', (err) => {
        console.warn('CONNECT failed, trying direct proxy request...');
        const proxyOptions = {
          ...options,
          agent: false,
          rejectUnauthorized: false,
          host: proxyConfig.host,
          port: proxyConfig.port,
          path: `https://${options.hostname}${options.path}`,
          headers: {
            ...options.headers,
            Host: options.hostname,
          },
        };
        
        const proxyReq = https.request(proxyOptions, (res) => {
          let body = '';
          res.on('data', (chunk) => { body += chunk; });
          res.on('end', () => {
            try {
              const json = JSON.parse(body);
              resolve({ status: res.statusCode, data: json, body });
            } catch (e) {
              resolve({ status: res.statusCode, data: null, body });
            }
          });
        });
        
        proxyReq.on('error', reject);
        if (data) proxyReq.write(data);
        proxyReq.end();
      });
      
      connectReq.end();
    } else {
      const req = https.request(options, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try {
            const json = JSON.parse(body);
            resolve({ status: res.statusCode, data: json, body });
          } catch (e) {
            resolve({ status: res.statusCode, data: null, body });
          }
        });
      });

      req.on('error', reject);
      if (data) req.write(data);
      req.end();
    }
  });
}

function parseUrl(url) {
  const urlObj = new URL(url);
  return {
    hostname: urlObj.hostname,
    port: urlObj.port || 443,
    path: urlObj.pathname + urlObj.search,
    protocol: urlObj.protocol,
  };
}

async function requestHttps(url, options = {}) {
  const parsed = parseUrl(url);
  const reqOptions = {
    hostname: parsed.hostname,
    port: parsed.port,
    path: parsed.path,
    method: options.method || 'GET',
    headers: options.headers || {},
  };
  return request(reqOptions, options.body);
}

async function createTempMail(prefix = null, domain = null) {
  const body = {};
  if (prefix) body.prefix = prefix;
  if (domain) body.domain = domain;

  const options = {
    hostname: TEMPMAIL_API_BASE,
    port: 443,
    path: '/v2/inbox/create',
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    }
  };

  const res = await request(options, JSON.stringify(body));
  if (res.status !== 200 && res.status !== 201) {
    throw new Error(`创建邮箱失败: ${res.status} ${res.body}`);
  }
  return { address: res.data.address, token: res.data.token };
}

async function getTempMailEmails(token) {
  const options = {
    hostname: TEMPMAIL_API_BASE,
    port: 443,
    path: `/v2/inbox?token=${encodeURIComponent(token)}`,
    method: 'GET',
    headers: {
      'Accept': 'application/json',
    }
  };

  const res = await request(options);
  if (res.status !== 200) {
    return { emails: [], expired: false };
  }
  return res.data;
}

async function mailfreeGetDomains() {
  const res = await requestHttps(`${MAILFREE_BASE}/api/domains`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  });
  if (res.status !== 200) {
    throw new Error(`获取域名失败: ${res.status}`);
  }
  return res.data;
}

async function mailfreeCreateEmail(local, domainIndex = 0) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/create`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    },
    body: JSON.stringify({ local, domainIndex }),
  });
  if (res.status !== 200) {
    throw new Error(`创建邮箱失败: ${res.status} ${res.body}`);
  }
  return res.data;
}

async function mailfreeGetEmails(mailbox, limit = 20) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/emails?mailbox=${encodeURIComponent(mailbox)}&limit=${limit}`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  });
  if (res.status !== 200) {
    return [];
  }
  return res.data;
}

async function mailfreeGetEmailDetail(emailId) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/email/${emailId}`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  });
  if (res.status !== 200) {
    return {};
  }
  return res.data;
}

function extractVerifyCode(content) {
  const match = content.match(/(?<!\d)(\d{6})(?!\d)/);
  return match ? match[1] : null;
}

async function pollVerifyCodeTempMail(email, inboxToken, timeout = 180) {
  const start = Date.now();
  const intervals = [3000, 4000, 5000, 6000, 8000, 10000];
  let idx = 0;

  while (Date.now() - start < timeout * 1000) {
    try {
      const data = await getTempMailEmails(inboxToken);
      if (data.expired) {
        throw new Error('收件箱已过期');
      }

      for (const mail of data.emails || []) {
        const sender = (mail.from || '').toLowerCase();
        const subject = mail.subject || '';
        const body = mail.body || '';
        const html = mail.html || '';

        const content = `${sender} ${subject} ${body} ${html}`.toLowerCase();
        const code = extractVerifyCode(content);
        if (code) {
          return code;
        }
      }
    } catch (e) {
      console.warn('查询邮箱失败:', e.message);
    }

    const wait = intervals[Math.min(idx, intervals.length - 1)];
    idx++;
    await new Promise(r => setTimeout(r, wait));
  }

  throw new Error('验证码超时');
}

async function pollVerifyCodeMailFree(email, timeout = 180) {
  const start = Date.now();
  const intervals = [3000, 4000, 5000, 6000, 8000, 10000];
  let idx = 0;
  const seenIds = new Set();

  while (Date.now() - start < timeout * 1000) {
    try {
      const emails = await mailfreeGetEmails(email, 10);
      for (const mail of emails) {
        const msgId = mail.id;
        if (!msgId || seenIds.has(msgId)) continue;
        seenIds.add(msgId);

        const sender = (mail.sender || '').toLowerCase();
        const subject = mail.subject || '';
        const preview = mail.preview || '';
        const verificationCode = mail.verification_code;

        let content = `${subject} ${preview}`;
        let code = null;

        if (verificationCode) {
          code = extractVerifyCode(String(verificationCode));
        } else {
          code = extractVerifyCode(content);
        }

        if (!code) {
          const detail = await mailfreeGetEmailDetail(msgId);
          content = `${content} ${detail.content || ''} ${detail.html_content || ''}`;
          code = extractVerifyCode(content);
        }

        if (code) {
          return code;
        }
      }
    } catch (e) {
      console.warn('查询邮箱失败:', e.message);
    }

    const wait = intervals[Math.min(idx, intervals.length - 1)];
    idx++;
    await new Promise(r => setTimeout(r, wait));
  }

  throw new Error('验证码超时');
}

async function ak2Request(path, method = 'GET', data = null, token = null) {
  const headers = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh',
    'Content-Type': 'application/json',
    'Referer': `https://${BASE_URL}/`,
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const options = {
    hostname: BASE_URL,
    port: 443,
    path,
    method,
    headers,
  };

  const res = await request(options, data ? JSON.stringify(data) : null);
  return res;
}

function randomPassword() {
  const upper = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const lower = 'abcdefghijklmnopqrstuvwxyz';
  const digits = '0123456789';
  const specials = '!@#$%&*';
  const all = upper + lower + digits + specials;

  let pwd = '';
  pwd += upper[Math.floor(Math.random() * upper.length)];
  pwd += lower[Math.floor(Math.random() * lower.length)];
  pwd += digits[Math.floor(Math.random() * digits.length)];
  pwd += specials[Math.floor(Math.random() * specials.length)];

  for (let i = 0; i < 12; i++) {
    pwd += all[Math.floor(Math.random() * all.length)];
  }

  return pwd.split('').sort(() => Math.random() - 0.5).join('');
}

function randomPrefix() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let prefix = '';
  for (let i = 0; i < 10; i++) {
    prefix += chars[Math.floor(Math.random() * chars.length)];
  }
  return prefix;
}

function saveAccount(email, password, apiKey) {
  fs.appendFileSync(ACCOUNTS_FILE, `${email}|${password}|${apiKey}\n`, 'utf8');
  fs.appendFileSync(KEYS_FILE, `${apiKey}\n`, 'utf8');
}

async function register() {
  console.log('=== ak2.store 自动注册 ===');
  console.log('邮箱服务:', MAIL_SERVICE);

  let email, inboxContext;

  if (MAIL_SERVICE === 'mailfree') {
    console.log('获取可用域名...');
    const domains = await mailfreeGetDomains();
    const local = randomPrefix();
    console.log('使用域名索引:', MAILFREE_DOMAIN_INDEX);
    const emailInfo = await mailfreeCreateEmail(local, MAILFREE_DOMAIN_INDEX);
    email = emailInfo.address || emailInfo.email;
    inboxContext = { type: 'mailfree', email };
  } else {
    const inbox = await createTempMail(randomPrefix());
    email = inbox.address;
    inboxContext = { type: 'temp-mail', token: inbox.token };
  }

  const password = randomPassword();
  console.log('邮箱:', email);
  console.log('密码:', password);

  await ak2Request('/api/v1/settings/public?timezone=Asia%2FShanghai', 'GET');

  console.log('发送验证码...');
  const sendRes = await ak2Request('/api/v1/auth/send-verify-code', 'POST', { email });
  if (sendRes.data?.code !== 0) {
    throw new Error('发送验证码失败: ' + JSON.stringify(sendRes.data));
  }

  console.log('等待验证码...');
  let code;
  if (inboxContext.type === 'mailfree') {
    code = await pollVerifyCodeMailFree(email);
  } else {
    code = await pollVerifyCodeTempMail(email, inboxContext.token);
  }
  console.log('验证码:', code);

  console.log('注册账号...');
  const registerRes = await ak2Request('/api/v1/auth/register', 'POST', {
    email,
    password,
    verify_code: code,
  });

  if (registerRes.data?.code !== 0) {
    throw new Error('注册失败: ' + JSON.stringify(registerRes.data));
  }

  const accessToken = registerRes.data.data.access_token;
  console.log('注册成功! Token:', accessToken.substring(0, 30) + '...');

  console.log('创建 API Key...');
  const keyRes = await ak2Request('/api/v1/keys', 'POST', {
    name: 'auto',
    group_id: 2,
  }, accessToken);

  if (keyRes.data?.code !== 0) {
    throw new Error('创建 Key 失败: ' + JSON.stringify(keyRes.data));
  }

  const apiKey = keyRes.data.data.key;
  console.log('API Key:', apiKey);

  saveAccount(email, password, apiKey);
  console.log('账号已追加到:', ACCOUNTS_FILE);
  console.log('Key已追加到:', KEYS_FILE);

  return { email, password, access_token: accessToken, api_key: apiKey };
}

async function run() {
  console.log('=== ak2.store 自动注册 ===');
  console.log('目标数量:', REGISTER_COUNT);
  console.log('总时长(分钟):', REGISTER_DURATION);
  console.log('邮箱服务:', MAIL_SERVICE);

  const startTime = Date.now();
  const endTime = startTime + REGISTER_DURATION * 60 * 1000;
  let totalCount = 0;
  let successCount = 0;
  let failCount = 0;

  while (totalCount < REGISTER_COUNT && Date.now() < endTime) {
    const remainingCount = REGISTER_COUNT - totalCount;
    const remainingTime = Math.max(0, Math.ceil((endTime - Date.now()) / 1000));
    console.log('\n────────────────────────────────────────');
    console.log(`进度: ${totalCount}/${REGISTER_COUNT} | 剩余: ${remainingCount}个`);
    console.log(`成功: ${successCount} | 失败: ${failCount}`);
    console.log(`时间: ${remainingTime}秒剩余`);
    console.log('────────────────────────────────────────');

    try {
      await register();
      successCount++;
      totalCount++;
      console.log(`\n✅ 成功注册 (总计: ${totalCount}/${REGISTER_COUNT})`);
    } catch (e) {
      failCount++;
      totalCount++;
      console.error('\n❌ 注册失败:', e.message);
      console.error(`(总计: ${totalCount}/${REGISTER_COUNT})`);
      console.error('继续下一个...');
    }

    if (totalCount < REGISTER_COUNT && Date.now() < endTime) {
      const wait = 5000 + Math.random() * 5000;
      console.log(`\n等待 ${Math.ceil(wait / 1000)} 秒...`);
      await new Promise(r => setTimeout(r, wait));
    }
  }

  console.log('\n========================================');
  console.log('注册完成');
  console.log('========================================');
  console.log('总计:', totalCount);
  console.log('成功:', successCount);
  console.log('失败:', failCount);
  console.log('账号文件:', ACCOUNTS_FILE);
  console.log('Key文件:', KEYS_FILE);
}

run().catch((e) => {
  console.error('运行失败:', e);
  process.exit(1);
});
