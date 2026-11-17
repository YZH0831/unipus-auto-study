# -*- coding: utf-8 -*-
"""浏览器会话：启动/接管 Edge·Chrome、登录态持久化、会话健康探测、保活。

两种模式
--------
- launch：程序自己拉起浏览器，用 state/storage_state.json 复用登录态。（默认）
- attach：接管你已登录的浏览器（先以 --remote-debugging-port=9222 启动），
          完全不碰登录流程，最省事。适合"程序启动登录老是掉"的情况。
"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import selectors as S

# 网络层错误（与"登录失效"区分开）
NET_ERROR_MARKERS = (
    "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION", "ERR_NAME_NOT_RESOLVED",
    "ERR_TIMED_OUT", "ERR_NETWORK_CHANGED", "ERR_PROXY", "ERR_ADDRESS_UNREACHABLE",
    "ERR_EMPTY_RESPONSE", "ERR_SOCKET", "ERR_FAILED", "ERR_ABORTED",
    "CONNECTION REFUSED", "TIMEOUT", "ECONNREFUSED", "ENOTFOUND",
    "SOCKET HANG UP", "CONNECTION CLOSED",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

KEEPALIVE_URL = "https://u.unipus.cn/"
KEEPALIVE_INTERVAL_S = 200


def _fake_media_args(cfg):
    """伪麦克风参数：口腔题必须有麦克风，否则页面会卡在"请允许录音"。"""
    if not cfg.browser.fake_media:
        return []
    args = [
        "--use-fake-ui-for-media-stream",      # 自动同意权限弹窗
        "--use-fake-device-for-media-stream",  # 用虚拟设备替代真实麦克风
        "--autoplay-policy=no-user-gesture-required",
    ]
    wav = str(cfg.browser.get("fake_audio_wav") or "").strip()
    if wav and Path(wav).exists():
        # 用指定音频喂给虚拟麦克风（跟读题能拿到"有声音"的输入）
        args.append(f"--use-file-for-fake-audio-capture={wav}")
    return args


class BrowserSession:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self._last_keepalive = 0.0
        self._channel_used = "-"
        self._owned = True          # False = attach 模式，不负责关闭浏览器
        self.pw = sync_playwright().start()

        if cfg.browser.mode == "attach":
            self._attach()
        else:
            self._launch()

        self.context.set_default_timeout(int(cfg.browser.page_timeout_ms))
        self.page = self._pick_page()
        self.page.on("dialog", self._on_dialog)
        self.humanizer = None
        log.info(f"浏览器就绪 | 模式={cfg.browser.mode} channel={self._channel_used} "
                 f"headless={cfg.browser.headless}")

    # ------------------------------------------------------------- 启动
    def _ctx_kwargs(self):
        kwargs = dict(
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["microphone"] if self.cfg.browser.fake_media else [],
        )
        # 可见模式下不锁 viewport，让 --start-maximized 真正生效；
        # 锁了 viewport 窗口会固定大小、无法最大化，用户容易认错窗口。
        if self.cfg.browser.headless:
            kwargs["viewport"] = {"width": int(self.cfg.browser.window_width),
                                  "height": int(self.cfg.browser.window_height)}
        else:
            kwargs["no_viewport"] = True
        state = self.state_file
        if self.cfg.login.reuse_state and state.exists():
            kwargs["storage_state"] = str(state)
            self.log.info(f"已加载登录态: {state}")
        return kwargs

    def _launch_args(self):
        return [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--no-first-run",
            "--no-default-browser-check",
        ] + _fake_media_args(self.cfg)

    def _launch(self):
        order = {
            "auto": ["msedge", "chrome", "chromium"],
            "msedge": ["msedge"],
            "chrome": ["chrome"],
            "chromium": ["chromium"],
        }.get(self.cfg.browser.channel, ["msedge", "chrome", "chromium"])
        last_err = None
        for ch in order:
            try:
                self._channel_used = ch
                self.browser = self.pw.chromium.launch(
                    channel=ch,
                    headless=bool(self.cfg.browser.headless),
                    slow_mo=int(self.cfg.browser.slow_mo),
                    args=self._launch_args(),
                )
                self.context = self.browser.new_context(**self._ctx_kwargs())
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                self.log.warning(f"以 {ch} 启动失败: {str(exc)[:120]}")
        # 兜底：playwright 自带 chromium
        try:
            self._channel_used = "chromium-bundled"
            self.browser = self.pw.chromium.launch(
                headless=bool(self.cfg.browser.headless),
                slow_mo=int(self.cfg.browser.slow_mo),
                args=self._launch_args(),
            )
            self.context = self.browser.new_context(**self._ctx_kwargs())
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "浏览器启动失败。请安装 Edge 或 Chrome，或先执行:\n"
                "  python -m playwright install chromium\n"
                f"最后错误: {exc}\n首次错误: {last_err}"
            )

    def _attach(self):
        url = self.cfg.browser.cdp_url
        try:
            self.browser = self.pw.chromium.connect_over_cdp(url)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"接管浏览器失败（{url}）。请先这样启动你的浏览器：\n\n"
                '  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
                '--remote-debugging-port=9222 --user-data-dir="C:\\edge-debug"\n\n'
                "然后在该浏览器里登录 U校园，再运行本程序。\n"
                f"原始错误: {exc}"
            )
        self._owned = False
        self._channel_used = "attached"
        if not self.browser.contexts:
            self.context = self.browser.new_context(
                user_agent=UA, locale="zh-CN", timezone_id="Asia/Shanghai"
            )
        else:
            self.context = self.browser.contexts[0]

    def _pick_page(self):
        """优先复用已打开的相关页，否则新建。"""
        try:
            for p in self.context.pages:
                if "unipus.cn" in (p.url or ""):
                    self.log.info(f"复用已打开页面: {p.url[:80]}")
                    p.bring_to_front()
                    return p
        except Exception:  # noqa: BLE001
            pass
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        return page

    # ------------------------------------------------------------- 事件
    def _on_dialog(self, dialog):
        """自动接受 alert/confirm（"确定离开此页吗"之类）。"""
        try:
            self.log.info(f"[页面对话框] {dialog.type}: {dialog.message[:60]}")
            dialog.accept()
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------- 登录态文件
    @property
    def state_file(self):
        return Path(self.cfg.state_dir) / "storage_state.json"

    def state_exists(self):
        return self.state_file.exists()

    def save_state(self):
        if not self._owned:
            return       # attach 模式不写状态文件，避免覆盖你自己的登录态
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.context.storage_state(path=str(self.state_file))
            self.log.info(f"登录态已保存: {self.state_file}")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"保存登录态失败: {str(exc)[:80]}")

    def clear_state(self):
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                self.log.info("已清除登录态文件")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"清除登录态失败: {str(exc)[:60]}")

    # ------------------------------------------------------- 会话健康探测
    def login_cookies(self):
        """找出像"登录凭证"的 cookie。"""
        try:
            cookies = self.context.cookies()
        except Exception:  # noqa: BLE001
            return []
        hits = []
        for c in cookies:
            name = (c.get("name") or "")
            dom = (c.get("domain") or "").lower()
            if "unipus" not in dom:
                continue
            low = name.lower()
            # 排除统计/埋点类
            if any(x in low for x in ("hm_", "_ga", "_gid", "sensors", "cnzz",
                                      "tongji", "um_", "hll")):
                continue
            if any(x in low for x in ("token", "session", "sess", "auth", "uid",
                                      "user", "login", "ticket", "sid")):
                hits.append(name)
        return hits

    def probe_session(self, timeout_ms=15000):
        """返回 'ok' / 'logged_out' / 'unknown' / 'offline'。

        - offline   ：网络层错误，跟登录无关
        - logged_out：被重定向到登录页 / 页面出现"登录 注册"入口且无登录 cookie
        - unknown   ：判断不了（调用方按保守策略处理）
        """
        try:
            resp = self.context.request.get(
                S.CONTENT_HOME, timeout=timeout_ms, max_redirects=5
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).upper()
            return "offline" if any(k in msg for k in NET_ERROR_MARKERS) else "unknown"

        try:
            final = (resp.url or "").lower()
            if any(h in final for h in S.LOGIN_URL_HINTS):
                return "logged_out"
            if resp.status in (401, 403):
                return "logged_out"

            body = (resp.text() or "")[:20000]
            if any(k in body for k in ("请先登录", "请登录后", "登录已过期",
                                       "登录超时", "重新登录", "未登录", "会话已失效")):
                return "logged_out"

            has_cookie = bool(self.login_cookies())
            # 已登录的典型标志
            if any(k in body for k in ("退出登录", "个人中心", "我的课程", "注销")):
                return "ok" if has_cookie or True else "unknown"
            # 有登录 cookie 就算通过（首页文案不一定可靠）
            if has_cookie:
                return "ok"
            # 没有任何登录 cookie，且页面在卖"登录/注册"
            if "登录" in body and "注册" in body:
                return "logged_out"
            return "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    # ------------------------------------------------------------- 保活
    def keep_alive(self, force=False):
        now = time.time()
        if not force and now - self._last_keepalive < KEEPALIVE_INTERVAL_S:
            return
        self._last_keepalive = now
        try:
            self.context.request.get(KEEPALIVE_URL, timeout=20000)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------- 页面导航
    def goto(self, url, context="页面"):
        """带重试的页面打开。"""
        retries = max(1, int(self.cfg.browser.nav_retry))
        timeout = int(self.cfg.login.nav_timeout_ms)
        last = None
        for attempt in range(1, retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                self.page.wait_for_timeout(1500)
                return True
            except Exception as exc:  # noqa: BLE001
                last = exc
                self.log.warning(
                    f"打开{context}失败（{attempt}/{retries}，超时 {timeout}ms）: {str(exc)[:90]}"
                )
                time.sleep(2)
        self.log.error(f"打开{context}最终失败: {last}")
        return False

    # --------------------------------------------------------- 收尾
    def close(self):
        try:
            self.save_state()
        except Exception:  # noqa: BLE001
            pass
        if not self._owned:
            try:
                self.pw.stop()
            except Exception:  # noqa: BLE001
                pass
            return
        for fn in (lambda: self.context.close(), lambda: self.browser.close(),
                   lambda: self.pw.stop()):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
