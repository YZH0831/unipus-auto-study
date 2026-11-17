# -*- coding: utf-8 -*-
"""登录：登录态复用 → 密码登录 → 手动登录兜底。

U校园统一登录有几个特点，直接决定了实现方式：
1. 登录入口在 u.unipus.cn，但课程在 ucontent.unipus.cn，跨子域共享 cookie；
2. 常有图形验证码 / 滑块，纯自动填表不一定能过；
3. 支持微信扫码，扫码是最稳的路径。

因此默认 `login_method: manual`——程序打开登录页，你手动登一次，
之后靠 state/storage_state.json 长期复用，只在失效时才需要再登。
"""
import time

from . import selectors as S

LOGIN_PAGE_CANDIDATES = [
    "http://u.unipus.cn/index.html/",      # 官网首页，再点右上角「登录」
    "https://u.unipus.cn/",
]

# 官网首页右上角的登录入口
LOGIN_ENTRY_TEXTS = ("登录", "登 录")

# 已登录后的门户特征
LOGGED_IN_URL_HINTS = ("newindex", "school_id", "ucontent.unipus.cn")


def _click_login_entry(page, log):
    """点官网首页右上角的「登录」，让网站自己去拼正确的 SSO 地址。

    为什么不用硬编码的 SSO URL：实测 `service=` 参数必须指向 /user/common 系列，
    填成 `/` 的话登录成功后会退回宣传页（ticket 消耗掉但会话没建立），
    表现就是"密码明明对，就是进不去"。让网站自己生成最稳。
    """
    try:
        hit = page.evaluate(r"""
        () => {
          const cands = [];
          const all = document.querySelectorAll('a, button, span, div, li');
          for (const el of all) {
            if (el.children.length > 0) continue;          // 只要叶子
            const t = (el.innerText || '').trim();
            if (!t || t.length > 4) continue;
            if (!['登录', '登 录', '立即登录'].includes(t)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 10 || r.height < 8) continue;
            if (r.top > 260) continue;                     // 只要顶部导航区的
            cands.push({el, top: r.top, left: r.left});
          }
          if (!cands.length) return '';
          // 取最靠上的那个（导航栏）
          cands.sort((a, b) => a.top - b.top || b.left - a.left);
          cands[0].el.setAttribute('data-upc-login-entry', '1');
          return cands[0].el.innerText.trim();
        }
        """)
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    try:
        el = page.query_selector('[data-upc-login-entry="1"]')
        if el is None or not el.is_visible():
            return False
        el.click(timeout=6000)
        log.info(f"  已点击官网「{hit}」入口")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  点击登录入口失败: {str(exc)[:70]}")
        return False


def _open_login_page(session, log):
    """打开统一登录页。已经在登录状态或登录页上就直接返回。"""
    page = session.page
    try:
        url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        url = ""

    if any(h in url for h in LOGGED_IN_URL_HINTS):
        return True
    if "sso.unipus.cn" in url and "login" in url:
        return True

    for target in LOGIN_PAGE_CANDIDATES:
        if not session.goto(target, context="官网首页"):
            continue
        page.wait_for_timeout(2500)
        if _click_login_entry(page, log):
            # 等跳转到 sso
            for _ in range(40):
                try:
                    cur = page.url or ""
                except Exception:  # noqa: BLE001
                    cur = ""
                if "sso.unipus.cn" in cur:
                    log.info(f"已跳转到统一登录页: {cur[:90]}")
                    return True
                page.wait_for_timeout(500)
            log.warning("点了登录但没跳转到 SSO，可能被弹窗拦住")
        # 如果已经在 sso
        try:
            if "sso.unipus.cn" in (page.url or ""):
                return True
        except Exception:  # noqa: BLE001
            pass
    return False

# 登录表单（多套候选，U校园历史版本换过几次）
USER_BOX = [
    'input[name="username"]',
    'input[name="userName"]',
    'input[name="loginName"]',
    'input[id*="username" i]',
    'input[placeholder*="手机号"]',
    'input[placeholder*="邮箱"]',
    'input[placeholder*="账号"]',
    'input[placeholder*="用户名"]',
    'input[type="text"]',
]
PWD_BOX = [
    'input[name="password"]',
    'input[id*="password" i]',
    'input[placeholder*="密码"]',
    'input[type="password"]',
]
SUBMIT_BTN = [
    'button[type="submit"]',
    'button:has-text("登录")',
    'button:has-text("登 录")',
    ".login-btn",
    '[class*="login-btn"]',
    '[class*="loginBtn"]',
]

CAPTCHA_HINTS = ("验证码", "滑块", "拖动", "captcha", "请完成验证", "安全验证")

