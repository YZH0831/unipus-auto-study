# -*- coding: utf-8 -*-
"""U校园 API 客户端：纯接口登录，不走浏览器、不过网页「U校园认证」墙。

为什么要有这条路
----------------
实测（2026-11-04）：网页端 SSO 登录成功后会被「U校园认证（学号+姓名比对学籍库）」
拦住，而手机 App 正常。网页认证失败属于学校侧数据问题，程序无法绕过。

但 U校园 的 App 走的是纯 API：
    POST sso.unipus.cn/sso/0.1/sso/login
就能拿到 openid + serviceTicket，**不触发任何认证弹窗**。
拿到 token 后还能直接用 WebSocket 上报学习时长（见 ws_duration.py）。

接口来源：GitHub 开源项目 unipus-tool（z5882852 / zhourx1129）的实测抓包，
本文件按同样协议重写，并加了错误处理、验证码交互、课程解析的健壮性。

诚实提示：这是逆向接口，不是官方 API。平台改版可能失效；
它不是"真实浏览器行为"，风控等级与 Web 方案不同，请自行判断风险。
"""
import base64
import json
import re
import time
from pathlib import Path

import requests
from requests import Session

# 关掉自签证书告警（平台部分子域证书链有问题）
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # noqa: BLE001
    pass

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": "https://sso.unipus.cn",
    "Pragma": "no-cache",
    "Referer": ("https://sso.unipus.cn/sso/login?service=https%3A%2F%2F"
                "u.unipus.cn%2Fuser%2Fcomm%2Flogin%3Fschool_id%3D"),
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
}

LOGIN_URL = "https://sso.unipus.cn/sso/0.1/sso/login"
CAPTCHA_URL = "https://sso.unipus.cn/sso/4.0/sso/image_captcha2"
SERVICE = "https://u.unipus.cn/user/comm/login?school_id="

# 登录返回码
CODE_OK = "0"
CODE_NEED_CAPTCHA = "1502"
CODE_BAD_CRED = "2012"


