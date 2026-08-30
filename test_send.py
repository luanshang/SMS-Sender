# -*- coding: utf-8 -*-
"""模拟 SmsForwarder 端 Webhook 签名算法，向本服务发送一条测试短信。

用法:
    python test_send.py                  # 用默认密钥 test-secret 发送
    python test_send.py --secret xxx     # 指定密钥
"""
import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.request
from urllib.parse import quote

DEFAULT_URL = "http://127.0.0.1:8322/v1/sms/receive"


def sign_android(timestamp_ms: str, secret: str) -> str:
    """严格复刻 WebhookUtils.kt 的签名：URLEncoder(Base64(HmacSHA256(ts + '\\n' + secret, secret)))"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return quote(base64.b64encode(digest).decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", default="test-secret")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--from", dest="from_number", default="10086")
    ap.add_argument("--content", default="【XX银行】您尾号 8899 的账户验证码是 123456，10 分钟内有效。")
    ap.add_argument("--tampered", action="store_true", help="故意用错误密钥签名，应返回 401")
    args = ap.parse_args()

    ts = str(int(time.time() * 1000))
    effective_secret = "wrong-secret" if args.tampered else args.secret
    sign = sign_android(ts, effective_secret)

    payload = {
        "from": args.from_number,
        "contact_name": "XX银行",
        "phone_area": "上海 上海",
        "content": args.content,
        "sim_slot": "SIM1",
        "device_name": "安卓备用机",
        "receive_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": ts,
        "sign": sign,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        args.url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[OK] {resp.status} {resp.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {e.read().decode()}")


if __name__ == "__main__":
    main()