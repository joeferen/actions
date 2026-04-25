const { chromium } = require('playwright');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

const PROXY = process.env.PROXY || null;
const TEMPMAIL_API_BASE = 'api.tempmail.lol';

const MAILFREE_BASE = process.env.MAILFREE_BASE || 'https://mailfree.smanx.xx.kg';
const MAILFREE_JWT_TOKEN = process.env.MAILFREE_JWT_TOKEN || 'auto';
const MAILFREE_DOMAIN_INDEX_DEFAULT = parseInt(process.env.MAILFREE_DOMAIN_INDEX || '0', 10);
const MAIL_SERVICE = process.env.MAIL_SERVICE || 'mailfree';

const GITHUB_TOKEN = process.env.GITHUB_TOKEN || '';
const GISTS_ID = process.env.GISTS_ID || '';

function parseArgs() {
  const args = {
    count: parseInt(process.env.REGISTER_COUNT || '1', 10),
    duration: parseInt(process.env.REGISTER_DURATION || '60', 10),
    workers: parseInt(process.env.WORKERS || '1', 10),
    proxy: PROXY,
    mailService: MAIL_SERVICE,
    mailfreeBase: MAILFREE_BASE,
    mailfreeJwtToken: MAILFREE_JWT_TOKEN,
    mailfreeDomainIndex: MAILFREE_DOMAIN_INDEX_DEFAULT,
    githubToken: GITHUB_TOKEN,
    gistsId: GISTS_ID,
    headless: process.env.HEADLESS === 'true',
  };

  for (let i = 2; i < process.argv.length; i++) {
    const arg = process.argv[i];
    if (arg === '--count' && i + 1 < process.argv.length) {
      args.count = parseInt(process.argv[++i], 10);
    } else if (arg === '--duration' && i + 1 < process.argv.length) {
      args.duration = parseInt(process.argv[++i], 10);
    } else if (arg === '--workers' && i + 1 < process.argv.length) {
      args.workers = parseInt(process.argv[++i], 10);
    } else if (arg === '--proxy' && i + 1 < process.argv.length) {
      args.proxy = process.argv[++i];
    } else if (arg === '--mail-service' && i + 1 < process.argv.length) {
      args.mailService = process.argv[++i];
    } else if (arg === '--mailfree-base' && i + 1 < process.argv.length) {
      args.mailfreeBase = process.argv[++i];
    } else if (arg === '--mailfree-jwt-token' && i + 1 < process.argv.length) {
      args.mailfreeJwtToken = process.argv[++i];
    } else if (arg === '--mailfree-domain-index' && i + 1 < process.argv.length) {
      args.mailfreeDomainIndex = parseInt(process.argv[++i], 10);
    } else if (arg === '--github-token' && i + 1 < process.argv.length) {
      args.githubToken = process.argv[++i];
    } else if (arg === '--gists-id' && i + 1 < process.argv.length) {
      args.gistsId = process.argv[++i];
    } else if (arg === '--headless' && i + 1 < process.argv.length) {
      args.headless = process.argv[++i] === 'true';
    } else if (arg === '--headless') {
      args.headless = true;
    }
  }

  return args;
}