# 登录页底部的协议勾选框：不勾的话点「登录」没反应或报错，
# 这是"密码明明正确却进不去"最常见的原因之一。
AGREE_CHECKBOX = [
    'input[type="checkbox"]',
    '.ant-checkbox-input',
    '[class*="agree"] input',
    '[class*="protocol"] input',
]
AGREE_HINTS = ("我已阅读并同意", "阅读并同意", "用户协议", "隐私政策")

JS_AGREE_STATE = r"""
() => {
  // 找到"我已阅读并同意"附近的 checkbox，返回 {found, checked}
  const els = Array.from(document.querySelectorAll('input[type="checkbox"]'));
  for (const el of els) {
    let p = el.parentElement, txt = '';
    for (let i = 0; i < 4 && p; i++) { txt += (p.innerText || ''); p = p.parentElement; }
    if (/同意|协议|隐私/.test(txt)) {
      return {found: true, checked: !!el.checked};
    }
  }
  return {found: els.length > 0, checked: els.length ? !!els[0].checked : false};
}
"""


def check_agreement(page, log, auto_check=False):
    """检查协议勾选框。返回 True 表示已勾选/无需勾选。

    auto_check=True 时自动勾上（仅用于无人值守的密码登录）。
    手动登录场景只做提示——代用户同意协议这种事应该让本人来做。
    """
    try:
        st = page.evaluate(JS_AGREE_STATE) or {}
    except Exception:  # noqa: BLE001
        return True
    if not st.get("found"):
        return True
    if st.get("checked"):
        return True

    if not auto_check:
        log.warning("!" * 58)
        log.warning("登录页的「我已阅读并同意…」协议框【还没有勾选】")
        log.warning("不勾这个框，点「登录」会没有反应 —— 这是最常见的原因")
        log.warning("请在浏览器里手动勾上，然后再点登录")
        log.warning("!" * 58)
        return False

    # 自动勾选
    for sel in AGREE_CHECKBOX:
        try:
            els = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            try:
                if el.is_checked():
                    return True
                el.check(timeout=3000, force=True)
                page.wait_for_timeout(300)
                log.info("已自动勾选协议框")
                return True
            except Exception:  # noqa: BLE001
                # 有些是伪装成 checkbox 的样式元素，直接点它
                try:
                    box = el.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2,
                                         box["y"] + box["height"] / 2)
                        page.wait_for_timeout(300)
                        return True
                except Exception:  # noqa: BLE001
                    continue
    log.warning("没能自动勾选协议框，请手动勾上")
    return False


def _has_login_cookie(session):
    """是否持有登录凭证 cookie（判断逻辑集中在 BrowserSession.login_cookies）。"""
    try:
        return bool(session.login_cookies())
    except Exception:  # noqa: BLE001
        return False


def check_session(session):
    """登录态是否仍然有效。"""
    status = session.probe_session()
    if status == "ok":
        return True
    if status == "logged_out":
        return False
    # unknown：保守处理——有登录凭证 cookie 就认为可用，否则要求重新登录
    return _has_login_cookie(session)


def _page_logged_in(page):
    """从页面内容判断是否已登录（不依赖 cookie 名字）。"""
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        return False
    if any(k in text for k in ("退出登录", "个人中心", "我的课程", "注销", "我的学习")):
        return True
    return False


def _wait_logged_in(session, timeout_s, log, tip=""):
    """轮询等待登录完成。

    三个独立信号，任一成立即算成功：
      ① 出现登录凭证 cookie 且已离开登录页
      ② 页面出现"退出登录 / 个人中心"等已登录标志
      ③ 探测接口返回 ok
    """
    deadline = time.time() + timeout_s
    page = session.page
    last_tip = 0
    while time.time() < deadline:
        try:
            url = (page.url or "").lower()
            on_login = any(h in url for h in S.LOGIN_URL_HINTS)
            if _has_login_cookie(session) and not on_login:
                log.info("检测到登录凭证 cookie，登录成功")
                return True
            if _page_logged_in(page):
                log.info("页面已显示已登录状态，登录成功")
                return True
        except Exception:  # noqa: BLE001
            pass
        left = int(deadline - time.time())
        if tip and time.time() - last_tip > 20:
            last_tip = time.time()
            log.info(f"  {tip}（剩余 {left} 秒）")
        time.sleep(1.5)
    return False


