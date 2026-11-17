# -*- coding: utf-8 -*-
"""WebSocket 学习时长上报。

U校园 记录学习时长的真实通道
--------------------------
浏览器/App 里，课程页会跟 `ucontent.unipus.cn` 建一条 socket.io 连接，
周期性上报「我正在学哪个模块」。平台按这些心跳累计学习时长。

协议（socket.io v2 / Engine.IO 3）：
    连接  wss://ucontent.unipus.cn/unipusio/?uuid=<openid>&token=<token>&EIO=3&transport=websocket
    鉴权  发送  40/userActivities?uuid=..&token=..,
    上报  发送  42/userActivities,0["start",{module,moduleGroup,client,url,tag1,tag2}]
    心跳  服务端发 "2"，客户端回 "3"

这条路不需要浏览器、不需要过网页认证，代价是它属于接口层调用，
不是"真实用户行为"。平台风控策略变化时可能失效。
"""
import json
import ssl
import time

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

WS_URL = ("wss://ucontent.unipus.cn/unipusio/"
          "?uuid={uuid}&token={token}&EIO=3&transport=websocket")

# 课件页 URL 模板（平台按这个路径识别"在学哪一节"）
COURSEWARE_URL = ("https://ucontent.unipus.cn/_pc_default/pc.html?cid=&appid=#/"
                  "{tid}/courseware/{unit}/{section}/{leaf}/__main")


def _payload(tid, unit, section, leaf):
    """构造一次上报的 ["start", {...}] 体。"""
    data = ["start", {
        "module": leaf,
        "moduleGroup": str(tid),
        "client": "U校园pc",
        "url": COURSEWARE_URL.format(tid=tid, unit=unit, section=section, leaf=leaf),
        "tag1": unit,
        "tag2": section,
    }]
    return "42/userActivities,0" + json.dumps(data, ensure_ascii=False)


def run(openid, token, tutorial_id, paths, log, interval_s=20,
        total_minutes=60, ping_timeout_s=90, stop_check=None):
    """跑刷时长循环。

    paths        : [(unit, section, leaf), ...]，会顺序轮转
    interval_s   : 上报周期（秒）。原项目用 20，5 也能跑但更像机器
    total_minutes: 跑多久（0 = 一直跑到中断）
    stop_check   : 可选回调，返回 True 时优雅退出

    返回实际运行秒数。
    """
    if websocket is None:
        raise SystemExit("缺少 websocket-client。请执行：pip install websocket-client")
    if not openid or not token:
        raise SystemExit("缺少 openid 或 token，无法连接时长通道")
    if not paths:
        paths = [("u1", "u1g1", "u1g2")]

    url = WS_URL.format(uuid=openid, token=token)
    log.info(f"连接时长通道: wss://ucontent.unipus.cn/unipusio/?uuid={openid[:10]}…")
    ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
    ws.connect(url, timeout=30)
    log.info("WebSocket 已连接")

    try:
        # 命名空间鉴权
        auth = f"40/userActivities?uuid={openid}&token={token},"
        ws.send(auth)
        try:
            ws.settimeout(10)
            resp = ws.recv()
            log.debug(f"鉴权响应: {str(resp)[:120]}")
        except Exception:  # noqa: BLE001
            pass

        started = time.time()
        deadline = started + total_minutes * 60 if total_minutes and total_minutes > 0 else 0
        idx = 0
        sent = 0
        last_log = 0.0
        ws.settimeout(ping_timeout_s)

        while True:
            if stop_check is not None:
                try:
                    if stop_check():
                        log.info("收到停止信号，结束时长上报")
                        break
                except Exception:  # noqa: BLE001
                    pass
            if deadline and time.time() >= deadline:
                log.info(f"已达设定时长 {total_minutes} 分钟，结束上报")
                break

            unit, section, leaf = paths[idx % len(paths)]
            idx += 1
            msg = _payload(tutorial_id, unit, section, leaf)
            try:
                ws.send(msg)
                sent += 1
                # 读一次响应（同时处理服务端 ping）
                try:
                    resp = ws.recv()
                    if resp == "2":
                        ws.send("3")
                    elif resp and str(resp).startswith("44"):
                        log.warning(f"服务端报错: {str(resp)[:160]}")
                except websocket.WebSocketTimeoutException:
                    pass
            except Exception as exc:  # noqa: BLE001
                log.warning(f"上报失败（{str(exc)[:80]}），尝试重连…")
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(3)
                ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
                ws.connect(url, timeout=30)
                ws.send(auth)
                ws.settimeout(ping_timeout_s)
                log.info("已重连")
                continue

            elapsed = time.time() - started
            if elapsed - last_log >= 120:
                last_log = elapsed
                log.info(f"  已上报 {sent} 次 | 累计 {elapsed/60:.1f} 分钟 | "
                         f"当前模块 {unit}/{section}/{leaf}")

            # 周期本身带抖动，避免每 20 秒精确到毫秒的机器特征
            import random
            time.sleep(max(3.0, interval_s * (0.85 + random.random() * 0.3)))

        return time.time() - started
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass
