# -*- coding: utf-8 -*-
"""快速端到端自测：验证 SSE 实时推送 + API。用法: python e2e.py [secret]"""
import base64
import hashlib
import hmac
import json
import sys
import threading
import time
import urllib.request
from urllib.parse import quote

BASE = "http://127.0.0.1:8322"
SECRET = sys.argv[1] if len(sys.argv) > 1 else "test-secret"
TOKEN = "test-page-token"


def sign(timestamp_ms: str) -> str:
    s = f"{timestamp_ms}\n{SECRET}"
    d = hmac.new(SECRET.encode(), s.encode(), hashlib.sha256).digest()
    return quote(base64.b64encode(d).decode())


def post_sms(content: str) -> None:
    ts = str(int(time.time() * 1000))
    payload = {
        "from": "10086", "contact_name": "XX银行", "phone_area": "上海 上海",
        "content": content, "sim_slot": "SIM1", "device_name": "安卓备用机",
        "receive_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": ts, "sign": sign(ts),
    }
    req = urllib.request.Request(
        BASE + "/v1/sms/receive",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        print("POST ->", r.status, r.read().decode())


received = []


def consume_sse():
    req = urllib.request.Request(BASE + f"/api/stream?token={TOKEN}")
    with urllib.request.urlopen(req, timeout=12) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            print("SSE <<", line)
            if not line.startswith(":"):
                received.append(line)


t = threading.Thread(target=consume_sse, daemon=True)
t.start()
time.sleep(1.5)                      # 等 SSE 连上
post_sms("模拟实时推送：您收到一条新验证码 888888。")
time.sleep(6)                        # 等事件到达

# 历史接口
with urllib.request.urlopen(BASE + f"/api/messages?limit=3&token={TOKEN}") as r:
    data = json.loads(r.read().decode("utf-8"))
    print("history total =", data["total"], "| latest code =", data["items"][0]["code"])

# 无 token 应 401
try:
    urllib.request.urlopen(BASE + "/api/messages", timeout=5)
    print("FAIL: no-token request should be 401")
except urllib.error.HTTPError as e:
    print("no-token ->", e.code, "(expected 401)")

print("SSE events received:", len(received))
print("RESULT:", "PASS" if any("888888" in line for line in received) else "FAIL")