# SMS Webhook Receiver（自研版）

为 [SmsForwarder](https://github.com/pppscn/SmsForwarder) 定制的自托管 Webhook 接收端 + 自研展示页面。
Android 手机收到短信 → SmsForwarder 推送 → 你的 VPS（Docker）→ SQLite 存储 → 浏览器实时展示（SSE）。

```
Android (SmsForwarder)
   │  Webhook POST https://sms.example.com/v1/sms/receive
   │  JSON + HMAC-SHA256 签名 (timestamp + sign)
   ▼
VPS (Docker)
   └─ 本服务 (FastAPI, :8322)
        ├─ POST /v1/sms/receive   接收短信（验签 → 入库 → SSE 广播）
        ├─ GET  /api/messages     历史记录
        ├─ GET  /api/stream       SSE 实时推送
        └─ GET  /                 自研前端页面
```

## 一、快速部署（VPS）

```bash
cd sms-webhook-receiver
cp .env.example .env        # 编辑 .env，SECRET 和 PAGE_TOKEN 必改
docker compose up -d --build
```

- 页面：`http://你的VPS:8322/?token=你的PAGE_TOKEN`
- 数据持久化在 `./data/sms.db`（已挂载 volume）
- 健康检查：`GET /healthz`

> ⚠ 生产环境务必在前面加 HTTPS 反代（Caddy/Nginx），否则 HMAC 密钥会被抓包嗅探。示例见文末。

## 二、SmsForwarder 端配置

### 1. 新建发送通道 → 选「Webhook」

| 配置项 | 值 |
|---|---|
| 请求方式 | `POST` |
| 推送地址 | `https://你的域名/v1/sms/receive` |
| 请求头 | `Content-Type: application/json` |
| 加密密钥 | 与 `.env` 的 `SECRET` 完全一致（开启 HMAC 签名） |
| 响应关键字 | `ok`（服务端响应含 ok 判定成功） |

### 2. 请求体模板（JSON）

在「请求体」栏粘贴以下模板，App 会自动替换占位符并做 JSON 转义：

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

> 双花括号 `{{...}}` 是 App 内置标签（`{{SMS}}`、`{{FROM}}`、`{{RECEIVE_TIME}}`、`{{CARD_SLOT}}`
> `{{DEVICE_NAME}}`、`{{CONTACT_NAME}}`、`{{PHONE_AREA}}`、`{{LOCATION}}` 等）；
> `[timestamp]`、`[sign]` 是方括号占位符，**仅在填写了加密密钥时才会被替换**，所以密钥必填。

### 3. 保存 → 测发

点「测发」后 App 显示成功、VPS 日志出现 `new sms from ...` 即打通。
之后在「转发规则」里把短信/来电/通知关联到这个通道即可。

## 三、安全设计

- **HMAC-SHA256 签名**：与 SmsForwarder 源码 `WebhookUtils.kt` 对齐 ——
  `sign = URLEncoder(Base64(HmacSHA256(timestamp + "\n" + secret, secret)))`，
  服务端先 `unquote` 还原再 `hmac.compare_digest` 比对，并校验时间戳偏差 ≤ ±5 分钟（防重放）。
- **可选补充鉴权**：`.env` 配 `WEBHOOK_KEY` 后，SmsForwarder 请求头需携带 `X-Webhook-Key: 值`。
- **页面隔离**：所有 `/api/*` 读取接口需要 `PAGE_TOKEN`（浏览器 URL `?token=` 携带）。
- 请求体上限 64KB；`content-length` 预检。

## 四、API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/sms/receive` | 接收 webhook（验签/入库/广播） |
| GET | `/api/messages?limit=&offset=` | 历史记录（需 token） |
| GET | `/api/stream?token=` | SSE 实时事件流 |
| GET | `/api/stats` | 统计（总数/今日/近7天/验证码/热门发件人） |
| DELETE | `/api/messages/{id}` | 删除单条消息（需 token） |
| DELETE | `/api/messages` | 清空全部消息（需 token） |
| GET | `/v1/sms/history` | 兼容 sms_server 的历史接口 |
| GET | `/v1/sms/code?phone_number=` | 按号码查最近验证码 |
| GET | `/healthz` | 健康检查 |
| GET | `/` | 前端页面 |
| GET | `/static/*` | 前端静态资源（style.css / app.js） |

## 五、前端面板（Vue 3 · 零构建）

前端保持「FastAPI 直接服务静态文件」的极简部署方式（无需 node 构建），
逻辑用 **Vue 3**（CDN 按 jsdelivr → npmmirror → unpkg 依次加载，兼容国内外网络）重写，
并新增以下能力：

- 🎨 深色 / 浅色主题切换（默认跟随系统，记忆选择）
- 🔑 验证码高亮 + 一键复制（含二次音效）
- 🗂️ 按发件号码/联系人自动分组，组头可折叠
- 📜 历史消息「加载更多」分页（不再限于最新 50 条）
- 🗑️ 单条删除（二次确认态）与「清空全部数据」（弹窗确认，SSE 同步所有打开页面）
- 📊 统计面板：总条数 / 今日 / 近 7 天 / 验证码条数 + 热门发件人快捷筛选
- 🔔 桌面通知（新验证码弹通知）与提示音开关（验证码双音）
- 🔍 实时搜索过滤（号码 / 联系人 / 内容 / 验证码），消息进出场动画、玻璃拟态

## 六、本地调试

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate ; Linux/macOS: source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8322
```

用仓库里的 `test_send.py` 生成带签名 payload 模拟 App：

```bash
python test_send.py                  # 发送一条测试短信
python test_send.py --tampered       # 用错误密钥签名，应返回 401
python e2e.py                        # 端到端自测（含 SSE 实时推送断言）
```

## 七、HTTPS 反代（Caddy 示例）

```caddyfile
sms.example.com {
    reverse_proxy 127.0.0.1:8322
}
```

Caddy 自动申请证书，SmsForwarder 推送地址填 `https://sms.example.com/v1/sms/receive`。

## 目录结构

```
sms-webhook-receiver/
├── backend/
│   ├── main.py            # FastAPI 应用（接收/验签/SQLite/SSE/统计/删除/静态资源）
│   └── requirements.txt
├── frontend/
│   ├── index.html         # 前端骨架 + Vue 3 模板（零构建，FastAPI 直接服务）
│   ├── style.css          # 深/浅双主题、玻璃拟态、动画、响应式
│   └── app.js             # Vue 3 应用逻辑（分组/分页/统计/通知/主题等）
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```