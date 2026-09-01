# -*- coding: utf-8 -*-
"""
SmsForwarder Webhook 接收服务（自研版）
======================================
为 SmsForwarder 的「Webhook」发送通道提供接收端，并在自研前端页面实时展示。

与 SmsForwarder 源码 (WebhookUtils.kt) 对齐的关键点：
1. 签名算法：sign = URLEncoder(Base64(HmacSHA256(timestamp + "\\n" + secret, secret)))
   - timestamp 为毫秒级时间戳；服务端需先 unquote 还原再比对，并校验时间戳新鲜度防重放。
2. 请求体为 JSON，字段用 {{FROM}}/{{SMS}} 等双花括号标签或 [from]/[content] 方括号占位。
3. 服务端响应体含关键字「ok」，SmsForwarder 会据其判定转发成功（通道配置"响应关键字=ok"）。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SECRET = os.getenv("SECRET", "")            # HMAC 密钥，与 SmsForwarder 通道"加密密钥"一致
PAGE_TOKEN = os.getenv("PAGE_TOKEN", "")    # 前端页面访问令牌（浏览器 URL ?token=xxx）
WEBHOOK_KEY = os.getenv("WEBHOOK_KEY", "")  # 可选：webhook 额外鉴权（Header: X-Webhook-Key）
DATA_DIR = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent / "data"))
PORT = int(os.getenv("PORT", "8322"))

MAX_BODY_BYTES = 64 * 1024       # 请求体上限 64KB
ALLOWED_SKEW_MS = 5 * 60 * 1000  # 时间戳允许偏差 ±5 分钟（防重放）

DB_PATH = os.path.join(DATA_DIR, "sms.db")
FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sms-webhook")

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_number  TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    phone_area   TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    sim_slot     TEXT NOT NULL DEFAULT '',
    device_name  TEXT NOT NULL DEFAULT '',
    receiver_number TEXT NOT NULL DEFAULT '',
    receive_time TEXT NOT NULL DEFAULT '',
    code         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sms)").fetchall()}
        if "receiver_number" not in columns:
            conn.execute("ALTER TABLE sms ADD COLUMN receiver_number TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def _query(sql: str, args: tuple = ()):
    """执行查询，返回 dict 列表。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _insert(sql: str, args: tuple = ()) -> int:
    """执行插入，返回新行 id。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


# 验证码提取：
# 1) 先剥离干扰段（日期 2026-09-01 / 时间 08:50:56 / 带字母前缀 a36005683 / 掩码手机号 159****9996）
# 2) 优先匹配「验证码/口令/密码/token/code」等关键词后的 4~8 位数字
# 3) 兜底：取清理后文本中最后一个 4~8 位纯数字
_CODE_NOISE_RE = re.compile(
    r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}(?:[日号])?"  # 日期
    r"|\d{1,2}[:：]\d{2}(?::\d{2})?"                  # 时间
    r"|[a-zA-Z]\d{4,8}"                               # 带字母前缀（如 a36005683）
    r"|\d{3}\*+\d{3,4}"                               # 掩码手机号（如 159****9996）
)
_CODE_KEYWORD_RE = re.compile(
    r"(?:验证码|动态码|校验码|安全码|登录码|口令|密码|token|code|pin|otp)"
    r"\s*[为是:：]*\s*(\d{4,8})",
    re.IGNORECASE,
)
_CODE_BARE_RE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")


def extract_code(content: str) -> str:
    text = _CODE_NOISE_RE.sub(" ", content or "")
    m = _CODE_KEYWORD_RE.search(text)
    if m:
        return m.group(1)
    codes = _CODE_BARE_RE.findall(text)
    return codes[-1] if codes else ""


# ---------------------------------------------------------------------------
# SSE 实时推送（事件总线）
# ---------------------------------------------------------------------------
class EventBus:
    def __init__(self) -> None:
        self._clients: set = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, item: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        if not clients or self._loop is None:
            return
        for q in clients:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, item)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._clients.discard(q)


bus = EventBus()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus.set_loop(asyncio.get_running_loop())
    init_db()
    if not SECRET and not WEBHOOK_KEY:
        logger.warning("SECRET 与 WEBHOOK_KEY 均为空，webhook 端点未做鉴权（仅建议本地调试）！")
    yield


app = FastAPI(title="SMS Webhook Receiver", version="1.1.0", lifespan=lifespan)

# 前端静态资源（index.html 引用的 style.css / app.js，无敏感数据、无需 token）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
@app.middleware("http")
async def page_auth(request: Request, call_next):
    """/api/* 的读取接口需要 PAGE_TOKEN（浏览器通过 ?token=xxx 携带）。"""
    path = request.url.path
    if PAGE_TOKEN and path.startswith("/api/"):
        if request.query_params.get("token") != PAGE_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def verify_signature(payload: dict) -> tuple[bool, str]:
    """校验 SmsForwarder 生成的 HMAC-SHA256 签名。"""
    if not SECRET:
        return True, ""
    timestamp = payload.get("timestamp")
    sign = payload.get("sign")
    if not timestamp or not sign:
        return False, "缺少 timestamp/sign 字段（请确认通道填写了加密密钥并保留占位符）"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "timestamp 非法"
    if abs(time.time() * 1000 - ts) > ALLOWED_SKEW_MS:
        return False, "timestamp 超时（防重放）"
    try:
        got = unquote(sign)  # Android 端做了 URLEncoder，先还原
        string_to_sign = f"{ts}\n{SECRET}"
        digest = hmac.new(SECRET.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(got, expected), "sign 校验失败"
    except Exception as e:  # noqa: BLE001
        return False, f"sign 解析异常: {e}"


# ---------------------------------------------------------------------------
# Webhook 接收
# ---------------------------------------------------------------------------
@app.post("/v1/sms/receive")
def receive(
    request: Request,
    payload: dict = Body(...),
    x_webhook_key: str = Header(default="", alias="X-Webhook-Key"),
):
    # 1. 体积限制
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="body too large")
        except ValueError:
            pass

    # 2. 鉴权：X-Webhook-Key 或 HMAC 签名，两者任一配置即强制校验
    if WEBHOOK_KEY and x_webhook_key != WEBHOOK_KEY:
        raise HTTPException(status_code=401, detail="invalid webhook key")
    ok, err = verify_signature(payload)
    if not ok:
        logger.warning("reject webhook: %s", err)
        raise HTTPException(status_code=401, detail=err)

    # 3. 字段归一化（兼容双花括号标签 / 方括号占位符两种命名）
    from_number = str(payload.get("from") or payload.get("sender") or "")
    content = str(payload.get("content") or payload.get("sms") or payload.get("msg") or "")
    sim_slot = str(payload.get("sim_slot") or payload.get("card_slot") or "")
    device_name = str(payload.get("device_name") or payload.get("device_mark") or "")
    receiver_number = str(payload.get("receiver_number") or payload.get("receive_number") or "").strip()
    receive_time = str(payload.get("receive_time") or "")
    code = extract_code(content)

    new_id = _insert(
        """INSERT INTO sms (from_number, contact_name, phone_area, content, sim_slot, device_name, receiver_number, receive_time, code)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
        (
            from_number,
            str(payload.get("contact_name") or ""),
            str(payload.get("phone_area") or ""),
            content,
            sim_slot,
            device_name,
            receiver_number,
            receive_time,
            code,
        ),
    )

    item = {
        "id": new_id,
        "from_number": from_number,
        "contact_name": str(payload.get("contact_name") or ""),
        "phone_area": str(payload.get("phone_area") or ""),
        "content": content,
        "sim_slot": sim_slot,
        "device_name": device_name,
        "receiver_number": receiver_number,
        "receive_time": receive_time,
        "code": code,
    }
    bus.publish({**item, "type": "sms"})
    logger.info("new sms from %s: %s", from_number, content[:60].replace("\n", " "))
    # 响应体包含 ok，供 SmsForwarder 响应关键字判定成功
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 查询 API
# ---------------------------------------------------------------------------
@app.get("/api/messages")
def messages(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = _query("SELECT * FROM sms ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    total = _query("SELECT COUNT(*) AS c FROM sms")[0]["c"]
    return {"items": rows, "total": total}


@app.get("/v1/sms/history")  # 兼容 sms_server 的路径，方便脚本复用
def history(limit: int = Query(10, ge=1, le=200), offset: int = Query(0, ge=0)):
    return messages(limit, offset)


@app.get("/v1/sms/code")
def get_code(phone_number: str = Query("", description="按发件号码查询最近验证码")):
    if phone_number:
        rows = _query(
            "SELECT * FROM sms WHERE from_number = ? AND code != '' ORDER BY id DESC LIMIT 10",
            (phone_number,),
        )
    else:
        rows = _query("SELECT * FROM sms WHERE code != '' ORDER BY id DESC LIMIT 10")
    return {"items": rows}


# ---------------------------------------------------------------------------
# 统计 / 管理接口（前端面板用）
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def stats():
    total = _query("SELECT COUNT(*) AS c FROM sms")[0]["c"]
    today = _query(
        "SELECT COUNT(*) AS c FROM sms WHERE created_at >= datetime('now','localtime','start of day')"
    )[0]["c"]
    week = _query(
        "SELECT COUNT(*) AS c FROM sms WHERE created_at >= datetime('now','localtime','-6 days','start of day')"
    )[0]["c"]
    code_count = _query("SELECT COUNT(*) AS c FROM sms WHERE code != ''")[0]["c"]
    senders = _query(
        "SELECT from_number, contact_name, COUNT(*) AS count FROM sms"
        " GROUP BY from_number ORDER BY count DESC LIMIT 10"
    )
    return {
        "total": total,
        "today": today,
        "week": week,
        "code_count": code_count,
        "senders": senders,
    }


@app.delete("/api/messages/{msg_id}")
def delete_message(msg_id: int):
    row = _query("SELECT id FROM sms WHERE id = ?", (msg_id,))
    if not row:
        raise HTTPException(status_code=404, detail="message not found")
    _query("DELETE FROM sms WHERE id = ?", (msg_id,))
    bus.publish({"type": "delete", "id": msg_id})
    return {"status": "ok"}


@app.delete("/api/messages")
def clear_messages():
    _query("DELETE FROM sms")
    bus.publish({"type": "clear"})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# SSE 实时推送
# ---------------------------------------------------------------------------
@app.get("/api/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# 页面与健康检查
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    if not FRONTEND_PATH.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html not found")
    return FileResponse(FRONTEND_PATH)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    # 注意：SSE 广播为进程内状态，生产请保持单 worker
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