class UnipusAPI:
    """U校园 接口客户端。"""

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.session = Session()
        self.session.headers = dict(HEADERS)
        self.openid = ""
        self.ticket = ""
        self.school_id = ""
        self.real_name = ""
        self.student_num = ""
        self.token = ""
        self._captcha_dir = Path(cfg.output_dir) / "captcha"
        self._captcha_dir.mkdir(parents=True, exist_ok=True)

    # ================================================================ 登录
    def login(self, username=None, password=None):
        """账号密码登录。返回 True/False。需要验证码时会交互提示。"""
        username = username if username is not None else str(self.cfg.account.get("username") or "")
        password = password if password is not None else str(self.cfg.account.get("password") or "")
        if not username or not password:
            self.log.error("缺少账号或密码。请在 config.yaml 填 account.username / account.password")
            return False

        code, msg, rs = self._login_once(username, password)
        self.log.info(f"登录返回: code={code} msg={msg}")

        if code == CODE_OK:
            self._absorb_login(rs)
            return True

        if code == CODE_NEED_CAPTCHA or "验证码" in (msg or ""):
            return self._login_with_captcha(username, password)

        if code == CODE_BAD_CRED:
            self.log.error("账号或密码错误 —— 请确认 config.yaml 里的 account.username / password")
            return False

        self.log.error(f"登录失败: {msg}")
        return False

    def _login_once(self, username, password, captcha="", encode_captcha=None):
        data = {
            "service": SERVICE,
            "username": username,
            "password": password,
            "captcha": captcha,
            "rememberMe": "on",
            "captchaCode": captcha,
        }
        if encode_captcha:
            data["encodeCaptha"] = encode_captcha
        body = json.dumps(data, separators=(",", ":"))
        try:
            resp = self.session.post(LOGIN_URL, data=body, timeout=25, verify=False)
        except Exception as exc:  # noqa: BLE001
            return "NETWORK", f"请求失败: {exc}", None
        if resp.status_code != 200:
            return "HTTP", f"HTTP {resp.status_code}", None
        try:
            j = resp.json()
        except Exception:  # noqa: BLE001
            return "PARSE", f"响应不是 JSON: {resp.text[:120]}", None
        return str(j.get("code", "")), j.get("msg", "") or "", j.get("rs")

    def _absorb_login(self, rs):
        if not isinstance(rs, dict):
            return
        self.openid = str(rs.get("openid") or rs.get("openId") or "")
        self.ticket = str(rs.get("serviceTicket") or rs.get("ticket") or "")
        nick = rs.get("nickname") or ""
        # 登录响应里可能直接带 schoolId，先捞出来
        for k in ("schoolId", "school_id", "schoolid"):
            if rs.get(k):
                self.school_id = str(rs[k])
                break
        self.log.info(f"登录成功 | openid={self.openid[:12]}… ticket={'有' if self.ticket else '无'}"
                      + (f" | 昵称={nick}" if nick else ""))
        # 把完整 rs 落盘，字段全貌一眼可见
        try:
            p = Path(self.cfg.output_dir) / "api_login_rs.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rs, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log.info(f"登录响应已保存: {p}")
            if self.school_id:
                self.log.info(f"登录响应里带 schoolId={self.school_id}")
            else:
                self.log.info(f"登录响应字段: {sorted(rs.keys())}")
        except Exception:  # noqa: BLE001
            pass

    def get_captcha(self):
        """取图形验证码。返回 (base64图片, encodeCaptha) 或 (None, None)。"""
        try:
            resp = self.session.post(CAPTCHA_URL, timeout=20, verify=False)
            j = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"获取验证码失败: {exc}")
            return None, None
        rs = j.get("rs") or {}
        img = rs.get("image")
        enc = rs.get("encodeCaptha") or rs.get("encodeCaptcha")
        if not img:
            self.log.warning(f"验证码响应异常: {str(j)[:150]}")
            return None, None
        # 存成 jpg 并给出路径
        try:
            raw = base64.b64decode(img)
            p = self._captcha_dir / f"captcha_{int(time.time())}.jpg"
            p.write_bytes(raw)
            self.log.warning(f"验证码图片已保存: {p}")
        except Exception:  # noqa: BLE001
            pass
        return img, enc

    def _login_with_captcha(self, username, password):
        self.log.warning("平台要求图形验证码")
        for attempt in range(1, 6):
            img, enc = self.get_captcha()
            if img is None:
                return False
            code = ""
            try:
                code = input("请输入验证码（看 output/captcha 目录里的最新图片）后回车: ").strip()
            except (EOFError, KeyboardInterrupt):
                self.log.error("无法交互输入验证码，登录中止")
                return False
            if not code:
                continue
            c, msg, rs = self._login_once(username, password, code, enc)
            self.log.info(f"第 {attempt} 次验证码尝试: code={c} msg={msg}")
            if c == CODE_OK:
                self._absorb_login(rs)
                return True
            if c != CODE_NEED_CAPTCHA and CODE_BAD_CRED == c:
                self.log.error("账号或密码错误")
                return False
        self.log.error("验证码多次失败，登录中止")
        return False

    # ============================================================ 建立会话
    def bind_ticket(self, school_id=""):
        """用 serviceTicket 建立 u.unipus.cn 的会话 cookie。

        关键排查点：如果这一步被重定向到「认证/选学校」页，说明会话拿不到完整权限，
        后面 getToken 会返回 HTML。所以这里要看清 Location。
        """
        if not self.ticket:
            self.log.warning("没有 serviceTicket，无法绑定会话")
            return False
        url = "https://u.unipus.cn/user/comm/login"
        params = {"school_id": school_id or "", "ticket": self.ticket}

        # ① 先不跟随重定向，看清它想把你送去哪
        try:
            r = self.session.get(url, params=params, timeout=25,
                                 verify=False, allow_redirects=False)
            loc = r.headers.get("Location", "")
            self.log.info(f"  bind_ticket: HTTP {r.status_code}"
                          + (f" → Location: {loc[:140]}" if loc else "（无重定向）"))
            if loc:
                low = loc.lower()
                if any(k in low for k in ("auth", "certif", "school", "bind",
                                          "verify", "identify", "index", "login")):
                    self.log.warning(f"  ⚠ 会话被引向非预期页面，可能触发了学校认证: {loc[:120]}")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"bind_ticket 探测失败: {str(exc)[:80]}")

        # ② 正常跟随（和原项目一致）
        try:
            r2 = self.session.get(url, params=params, timeout=25, verify=False)
            self.log.info(f"  bind_ticket 跟随重定向后: HTTP {r2.status_code} "
                          f"最终 URL={r2.url[:120]}")
            self._dump("bind_ticket", r2.text or "")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"绑定会话失败: {exc}")
            return False

        names = sorted({c.name for c in self.session.cookies})
        self.log.info(f"  会话 cookie: {names}")
        ok = any(n in names for n in ("jwt", "ucamUserToken", "user_map", "TGC"))
        if ok:
            self.log.info("  会话绑定完成")
        else:
            self.log.warning("  没拿到预期的登录 cookie，后续接口很可能返回登录页")
        return ok

    # ============================================================ 会话缓存
    @property
    def _sess_file(self):
        return Path(self.cfg.state_dir) / "api_session.json"

    def save_session(self):
        """把 openid / ticket / cookies 存下来，下次可直接复用，免去再登录。"""
        try:
            self._sess_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "openid": self.openid,
                "ticket": self.ticket,
                "school_id": self.school_id,
                "real_name": self.real_name,
                "student_num": self.student_num,
                "cookies": [
                    {"name": c.name, "value": c.value,
                     "domain": c.domain or "", "path": c.path or "/",
                     "secure": bool(c.secure), "expires": c.expires}
                    for c in self.session.cookies
                ],
                "saved_at": int(time.time()),
            }
            self._sess_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.log.info(f"API 会话已缓存（{len(data['cookies'])} 个 cookie）: {self._sess_file}")
            return True
        except Exception as exc:  # noqa: BLE001
            self.log.debug(f"会话缓存失败: {str(exc)[:80]}")
            return False

    def load_session(self, verify=True):
        """尝试复用缓存会话。verify=True 时用 getToken 验证是否还有效。"""
        if not self._sess_file.exists():
            return False
        try:
            d = json.loads(self._sess_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        self.openid = d.get("openid") or ""
        self.ticket = d.get("ticket") or ""
        self.school_id = d.get("school_id") or ""
        self.real_name = d.get("real_name") or ""
        self.student_num = d.get("student_num") or ""
        if not self.openid:
            return False

        from requests.cookies import create_cookie
        restored = 0
        for c in d.get("cookies") or []:
            try:
                self.session.cookies.set_cookie(create_cookie(
                    name=c["name"], value=c["value"],
                    domain=c.get("domain") or None,
                    path=c.get("path") or "/",
                    secure=bool(c.get("secure")),
                    expires=c.get("expires"),
                ))
                restored += 1
            except Exception:  # noqa: BLE001
                continue
        self.log.info(f"已恢复 {restored} 个 cookie（{self.real_name or ''} {self.student_num}）")

        if not verify:
            return True
        tok = self.set_token()
        if tok:
            self.log.info("复用缓存会话成功")
            return True
        self.log.info("缓存会话已失效，需要重新登录")
        return False

    # ============================================================ 用户信息
    def fetch_user_info(self):
        """取用户信息（含 schoolId / 真实姓名 / 学号）。"""
        if not self.openid:
            return None
        url = "https://ucamapi.unipus.cn/rpc/api/user-info"
        params = {"openId": self.openid, "source": "0",
                  "callback": "jsonp_0000000000"}
        try:
            resp = self.session.get(url, params=params, timeout=25, verify=False)
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"取用户信息失败: {exc}")
            return None
        text = resp.text or ""
        self._dump("user_info", text)

        # 去 jsonp 包装后解析
        data = None
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            self.log.warning(f"用户信息不是合法 JSON: {text[:160]}")
            return None

        # 兼容多种结构：result.user / user / data.user
        user = None
        for path in (("result", "user"), ("user",), ("data", "user"),
                     ("result", "userInfo"), ("data",)):
            node = data
            ok = True
            for k in path:
                if isinstance(node, dict) and k in node:
                    node = node[k]
                else:
                    ok = False
                    break
            if ok and isinstance(node, dict) and node:
                user = node
                break
        if user is None:
            self.log.warning(f"用户信息结构不认识，顶层键={list(data.keys())[:10]}")
            return None

        self.school_id = str(user.get("schoolId") or user.get("school_id") or "")
        self.real_name = user.get("name") or user.get("realName") or ""
        self.student_num = str(user.get("num") or user.get("studentNo") or "")
        self.log.info(f"用户: {self.real_name} | 学号 {self.student_num} | "
                      f"学校 {user.get('schoolName','')} | schoolId={self.school_id}")
        return user

    def _dump(self, name, text):
        """把原始响应落盘，便于排查接口变化。"""
        try:
            p = Path(self.cfg.output_dir) / f"api_{name}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(text)[:20000], encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            pass

    def set_token(self):
        """取 X-Annotator-Auth-Token（WebSocket 刷时长要用）。

        优先走 /user/data/getToken；返回非 JSON 时退化为正则抓 token。
        若返回 HTML（被重定向到登录/认证页），换一套 u.unipus.cn 的 Referer 再试。
        """
        url = "https://u.unipus.cn/user/data/getToken"
        params = {"_": int(time.time() * 1000)}

        attempts = [
            {},   # 原样
            {"Referer": "https://u.unipus.cn/", "Origin": "https://u.unipus.cn"},
            {"Referer": "https://u.unipus.cn/user/student",
             "Origin": "https://u.unipus.cn",
             "X-Requested-With": "XMLHttpRequest"},
        ]

        token = ""
        for i, extra in enumerate(attempts, 1):
            try:
                resp = self.session.get(url, params=params, timeout=25,
                                        verify=False, headers=extra)
                text = resp.text or ""
                self._dump(f"get_token{'' if i == 1 else i}", text)
                if text.lstrip().startswith("<"):
                    if i < len(attempts):
                        self.log.debug(f"  getToken 第 {i} 次返回 HTML，换 headers 重试")
                        continue
                    self.log.warning("  getToken 始终返回 HTML —— 会话没有访问权限")
                    break
                try:
                    j = resp.json()
                    token = j.get("token") or (j.get("data") or {}).get("token") or ""
                except Exception:  # noqa: BLE001
                    m = re.search(r'"token"\s*:\s*"([^"]+)"', text)
                    if m:
                        token = m.group(1)
                    elif text and len(text) < 500 and re.fullmatch(r"[A-Za-z0-9._\-]+", text.strip()):
                        token = text.strip()
                if token:
                    break
            except Exception as exc:  # noqa: BLE001
                self.log.warning(f"  取 token 异常（第 {i} 次）: {str(exc)[:80]}")

        self.token = token or ""
        if self.token:
            self.session.headers.update({"X-Annotator-Auth-Token": self.token})
            self.log.info(f"已获取 Annotator Token（{len(self.token)} 字符）")
            return self.token
        self.log.warning("token 为空。若反复失败，看 output/api_get_token*.txt 里的原始响应")
        return None

    # ============================================================== 课程
    def list_courses(self):
        """课程列表。返回 [{class_name, course_name, tutorial_id}]。

        优先解析 HTML 页面里的 .class-content 结构；
        解析不到就退化为正则全量扫 tutorialid + 课程名。
        """
        url = "https://u.unipus.cn/user/student"
        params = {"school_id": self.school_id}
        try:
            resp = self.session.get(url, params=params, timeout=30,
                                    allow_redirects=False, verify=False)
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"取课程列表失败: {exc}")
            return []
        if resp.status_code != 200:
            self.log.warning(f"课程页 HTTP {resp.status_code}")
            return []
        html = resp.text or ""
        courses = self._parse_courses(html)
        if courses:
            self.log.info(f"解析到 {len(courses)} 门课程")
        else:
            self.log.warning("没解析出课程（可能未选课，或页面结构变了）")
            try:
                p = Path(self.cfg.output_dir) / "courses_page.html"
                p.write_text(html, encoding="utf-8", errors="ignore")
                self.log.info(f"课程页 HTML 已保存，便于排查: {p}")
            except Exception:  # noqa: BLE001
                pass
        return courses

    @staticmethod
    def _parse_courses(html):
        out = []
        # ① 标准结构
        for block in re.split(r'<div[^>]+class="[^"]*class-content[^"]*"', html)[1:]:
            seg = block[:6000]
            tid = re.search(r'tutorialid="(\d+)"', seg)
            name = (re.search(r'class="[^"]*my_course_name[^"]*"[^>]*>\s*([^<]+)', seg)
                    or re.search(r'class="[^"]*class-name[^"]*"[^>]*>\s*([^<]+)', seg))
            if tid and name:
                out.append({
                    "tutorial_id": tid.group(1),
                    "course_name": name.group(1).strip(),
                    "class_name": name.group(1).strip(),
                })
        if out:
            return out
        # ② 正则兜底：所有 tutorialid
        for m in re.finditer(r'tutorialid="(\d+)"', html):
            out.append({"tutorial_id": m.group(1), "course_name": "(未知课程)",
                        "class_name": "(未知课程)"})
        return out

    def course_units(self, tutorial_id):
        """课程单元列表。"""
        url = (f"https://ucontent.unipus.cn/course/api/v2/course_progress/"
               f"{tutorial_id}/{self.openid}/default/")
        try:
            resp = self.session.get(url, timeout=25, verify=False)
            j = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"取单元失败: {exc}")
            return []
        units = ((j.get("rt") or {}).get("units") or {})
        out = []
        for name, d in units.items():
            state = d.get("state") or {}
            out.append({
                "unit": name,
                "required": bool(state.get("required")),
                "done": bool(state.get("done") or state.get("finished")),
            })
        if out:
            self.log.info(f"共 {len(out)} 个单元: "
                          + ", ".join(f"{u['unit']}{'(必修)' if u['required'] else '(选修)'}"
                                      for u in out[:12]))
        return out

    def unit_leafs(self, tutorial_id, unit):
        """某单元下的章节（leafs）。"""
        url = (f"https://ucontent.unipus.cn/course/api/v2/course_progress/"
               f"{tutorial_id}/{unit}/{self.openid}/default/")
        try:
            resp = self.session.get(url, timeout=25, verify=False)
            j = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.log.debug(f"取 {unit} 章节失败: {exc}")
            return []
        leafs = ((j.get("rt") or {}).get("leafs") or {})
        return list(leafs.keys())

    def build_module_paths(self, tutorial_id, max_units=20, max_leafs=40):
        """构建 [(unit, section, leaf)] 三元组，供 WebSocket 上报使用。

        U校园 课件路径形如 /courseware/u1/u1g1/u1g2/__main，
        即 unit → section → leaf 三层。leafs 的 key 通常是 section 级，
        leaf 级需要用默认后缀；拿不准时退回官方默认的 u1/u1g1/u1g2。
        """
        paths = []
        units = self.course_units(tutorial_id)
        for u in units[:max_units]:
            unit = u["unit"]
            leafs = self.unit_leafs(tutorial_id, unit)[:max_leafs]
            for lf in leafs:
                # section 形如 u1g1；leaf 通常为 u1g2 / u1g1
                section = lf
                leaf = lf
                paths.append((unit, section, leaf))
        if not paths:
            paths = [("u1", "u1g1", "u1g2")]
            self.log.warning("没取到章节树，退回默认路径 u1/u1g1/u1g2")
        else:
            self.log.info(f"构建了 {len(paths)} 个上报路径（示例 {paths[0]}）")
        return paths
