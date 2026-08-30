# SMS 转发面板 · 完整部署与配置教程

把 Android 手机的短信（包括验证码）实时转发到你自己写的 Web 页面显示。
手机端用开源项目 [SmsForwarder](https://github.com/pppscn/SmsForwarder)（短信转发器），
服务器端用本仓库的自研接收服务（FastAPI + SQLite + SSE），通过 VPS + Docker + 1Panel 反向代理部署。

```
Android 手机 (SmsForwarder App)
   │  收到短信 → Webhook POST (HMAC-SHA256 签名)
   ▼
你的 VPS (Docker)
   └─ 本服务 (端口 8322)
        ├─ POST /v1/sms/receive   接收短信（验签→入库→SSE广播）
        ├─ GET  /api/messages     历史记录
        ├─ GET  /api/stream       SSE 实时推送
        └─ GET  /                 自研前端页面
   ▲
1Panel 反向代理 (Nginx) → HTTPS 443
   ▲
浏览器访问 https://sms.你的域名/?token=xxx
```

---

## 目录

1. [一、服务器部署（VPS + Docker）](#一服务器部署vps--docker)
2. [二、1Panel 反向代理 + HTTPS](#二1panel-反向代理--https)
3. [三、SmsForwarder App 配置](#三smsforwarder-app-配置)
4. [四、创建转发规则（短信 / 通知）](#四创建转发规则短信--通知)
5. [五、常见问题与排坑](#五常见问题与排坑)
6. [六、安全建议](#六安全建议)

---

## 一、服务器部署（VPS + Docker）

### 1.1 准备环境

VPS 需安装 Docker 与 Docker Compose：

```bash
# 安装 Docker（国内 VPS 用 daocloud 镜像更快）
curl -fsSL https://get.daocloud.io/docker | sh

# 验证
docker --version
docker compose version
```

### 1.2 上传项目

在本地项目根目录执行（也可用 `git clone`）：

```bash
scp -r sms-webhook-receiver root@你的VPS_IP:~
```

### 1.3 配置环境变量

```bash
cd ~/sms-webhook-receiver
cp .env.example .env
nano .env
```

`.env` 中**必须修改**两项（其余可保留默认）：

```ini
# 与 SmsForwarder App 里 Webhook 通道的「加密密钥」保持一致（重要！）
SECRET=换成一段很长的随机字符串

# 查看页面用的口令，浏览器访问时 URL 加 ?token=xxx
PAGE_TOKEN=换成另一段随机字符串
```

> `SECRET` 不要泄露给无关人，否则他人可伪造短信写入你的面板。
> 也可以再配 `WEBHOOK_KEY`，App 端请求头带 `X-Webhook-Key`，与 `SECRET` 二选一或同用。

### 1.4 构建并启动

```bash
docker compose up -d --build

# 查看日志（应无报错）
docker compose logs -f

# 健康检查（返回 {"status":"ok"} 即成功）
curl http://127.0.0.1:8322/healthz
```

数据持久化在 `./data/sms.db`（SQLite，已挂载 volume），容器重建不丢数据。

---

## 二、1Panel 反向代理 + HTTPS

### 2.1 解析域名

在 DNS 服务商给域名（如 `sms.liushang.online`）添加 **A 记录**，指向 VPS 公网 IP，等待生效。

### 2.2 创建反向代理站点

1. 登录 1Panel → 「网站」→「创建网站」
2. 类型选 **「反向代理」**
3. **主域名**：`sms.liushang.online`
4. **反向代理地址**：`127.0.0.1:8322`  ← ⚠️ 不要带 `http://` 前缀！

> ⚠️ 如果填成 `http://127.0.0.1:8322`，nginx 会报
> `invalid port in upstream "http://127.0.0.1:8322"`，站点起不来。

### 2.3 启用 HTTPS（自动证书）

1. 站点「设置」→「HTTPS」
2. 打开「启用 HTTPS」，证书来源选「**申请 Let's Encrypt 证书**」（自动续期）
3. 勾选「**HTTP 跳转 HTTPS**」

### 2.4 （推荐）收紧端口

对外只走 HTTPS，`8322` 不暴露公网。修改 `docker-compose.yml`：

```yaml
    ports:
      - "127.0.0.1:8322:8322"   # 只监听本机
```

重建生效：

```bash
docker compose up -d
```

---

## 三、SmsForwarder App 配置

### 3.1 新建 Webhook 通道

App 内：**短信转发 → 发送通道 → 页面右上角 ＋ → 选「Webhook」**

| 界面字段 | 填什么 |
|---|---|
| 名称 | 随意，如 `我的短信面板` |
| 启用 | 打开 |
| 请求方式 | `POST` |
| 推送地址 | `https://sms.liushang.online/v1/sms/receive` |
| 加密密钥 | 与服务器 `.env` 的 `SECRET` **完全一致** |
| 响应关键字 | `ok`（⚠️ 必须小写，见排坑 5.3） |
| 请求体 | 下方 JSON 模板 |
| 请求头 | 新增：`Content-Type` → `application/json` |

### 3.2 请求体模板

```json
{
  "from": "{{FROM}}",
  "contact_name": "{{CONTACT_NAME}}",
  "phone_area": "{{PHONE_AREA}}",
  "content": "{{SMS}}",
  "sim_slot": "{{CARD_SLOT}}",
  "device_name": "{{DEVICE_NAME}}",
  "receive_time": "{{RECEIVE_TIME}}",
  "timestamp": "[timestamp]",
  "sign": "[sign]"
}
```

> ⚠️ 必须保留 `"[timestamp]"` 和 `"[sign]"`，且**必须填了加密密钥**才会被替换成真实签名，
> 服务器验签才通过。

### 3.3 保存并测发

保存后点进通道 →「测发」，VPS 日志出现 `new sms from ...` 即打通。

---

## 四、创建转发规则（短信 / 通知）

### 4.1 短信规则（收普通短信 + 106 验证码短信）

**短信转发 → 转发规则 → ＋**

| 项 | 填什么 |
|---|---|
| 规则名称 | `全部短信` |
| 匹配类型 | **短信** |
| 卡槽 | **全部**（⚠️ 别只选卡槽1/卡槽2，见排坑 5.4） |
| 匹配条件 | 全部短信 即可 |
| 发送通道 | 勾选 `我的短信面板` |

### 4.2 App 通知规则（可选）

只在你需要转发**状态栏 App 推送**（如微信/银行 App 的通知）时才需要：

1. **设置 → 转发设置 → 打开「转发应用通知」**，并按提示授权「通知使用权」
2. 再建一条规则，匹配类型选 **「通知（APP通知）」**，勾选 Webhook 通道

> 注意：验证码如果来自 106 号段，它是**短信**不是 App 通知，走 4.1 即可，无需此规则。

---

## 五、常见问题与排坑

### 5.1 验证码短信（106号段）一条都没转发 ❗最常见

**现象**：普通手机号短信能转发，验证码短信全部没转发。
**根因**：国产 ROM（小米/华为/OPPO/vivo 等）在系统短信 App 里开启了**「验证码保护」**，
系统安全组件会拦截验证码短信的广播，任何第三方应用都收不到。

**解决**：打开系统短信 App → **更多设置 → 关闭「验证码保护」**（不同 ROM 叫法略不同，
也可能叫"验证码安全保护/验证码拦截"）。关闭后重启手机生效。

> 误判提醒：这项功能与「智能短信/卡片短信」（短信卡片化显示）无关——很多人先想到关
> 智能短信，但真正拦截广播的是「验证码保护」。请先检查这一项。

### 5.2 App 通知一个都没转发

**现象**：状态栏 App 推送不转发。
**解决**：确认「转发应用通知」总开关已打开 + 已授权「通知使用权」+ 建了"通知"类型的
转发规则（短信规则和通知规则是两套，短信规则不会转发通知）。

### 5.3 测发显示"请求失败：{status:ok}"

**现象**：服务器返回了 `{"status":"ok"}`，但 App 判失败。
**根因**：SmsForwarder 的响应关键字匹配是**大小写敏感**的（源码 `response.contains(...)`），
填了 `OK`/`Ok` 匹配不上小写的 `ok`。
**解决**：把「响应关键字」改成小写 **`ok`**。

### 5.4 短信规则匹配不上某些短信

**根因**：App 有时获取不到短信的卡槽信息（得到 `SIM0`），若规则限定卡槽1/2 则匹配不上。
**解决**：规则「卡槽」选 **「全部」**。

### 5.5 nginx 报 `invalid port in upstream`

**根因**：1Panel 反向代理地址带了 `http://` 前缀。
**解决**：改为 `127.0.0.1:8322`（不带协议前缀）。

### 5.6 App 测发报"证书验证失败"

**先做**：VPS 上 `curl -v https://sms.你的域名/healthz`，看证书是否有效、域名解析是否生效。
若证书自签/未签发 → 回 1Panel 重新申请 Let's Encrypt 证书。
若服务器全绿仍报错 → 查看 App 发送日志里的具体异常栈。

### 5.7 页面能开但收不到新短信

**依次检查**：
1. VPS 日志有没有 `new sms from ...`（没有 → App 没收到广播，看 5.1 / 5.2）
2. 转发规则有没有建、有没有开启、通道是否勾选
3. 页面 Token 是否正确（URL `?token=xxx`）

---

## 六、安全建议

1. **HTTPS 必须**：签名密钥走明文 HTTP 会被抓包，务必用 1Panel 反代 + 自动证书。
2. `.env` 两个密钥换成足够长的随机串，且**不要提交到 git**（`.gitignore` 已忽略 `.env`）。
3. 页面读取接口靠 `PAGE_TOKEN` 保护（URL `?token=`），不要裸奔。
4. 服务内置：body 上限 64KB + 时间戳 ±5 分钟防重放 + 可选 `X-Webhook-Key`。
5. **保持单 worker**（SSE 广播是进程内状态），docker-compose 已固定 `replicas: 1`，勿扩容。

---

## 附：本地调试 / 自测

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate ; Linux/macOS: source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8322
```

模拟 App 发一条带签名的短信：

```bash
python test_send.py                # 发送一条测试短信
python test_send.py --tampered     # 用错误密钥签名，应返回 401
python e2e.py                      # 端到端自测（含 SSE 实时推送断言）
```