def _password_login(session, log):
    """账号密码自动填表。遇验证码会暂停等人工完成。"""
    cfg = session.cfg
    page = session.page
    for attempt in range(1, max(1, int(cfg.login.max_retry)) + 1):
        log.info(f"账号密码登录：第 {attempt} 次尝试")
        user_el = S.first_in(page, USER_BOX, timeout_s=10)
        pwd_el = S.first_in(page, PWD_BOX, timeout_s=5)
        if user_el is None or pwd_el is None:
            log.warning("未找到登录输入框（页面结构可能变化，或需要先点『登录』打开表单）")
            # 试着点一下"登录"入口把表单叫出来
            entry = S.first_in(page, ['a:has-text("登录")', 'button:has-text("登录")',
                                      '[class*="login"]'], timeout_s=3)
            if entry is not None:
                try:
                    entry.click(timeout=3000)
                    page.wait_for_timeout(1500)
                except Exception:  # noqa: BLE001
                    pass
                user_el = S.first_in(page, USER_BOX, timeout_s=8)
                pwd_el = S.first_in(page, PWD_BOX, timeout_s=5)
        if user_el is None or pwd_el is None:
            return False
        try:
            user_el.fill(cfg.account.username)
            page.wait_for_timeout(300)
            pwd_el.fill(cfg.account.password)
            page.wait_for_timeout(400)
        except Exception as exc:  # noqa: BLE001
            log.error(f"填写登录表单失败: {str(exc)[:80]}")
            return False

        # 图形验证码：交给人工
        if _captcha_present(page):
            log.warning("检测到图形/滑块验证码，请在浏览器里手动完成后程序自动继续")
            if _wait_logged_in(session, 180, log, "等待手动完成验证码"):
                return True

        # 协议勾选框：不勾会导致"密码对但登录没反应"
        check_agreement(page, log, auto_check=True)

        btn = S.first_in(page, SUBMIT_BTN, timeout_s=5)
        if btn is None:
            log.warning("未找到登录按钮")
            return False
        try:
            btn.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"点击登录失败: {str(exc)[:80]}")

        if _wait_logged_in(session, 60, log, "等待登录结果"):
            return True

        # 可能是验证码弹出来了
        if _captcha_present(page):
            log.warning("登录被验证码拦截，请在浏览器中手动完成（等待 180 秒）")
            if _wait_logged_in(session, 180, log):
                return True
        log.warning("本次密码登录未成功，稍后重试")
        time.sleep(3)
    return False


def _captcha_present(page):
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        return False
    if any(h in text for h in CAPTCHA_HINTS):
        return True
    for sel in ('[class*="captcha"]', '[class*="slider"]', "#nc_1_wrapper",
                '[class*="verify"]', "canvas[class*='verify']"):
        try:
            el = page.query_selector(sel)
            if el is not None and el.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _manual_login(session, log):
    """打开登录页，等用户手动登录（扫码/账号密码都行）。"""
    cfg = session.cfg
    if not _open_login_page(session, log):
        return False
    # 提醒用户勾选协议框（不代勾，协议同意应由本人操作）
    check_agreement(session.page, log, auto_check=False)
    wait_s = int(cfg.login.manual_wait_s)
    log.warning("=" * 58)
    log.warning("请在刚弹出的浏览器窗口里完成登录（扫码 或 账号密码都可以）")
    log.warning(f"登录成功后程序会自动继续，最多等待 {wait_s} 秒")
    log.warning("=" * 58)
    ok = _wait_logged_in(session, wait_s, log, "等待手动登录")
    if ok:
        session.save_state()
    return ok


def ensure_login(session, cfg, log):
    """确保处于已登录状态。返回 True/False。"""
    if cfg.login.reuse_state and check_session(session):
        log.info("复用已保存的登录态（无需重新登录）")
        return True

    if session.state_exists():
        log.warning("已保存的登录态失效，需要重新登录")

    method = cfg.account.login_method
    if method == "password" and cfg.account.username and cfg.account.password:
        if _open_login_page(session, log) and _password_login(session, log):
            session.save_state()
            return True
        log.warning("账号密码登录失败，转为手动登录")
    return _manual_login(session, log)


def relogin(session, cfg, log):
    """掉线后的自动重登录。"""
    log.warning("=" * 58)
    log.warning("检测到登录状态失效 → 开始重新登录")
    log.warning("=" * 58)
    try:
        session.context.clear_cookies()
    except Exception:  # noqa: BLE001
        pass

    if cfg.account.login_method == "password" and cfg.account.username:
        for attempt in range(1, max(1, int(cfg.login.relogin_max_attempts)) + 1):
            log.info(f"自动重登录 第 {attempt} 次")
            if _open_login_page(session, log) and _password_login(session, log):
                session.save_state()
                return True
            time.sleep(min(15 * attempt, 60))
    return _manual_login(session, log)