const DATA_DIR = path.join(__dirname, 'data');
const ACCOUNTS_FILE = path.join(DATA_DIR, 'accounts.txt');

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function request(options, data = null, proxyUrl = null) {
  return new Promise((resolve, reject) => {
    if (proxyUrl) {
      const proxyObj = new URL(proxyUrl);
      const isHttps = options.port === 443 || options.hostname.includes('deepseek');
      
      const connectReq = (isHttps ? https : http).request({
        host: proxyObj.hostname,
        port: proxyObj.port || (isHttps ? 443 : 80),
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
        });

        secureSocket.on('secureConnect', () => {
          const req = https.request({
            ...options,
            socket: secureSocket,
            agent: false,
          }, (res) => {
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

        secureSocket.on('error', (err) => {
          const fallbackOptions = {
            ...options,
            agent: false,
            rejectUnauthorized: false,
            host: proxyObj.hostname,
            port: proxyObj.port,
            path: `https://${options.hostname}${options.path}`,
            headers: {
              ...options.headers,
              Host: options.hostname,
            },
          };
          
          const proxyReq = https.request(fallbackOptions, (res) => {
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
      });

      connectReq.on('error', reject);
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

async function mailfreeGetDomains(MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/domains`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  }, null);
  if (res.status !== 200) {
    throw new Error(`获取域名失败: ${res.status}`);
  }
  return res.data;
}

async function mailfreeCreateEmail(local, domainIndex = 0, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/create`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    },
    body: JSON.stringify({ local, domainIndex }),
  }, null);
  if (res.status !== 200) {
    throw new Error(`创建邮箱失败: ${res.status} ${res.body}`);
  }
  return res.data;
}

async function mailfreeGetEmails(mailbox, limit = 20, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/emails?mailbox=${encodeURIComponent(mailbox)}&limit=${limit}`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  }, null);
  if (res.status !== 200) {
    return [];
  }
  return res.data;
}

async function mailfreeGetEmailDetail(emailId, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY) {
  const res = await requestHttps(`${MAILFREE_BASE}/api/email/${emailId}`, {
    headers: {
      'Accept': 'application/json',
      'X-Admin-Token': MAILFREE_JWT_TOKEN,
    }
  }, null);
  if (res.status !== 200) {
    return {};
  }
  return res.data;
}

async function mailfreeDeleteEmail(mailbox, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY) {
  try {
    const res = await requestHttps(`${MAILFREE_BASE}/api/delete?mailbox=${encodeURIComponent(mailbox)}`, {
      method: 'DELETE',
      headers: {
        'Accept': 'application/json',
        'X-Admin-Token': MAILFREE_JWT_TOKEN,
      }
    }, null);
    console.log('MailFree email deleted:', mailbox);
    return res;
  } catch (e) {
    console.warn('Failed to delete MailFree email:', e.message);
  }
}

async function mailfreePollVerifyCode(email, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY, timeout = 300) {
  const start = Date.now();
  const intervals = [5000, 6000, 8000, 10000, 12000, 15000];
  let idx = 0;
  const seenIds = new Set();

  while (Date.now() - start < timeout * 1000) {
    try {
      console.log('Checking MailFree inbox...');
      const emails = await mailfreeGetEmails(email, 10, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY);
      console.log('Inbox check result:', { emailsCount: emails.length });

      for (const mail of emails) {
        const msgId = mail.id;
        if (!msgId || seenIds.has(msgId)) continue;
        seenIds.add(msgId);

        const sender = (mail.sender || '').toLowerCase();
        const subject = mail.subject || '';
        const preview = mail.preview || '';
        const verificationCode = mail.verification_code;

        console.log('Email received:', { sender, subject });

        let content = `${subject} ${preview}`;
        let code = null;

        if (verificationCode) {
          code = extractVerifyCode(String(verificationCode));
        } else {
          code = extractVerifyCode(content);
        }

        if (!code) {
          const detail = await mailfreeGetEmailDetail(msgId, MAILFREE_BASE, MAILFREE_JWT_TOKEN, PROXY);
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
    console.log(`Waiting ${wait/1000}s for email...`);
    await new Promise(r => setTimeout(r, wait));
  }

  throw new Error('验证码超时');
}

async function requestHttps(url, options = {}) {
  const urlObj = new URL(url);
  const reqOptions = {
    hostname: urlObj.hostname,
    port: urlObj.port || 443,
    path: urlObj.pathname + urlObj.search,
    method: options.method || 'GET',
    headers: options.headers || {},
  };
  return request(reqOptions, options.body, null);
}

async function createTempMail(prefix = null, retries = 3) {
  const body = {};
  if (prefix) body.prefix = prefix;

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

  for (let i = 0; i < retries; i++) {
    try {
      const res = await request(options, JSON.stringify(body), null);
      if (res.status !== 200 && res.status !== 201) {
        console.warn(`创建邮箱失败 (尝试 ${i+1}/${retries}): ${res.status}`);
        await new Promise(r => setTimeout(r, 2000));
        continue;
      }
      return { address: res.data.address, token: res.data.token };
    } catch (e) {
      console.warn(`创建邮箱错误 (尝试 ${i+1}/${retries}):`, e.message);
      if (i < retries - 1) {
        await new Promise(r => setTimeout(r, 2000));
      }
    }
  }
  throw new Error('创建邮箱失败，已达到最大重试次数');
}

async function getTempMailEmails(token, retries = 3) {
  const options = {
    hostname: TEMPMAIL_API_BASE,
    port: 443,
    path: `/v2/inbox?token=${encodeURIComponent(token)}`,
    method: 'GET',
    headers: {
      'Accept': 'application/json',
    }
  };

  for (let i = 0; i < retries; i++) {
    try {
      const res = await request(options, null, null);
      if (res.status !== 200) {
        console.warn(`获取邮箱失败 (尝试 ${i+1}/${retries}): ${res.status}`);
        if (i < retries - 1) await new Promise(r => setTimeout(r, 2000));
        continue;
      }
      return res.data;
    } catch (e) {
      console.warn(`获取邮箱错误 (尝试 ${i+1}/${retries}):`, e.message);
      if (i < retries - 1) await new Promise(r => setTimeout(r, 2000));
    }
  }
  return { emails: [], expired: false };
}

function extractVerifyCode(content) {
  const match = content.match(/(?<!\d)(\d{6})(?!\d)/);
  return match ? match[1] : null;
}

async function pollVerifyCode(email, inboxToken, timeout = 300) {
  const start = Date.now();
  const intervals = [5000, 6000, 8000, 10000, 12000, 15000];
  let idx = 0;

  while (Date.now() - start < timeout * 1000) {
    try {
      console.log('Checking email inbox...');
      const data = await getTempMailEmails(inboxToken, 5);
      console.log('Inbox check result:', { 
        emailsCount: data.emails?.length || 0, 
        expired: data.expired 
      });
      
      if (data.expired) {
        throw new Error('收件箱已过期');
      }

      for (const mail of data.emails || []) {
        const sender = (mail.from || '').toLowerCase();
        const subject = mail.subject || '';
        const body = mail.body || '';
        const html = mail.html || '';

        console.log('Email received:', { sender, subject });

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
    console.log(`Waiting ${wait/1000}s for email...`);
    await new Promise(r => setTimeout(r, wait));
  }

  throw new Error('验证码超时');
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

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
];

function getRandomUserAgent() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

function randomPrefix() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let prefix = '';
  for (let i = 0; i < 10; i++) {
    prefix += chars[Math.floor(Math.random() * chars.length)];
  }
  return prefix;
}

function saveAccount(email, password) {
  if (email === password) {
    fs.appendFileSync(ACCOUNTS_FILE, `${email}\n`, 'utf8');
  } else {
    fs.appendFileSync(ACCOUNTS_FILE, `${email}|${password}\n`, 'utf8');
  }
}

async function uploadToGists(args) {
  if (!args.githubToken || !args.gistsId) {
    console.log('GITHUB_TOKEN or GISTS_ID not configured, skipping upload');
    return;
  }

  try {
    const localContent = fs.readFileSync(ACCOUNTS_FILE, 'utf8');
    const localAccounts = localContent.split('\n').filter(line => line.trim());
    
    console.log('Downloading remote accounts from Gists...');
    const url = `https://api.github.com/gists/${args.gistsId}`;
    
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `token ${args.githubToken}`,
        'Accept': 'application/vnd.github.v3+json',
      }
    });

    let remoteAccounts = [];
    if (response.ok) {
      const gistData = await response.json();
      if (gistData.files && gistData.files['accounts.txt']) {
        const remoteContent = gistData.files['accounts.txt'].content;
        remoteAccounts = remoteContent.split('\n').filter(line => line.trim());
        console.log(`Found ${remoteAccounts.length} remote accounts`);
      }
    }

    const localSet = new Set(localAccounts.map(acc => acc.split('|')[0].trim()));
    const mergedAccounts = [...remoteAccounts];
    
    for (const account of localAccounts) {
      const email = account.split('|')[0].trim();
      if (!localSet.has(email)) {
        mergedAccounts.push(account);
      }
    }

    const mergedContent = mergedAccounts.join('\n') + '\n';
    fs.writeFileSync(ACCOUNTS_FILE, mergedContent, 'utf8');
    console.log(`Merged accounts saved. Total: ${mergedAccounts.length}`);

    const uploadData = JSON.stringify({
      description: 'DeepSeek Accounts',
      files: {
        'accounts.txt': {
          content: mergedContent
        }
      }
    });

    const uploadResponse = await fetch(url, {
      method: 'PATCH',
      headers: {
        'Authorization': `token ${args.githubToken}`,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
      },
      body: uploadData
    });

    if (uploadResponse.ok) {
      console.log('Successfully uploaded to Gists!');
    } else {
      const error = await uploadResponse.text();
      console.error('Failed to upload to Gists:', error);
    }
  } catch (e) {
    console.error('Error uploading to Gists:', e.message);
  }
}

async function waitForWaf(page, retries = 3) {
  console.log('Waiting for WAF verification...');
  for (let attempt = 0; attempt < retries; attempt++) {
    for (let i = 0; i < 60; i++) {
      const title = await page.title();
      console.log(`[${i+1}] Title: ${title}`);
      
      if (title.includes('Human') || title.includes('Verification')) {
        console.log('Human Verification detected, stopping registration!');
        return false;
      }
      
      if (!title.includes('Human') && !title.includes('Verification')) {
        console.log('WAF passed!');
        
        await page.waitForTimeout(3000);
        const inputs = await page.$$('input');
        if (inputs.length < 3) {
          console.log('Form inputs not found, reloading page...');
          await page.reload({ waitUntil: 'domcontentloaded', timeout: 180000 });
          await page.waitForTimeout(15000);
          const newInputs = await page.$$('input');
          if (newInputs.length < 3) {
            console.log('Still no form inputs after reload');
          }
        }
        
        return true;
      }
      
      const beginBtn = await page.$('button:has-text("Begin")');
      if (beginBtn) {
        console.log('Clicking Begin button...');
        await beginBtn.click();
        await page.waitForTimeout(5000);
      }
      
      await page.waitForTimeout(5000);
    }
    console.log('WAF timeout, retrying...');
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 180000 });
    await page.waitForTimeout(15000);
  }
  console.log('WAF max retries reached, continuing anyway...');
  return false;
}

async function register(browser, args, maxRetries = 1) {
  let email = '';
  let password = '';
  
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    console.log('\n=== Registration Start ===');
    
    const context = await browser.newContext({
      locale: 'en-US',
      viewport: { width: 1280, height: 800 },
      userAgent: getRandomUserAgent(),
      deviceScaleFactor: Math.random() > 0.5 ? 2 : 1,
      isMobile: Math.random() > 0.8,
      hasTouch: Math.random() > 0.8,
      timezoneId: 'America/New_York',
      permissions: ['geolocation'],
      geolocation: { latitude: 40.7128 + Math.random() * 10, longitude: -74.0060 + Math.random() * 10 },
    });

    const page = await context.newPage();

    try {
      console.log('Opening DeepSeek signup page...');
      await page.goto('https://chat.deepseek.com/sign_up', { 
        waitUntil: 'domcontentloaded',
        timeout: 180000 
      });

      await page.waitForTimeout(20000);

      const wafPassed = await waitForWaf(page);
      if (!wafPassed) {
        console.log('WAF verification failed, stopping entire script!');
        await browser.close();
        process.exit(1);
      }

      console.log('Waiting for form to load...');
      for (let i = 0; i < 30; i++) {
        const inputs = await page.$$('input');
        if (inputs.length >= 4) {
          console.log('Form loaded!');
          break;
        }
        await page.waitForTimeout(2000);
      }

      console.log('Creating mail...');
      
      let email, inboxToken;
      if (args.mailService === 'mailfree') {
        console.log('Using MailFree service...');
        const domains = await mailfreeGetDomains(args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
        const local = randomPrefix();
        const emailInfo = await mailfreeCreateEmail(local, args.mailfreeDomainIndex, args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
        email = emailInfo.address || emailInfo.email;
        inboxToken = null;
      } else {
        console.log('Using TempMail service...');
        const inbox = await createTempMail(randomPrefix(), 5);
        email = inbox.address;
        inboxToken = inbox.token;
      }
      
      const password = email;

      console.log('Email:', email);
      console.log('Password:', password);

      console.log('Checking if page is loaded...');
      let pageText = await page.evaluate(() => document.body.innerText);
      if (!pageText || pageText.trim().length < 10) {
        console.log('Page is blank, reloading...');
        await page.reload({ waitUntil: 'domcontentloaded', timeout: 180000 });
        await page.waitForTimeout(15000);
        const wafPassed2 = await waitForWaf(page);
        if (!wafPassed2) {
          console.log('WAF verification failed after reload, stopping entire script!');
          await browser.close();
          process.exit(1);
        }
      }

      if (pageText.includes('phone number') || pageText.includes('手机号') || pageText.includes('+86')) {
        console.log('Page requires phone number registration, marking as failed!');
        if (args.mailService === 'mailfree' && email) {
          await mailfreeDeleteEmail(email, args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
        }
        await context.close();
        return { email: '', password: '', success: false };
      }

      console.log('Waiting for form inputs...');
      for (let i = 0; i < 15; i++) {
        const inputs = await page.$$('input');
        if (inputs.length >= 4) {
          console.log('Form inputs found!');
          break;
        }
        await page.waitForTimeout(2000);
      }

      console.log('Filling email...');
      await page.fill('input[placeholder="Email address"]', email);
      await page.waitForTimeout(500);
      
      console.log('Filling password...');
      await page.fill('input[placeholder="Password"]', password);
      await page.waitForTimeout(500);
      
      console.log('Filling confirm password...');
      await page.fill('input[placeholder="Confirm password"]', password);
      await page.waitForTimeout(1000);

      console.log('Clicking Send code...');
      const sendCodeBtn = await page.$('button:has-text("Send code")');
      if (sendCodeBtn) {
        await sendCodeBtn.click();
      } else {
        console.log('Send code button not found, trying to click by text...');
        await page.click('text=Send code');
      }
      
      console.log('Waiting for send code response...');
      let sendCodeSuccess = false;
      for (let i = 0; i < 20; i++) {
        await page.waitForTimeout(1500);
        
        const pageText = await page.evaluate(() => document.body.innerText);
        console.log(`[${i+1}] Page text:`, pageText.substring(0, 300));
        
        if (pageText.toLowerCase().includes('code sent') || 
            pageText.toLowerCase().includes('sent successfully') ||
            pageText.toLowerCase().includes('已发送') ||
            pageText.toLowerCase().includes('发送成功') ||
            pageText.toLowerCase().includes('resend') ||
            pageText.toLowerCase().includes('60s') ||
            pageText.toLowerCase().includes('second')) {
          console.log('Verification code sent successfully!');
          sendCodeSuccess = true;
          break;
        }
        
        const errorText = pageText.toLowerCase();
        if (errorText.includes('error') || errorText.includes('fail') || errorText.includes('invalid')) {
          console.log('Failed to send verification code!');
          throw new Error('Failed to send verification code: ' + pageText.substring(0, 200));
        }
      }
      
      if (!sendCodeSuccess) {
        console.log('Warning: Could not confirm code was sent, but continuing...');
      }
      
      console.log('Waiting for code email...');
      let code;
      if (args.mailService === 'mailfree') {
        code = await mailfreePollVerifyCode(email, args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
      } else {
        code = await pollVerifyCode(email, inboxToken);
      }
      console.log('Verification code:', code);

      console.log('Filling code...');
      await page.fill('input[placeholder="Code"]', code);
      await page.waitForTimeout(1000);

      console.log('Clicking Sign up...');
      const signUpBtn = await page.$('button:has-text("Sign up")');
      if (signUpBtn) {
        await signUpBtn.click({ timeout: 10000 });
      } else {
        await page.click('text=Sign up', { timeout: 10000 });
      }

      await page.waitForTimeout(5000);

      console.log('Checking for date of birth dialog...');
      for (let i = 0; i < 10; i++) {
        const dialogText = await page.evaluate(() => document.body.innerText);
        if (dialogText.includes('When were you born') || dialogText.includes('出生')) {
          console.log('Date of birth dialog detected, selecting date...');
          
          const yearSelect = await page.$('select, [role="combobox"]');
          if (yearSelect) {
            await yearSelect.selectOption('2000');
          }
          
          await page.waitForTimeout(1000);
          
          const monthSelects = await page.$$('select, [role="combobox"]');
          if (monthSelects.length > 1) {
            await monthSelects[1].selectOption('02');
          } else if (monthSelects.length === 1) {
            await monthSelects[0].selectOption('02');
          }
          
          await page.waitForTimeout(1000);
          
          const confirmBtn = await page.$('button:has-text("Confirm"), button:has-text("确定"), button:has-text("Next"), button:has-text("Continue")');
          if (confirmBtn) {
            await confirmBtn.click();
          }
          
          console.log('Date selected!');
          break;
        }
        await page.waitForTimeout(2000);
      }

      const currentUrl = page.url();
      console.log('Current URL:', currentUrl);

      const success = currentUrl.includes('chat') || currentUrl.includes('home') || (!currentUrl.includes('sign_up') && !currentUrl.includes('login'));
      
      if (success) {
        console.log('Registration successful!');
        saveAccount(email, password);
        console.log('Account saved to:', ACCOUNTS_FILE);
      } else {
        console.log('Registration may have failed. URL:', currentUrl);
        const bodyText = await page.evaluate(() => document.body.innerText);
        console.log('Page text:', bodyText.substring(0, 500));
      }

      if (args.mailService === 'mailfree' && email) {
        await mailfreeDeleteEmail(email, args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
      }

      await context.close();
      return { email, password, success };
      
    } catch (e) {
      console.error(`Attempt ${attempt} failed:`, e.message);
      
      if (args.mailService === 'mailfree' && email) {
        await mailfreeDeleteEmail(email, args.mailfreeBase, args.mailfreeJwtToken, args.proxy);
      }
      
      await context.close();
    }
  }
  
  return { email: '', password: '', success: false };
}

async function run() {
  const args = parseArgs();
  const { count, duration, workers, proxy, mailService, mailfreeBase, mailfreeJwtToken, mailfreeDomainIndex } = args;

  console.log('=== DeepSeek Auto Register ===');
  console.log('Count:', count);
  console.log('Duration:', duration, 'minutes');
  console.log('Workers:', workers);
  console.log('Proxy:', proxy);
  console.log('Mail Service:', mailService);
  console.log('Headless:', headless);
  console.log('===========================');

  const browserOptions = {
    headless: headless,
    channel: 'chromium',
  };
  
  if (proxy) {
    browserOptions.proxy = { server: proxy };
    console.log('Using proxy:', proxy);
  }
  
  const browser = await chromium.launch(browserOptions);

  let successCount = 0;
  let failCount = 0;
  const startTime = Date.now();
  const endTime = startTime + duration * 60 * 1000;

  for (let i = 0; i < count && Date.now() < endTime; i++) {
    console.log(`\n=== Registration ${i + 1}/${count} ===`);
    try {
      const result = await register(browser, args);
      if (result.success) {
        successCount++;
        console.log(`\n✅ Success (${successCount}/${count})`);
      } else {
        failCount++;
        console.log(`\n❌ Failed (${failCount}/${count})`);
      }
    } catch (e) {
      failCount++;
      console.error(`\n❌ Error:`, e.message);
    }

    if (i < count - 1 && Date.now() < endTime) {
      const wait = 5000 + Math.random() * 5000;
      console.log(`Waiting ${Math.ceil(wait / 1000)} seconds...`);
      await new Promise(r => setTimeout(r, wait));
    }
  }

  await browser.close();

  if (args.githubToken && args.gistsId) {
    await uploadToGists(args);
  }

  console.log('\n========================================');
  console.log('Registration Complete');
  console.log('========================================');
  console.log('Total:', count);
  console.log('Success:', successCount);
  console.log('Failed:', failCount);
  console.log('Accounts file:', ACCOUNTS_FILE);
}

run().catch(console.error);
