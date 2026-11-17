# -*- coding: utf-8 -*-
"""总编排：登录 → 进课程 → 扫目录 → 逐个 Task 执行 → 出报告。

一个 Task 的处理顺序（顺序有讲究）
--------------------------------
    1. 播放视频/音频  —— 它本身就是真实时间，且平台把它和学习时长分开算
    2. 做单元测试     —— 需要点来点去，顺手就让页面保持活跃
    3. 口语录音       —— 同上
    4. 补足停留时长   —— 前几步花的时间会从预算里扣掉，不够才补

这样一趟下来，每个 Task 既"学了时长"又"做了内容"，比单纯挂机更像人。
"""
import random
import re
import time

from . import login as L
from . import menu as M
from . import portal as P
from . import quiz as Q
from . import selectors as S
from . import speaking as SP
from . import tabs as T
from . import video as V
from .dwell import TimeBudget, dwell, has_media, read_remaining_minutes
from .humanize import Humanizer
from .popups import PopupGuard
from .report import Report

RISK_HINTS = ("操作过于频繁", "访问受限", "请稍后再试", "异常访问",
              "安全验证", "人机验证", "滑动验证", "账号异常")


class Runner:
    def __init__(self, cfg, log, args):
        self.cfg = cfg
        self.log = log
        self.args = args
        self.session = None
        self.humanizer = None
        self.popup = None
        self.report = Report(cfg, log)
        self.budget = TimeBudget(cfg, log)
        self.quiz_solver = None
        self._last_check = 0.0
        self._stop = False
        self._start_ts = time.time()
        self._task_counter = 0

    # ==================================================================
    def run(self):
        try:
            self._bootstrap()
            course_ok = self._enter_course()
            if not course_ok:
                self.log.error("没能进入课程页，退出")
                return 1
            leaves = self._scan()
            if not leaves:
                self.log.error("没有找到可学习的目录项")
                return 1
            leaves = self._select(leaves)
            if not leaves:
                self.log.error("筛选后没有要处理的项")
                return 1

            self.report.leaves = leaves
            self._execute(leaves)
            self._summary()
            return 0
        except KeyboardInterrupt:
            self.log.warning("\n用户中断（Ctrl+C），正在保存报告…")
            try:
                self.report.save()
            except Exception:  # noqa: BLE001
                pass
            return 130
        finally:
            try:
                if self.session:
                    self.session.close()
            except Exception:  # noqa: BLE001
                pass

    # ==================================================================
    def _bootstrap(self):
        from .browser import BrowserSession

        self.log.info("=" * 62)
        self.log.info("U校园自动化助手 启动")
        self.log.info(f"时长模式: {self.cfg.duration.mode} | "
                      f"答题策略: {self.cfg.quiz.strategy} | "
                      f"口语: {'开' if self.cfg.speaking.enabled else '关'}")
        self.log.info("=" * 62)

        self.session = BrowserSession(self.cfg, self.log)
        self.humanizer = Humanizer(self.cfg, self.log)
        self.session.humanizer = self.humanizer
        self.popup = PopupGuard(self.cfg, self.log, self.humanizer)

        if not L.ensure_login(self.session, self.cfg, self.log):
            raise SystemExit("登录未完成，退出。请重新运行并完成登录。")

        self.quiz_solver = Q.QuizSolver(
            self.session, self.cfg, self.log, self.humanizer, self.popup
        )

    # ==================================================================
    def _enter_course(self):
        """进入课程目录页。

        三条路，按顺序试：
          ① config 里填了 course.url → 直接跳（最快）
          ② course.auto_enter → 从门户自动导航：学习 → 课程 → 教材 → 教程学习
          ③ 按课程名在门户里找（最后兜底）
        """
        cfg = self.cfg.course
        auto = bool(cfg.get("auto_enter", True))

        # ① 直接跳 URL
        if cfg.url:
            self.log.info(f"打开课程页: {cfg.url}")
            if self.session.goto(cfg.url, context="课程页"):
                self.session.page.wait_for_timeout(3000)
                if self._kicked_to_login():
                    self.log.warning("课程页被重定向到登录页 → 登录态无效，重新登录")
                    if not L.relogin(self.session, self.cfg, self.log):
                        return False
                    if not self.session.goto(cfg.url, context="课程页"):
                        return False
                    self.session.page.wait_for_timeout(3000)
                if P.on_courseware(self.session):
                    return self._after_enter()
                if not auto:
                    self.log.error("填写的课程地址里没检测到课程目录。"
                                   "请确认那是「教程学习」进去后的页面，"
                                   "或者把 course.auto_enter 设为 true 让程序自动导航。")
                    return False
                self.log.info("该地址没有课程目录，改用门户自动导航…")

        # ② 门户自动导航
        if auto:
            if not self.session.goto(S.HOME_URL, context="门户首页"):
                return False
            if self._kicked_to_login():
                self.log.warning("门户被重定向到登录页 → 登录态无效，重新登录")
                if not L.relogin(self.session, self.cfg, self.log):
                    return False
                if not self.session.goto(S.HOME_URL, context="门户首页"):
                    return False
            self.session.page.wait_for_timeout(2500)
            if P.enter_courseware(self.session, self.cfg, self.log, self.humanizer, self.popup):
                return self._after_enter()

        # ③ 按课程名兜底
        if self._find_course_by_name() and P.on_courseware(self.session):
            return self._after_enter()

        self.log.error("没能进入课程目录。建议：手动点进「教程学习」页面，"
                       "把地址栏内容填到 config.yaml 的 course.url，再运行。")
        return False

    def _after_enter(self):
        """进到课程页后的收尾：清弹窗、记录课程名。"""
        page = self.session.page
        self.popup.sweep(page, rounds=2)
        self.report.course = self._page_title(page)
        self.log.info(f"当前课程：{self.report.course}")
        self.log.info(f"当前地址：{page.url[:110]}")
        return True

    def _kicked_to_login(self):
        try:
            url = (self.session.page.url or "").lower()
        except Exception:  # noqa: BLE001
            return False
        if any(h in url for h in S.LOGIN_URL_HINTS):
            return True
        try:
            text = self.session.page.evaluate(
                "() => (document.body ? document.body.innerText : '')") or ""
        except Exception:  # noqa: BLE001
            return False
        return any(k in text for k in ("请先登录", "请登录后查看", "登录已过期"))

    def _page_title(self, page):
        try:
            t = page.title() or ""
            if t.strip():
                return t.strip()[:80]
        except Exception:  # noqa: BLE001
            pass
        try:
            el = page.query_selector("h1, .course-name, [class*='course-title']")
            if el is not None:
                return (el.inner_text() or "").strip()[:80]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _find_course_by_name(self):
        """在 U校园 课程列表里按关键词找课程并点进去。"""
        key = self.cfg.course.name_contains.strip()
        log = self.log
        log.info(f"未配置 course.url，尝试按课程名『{key}』查找")
        if not self.session.goto(S.CONTENT_HOME, context="课程列表"):
            return False
        page = self.session.page
        page.wait_for_timeout(int(float(self.cfg.course.entry_wait_s) * 1000))

        # 常见入口：我的课程 / 课程列表
        for txt in ("我的课程", "课程", "学习"):
            try:
                el = page.query_selector(f'a:has-text("{txt}"), button:has-text("{txt}")')
                if el is not None and el.is_visible():
                    el.click(timeout=4000)
                    page.wait_for_timeout(2000)
                    break
            except Exception:  # noqa: BLE001
                continue

        deadline = time.time() + 30
        while time.time() < deadline:
            cand = None
            for sel in ("a", "[class*='course']", "li", "div"):
                try:
                    els = page.query_selector_all(sel)
                except Exception:  # noqa: BLE001
                    continue
                for el in els:
                    try:
                        if not el.is_visible():
                            continue
                        txt = (el.inner_text() or "").strip()
                        if key and key in txt and len(txt) < 200:
                            cand = el
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if cand is not None:
                    break
            if cand is not None:
                log.info(f"找到课程入口，点击进入")
                try:
                    if self.humanizer:
                        self.humanizer.click(page, cand, timeout=8000)
                    else:
                        cand.click(timeout=8000)
                    page.wait_for_timeout(3500)
                    return True
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"点击课程失败: {str(exc)[:80]}")
            page.wait_for_timeout(1500)

        log.error(f"没找到名字包含『{key}』的课程。"
                  f"建议直接复制课程学习页地址填到 config 的 course.url")
        return False

    # ==================================================================
    def _scan(self):
        page = self.session.page
        self.log.info("-" * 62)
        self.log.info("开始扫描课程目录…")
        leaves = M.discover_leaves(page, self.log, self.humanizer)
        if not leaves:
            # 再等一轮（有的课程目录渲染很慢）
            self.log.info("  目录为空，等待 8 秒后重试…")
            page.wait_for_timeout(8000)
            M.expand_all(page, self.log, humanizer=self.humanizer)
            leaves = M.discover_leaves(page, self.log, self.humanizer)
        return leaves

    def _select(self, leaves):
        cfg = self.cfg.course
        if cfg.pick_interactively:
            leaves = self._pick_interactively(leaves)
        leaves = M.filter_leaves(leaves, cfg.units, self.log)
        if self.cfg.safety.max_items:
            n = int(self.cfg.safety.max_items)
            if len(leaves) > n:
                self.log.info(f"按 safety.max_items 只处理前 {n} 项")
                leaves = leaves[:n]
        for i, lf in enumerate(leaves, 1):
            self.log.info(f"  [{i}/{len(leaves)}] {lf.display}")
        return leaves

    def _pick_interactively(self, leaves):
        """控制台勾选单元。"""
        units = []
        for lf in leaves:
            u = lf.unit or "(默认)"
            if u not in units:
                units.append(u)
        if not units:
            return leaves
        print("\n检测到以下单元：")
        for i, u in enumerate(units, 1):
            n = sum(1 for l in leaves if (l.unit or "(默认)") == u)
            print(f"  [{i}] {u}  ({n} 项)")
        print("\n输入要学习的单元序号（如 1 3 5 / 1-3 / all），回车=全部：")
        try:
            spec = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            spec = ""
        if not spec or spec.lower() == "all":
            return leaves

        wanted = set()
        for part in re.split(r"[,\s，]+", spec):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit():
                    wanted.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                wanted.add(int(part))
        if not wanted:
            return leaves
        kept = [l for l in leaves if (units.index(l.unit or "(默认)") + 1) in wanted]
        self.log.info(f"已勾选 {len(kept)} 项")
        return kept

    # ==================================================================
    def _execute(self, leaves):
        rounds = max(1, int(self.cfg.safety.retry_rounds) + 1)
        for rnd in range(1, rounds + 1):
            todo = [l for l in leaves if l.status in ("pending", "failed")]
            if not todo:
                break
            if rnd > 1:
                self.log.info("=" * 62)
                self.log.info(f"第 {rnd} 轮：重试上一轮未成功的 {len(todo)} 项")
                self.log.info("=" * 62)
            self._plan_budget(todo)

            for i, leaf in enumerate(todo, 1):
                if self._should_stop():
                    return
                self.log.info("-" * 62)
                self.log.info(f"[{i}/{len(todo)}] {leaf.display}")
                try:
                    self._process_leaf(leaf)
                except Exception as exc:  # noqa: BLE001
                    self.log.error(f"  处理失败: {str(exc)[:160]}")
                    leaf.status = "failed"
                    leaf.note = str(exc)[:80]
                self.log.info(f"  → {leaf.status}  {leaf.note}")
                self._try_save_tree()

    def _plan_budget(self, leaves):
        """计算时长预算。

        total 模式需要知道每个叶子下有几种 Tab/Task 才能均分。全量探测要逐个打开页面，
        上百个叶子会很慢，所以**只抽样前几个**，用样本均值外推到全部。
        抽样数由 duration.probe_samples 控制（默认 3，设 0 表示全量探测）。
        """
        if self.cfg.duration.mode != "total":
            self.budget.plan(len(leaves))
            return

        samples = int(self.cfg.duration.get("probe_samples", 3) or 0)
        if samples <= 0 or samples >= len(leaves):
            sample_leaves = leaves
        else:
            sample_leaves = leaves[:samples]

        self.log.info(f"抽样 {len(sample_leaves)} 个目录项探测 Tab/Task 数量…")
        page = self.session.page
        counts = []
        for leaf in sample_leaves:
            if not M.click_leaf(page, leaf, self.log, self.humanizer, settle_s=2.0):
                continue
            counts.append(self._probe_counts(page))

        if not counts:
            self.log.warning("  探测失败，按每项 1 Tab × 1 Task 估算")
            counts = [(1, 1)]

        avg_tabs = sum(c[0] for c in counts) / len(counts)
        avg_tasks = sum(c[1] for c in counts) / len(counts)
        self.log.info(f"  样本均值：{avg_tabs:.1f} Tab × {avg_tasks:.1f} Task "
                      f"→ 全课合计约 {len(leaves) * avg_tabs * avg_tasks:.0f} 个 Task")
        # 用外推值构造等价的 plan_items（精确值不重要，只要总量对）
        unit = max(1, round(avg_tabs * avg_tasks))
        self.budget.plan(len(leaves), [(lf, 1, unit) for lf in leaves])

    def _probe_counts(self, page):
        tab_list = T.list_tabs(page)
        n_tabs = max(1, len(tab_list))
        n_tasks = max(1, len(T.list_tasks(page)))
        return n_tabs, n_tasks

    # ==================================================================
    def _process_leaf(self, leaf):
        page = self.session.page
        if not M.click_leaf(page, leaf, self.log, self.humanizer):
            # 重扫一次再试（目录可能被重绘，data-upc-id 丢了）
            self.log.info("  重新扫描目录后重试定位…")
            M.discover_leaves(page, self.log, self.humanizer)
            if not M.click_leaf(page, leaf, self.log, self.humanizer):
                leaf.status = "failed"
                leaf.note = "节点定位失败"
                return

        self.popup.sweep(page, rounds=2)

        if self._risk_detected(page):
            self._handle_risk()
            leaf.status = "failed"
            leaf.note = "触发风控"
            return

        tab_list = T.list_tabs(page)
        if not tab_list:
            # 没有 Tab：整个叶子就是一个 Task
            self.log.info("  无 Tab（单页课件），按一个 Task 处理")
            ok = self._process_task(page, leaf, None, None, 1.0)
            leaf.status = "done" if ok else "failed"
            return

        self.log.info(f"  {len(tab_list)} 个 Tab")
        any_ok = False
        for ti, tab in enumerate(tab_list):
            if self._should_stop():
                break
            if not T.click_tab(page, tab, self.log, self.humanizer):
                self.log.warning(f"    切换 Tab 失败：{tab.name}，跳过")
                continue
            task_list = T.list_tasks(page)
            if not task_list:
                self.log.info(f"    Tab『{tab.name}』没有 Task，直接停留")
                dwell(self.session, self.cfg, self.log, page,
                      self.budget.next_seconds(), f"{tab.name}",
                      self.popup, self.humanizer, self._keepalive, has_media)
                any_ok = True
                continue

            self.log.info(f"    Tab[{ti + 1}/{len(tab_list)}] {tab.name} → "
                          f"{len(task_list)} 个 Task")
            for ki, task in enumerate(task_list):
                if self._should_stop():
                    break
                if not T.click_task(page, task, self.log, self.humanizer):
                    self.log.warning(f"      切换 Task 失败：{task.name}，跳过")
                    continue
                ok = self._process_task(page, leaf, tab, task)
                any_ok = any_ok or ok

        leaf.status = "done" if any_ok else "failed"
        if not leaf.note and any_ok:
            leaf.note = "已处理"

    # ------------------------------------------------------------------
    def _process_task(self, page, leaf, tab, task):
        """处理一个 Task：播放媒体 → 答题 → 口语 → 补时长。"""
        label = " / ".join(x for x in (
            tab.name if tab else "", task.name if task else "") if x) or leaf.title
        self.log.info(f"      ▸ {label}")
        self.popup.sweep(page, rounds=1)
        spent = 0.0
        ok = True

        # ---- 1. 媒体 ----
        try:
            has_v = V.find_media(page, timeout_s=4)
            if has_v[1] is not None:
                status, note = V.play_video(
                    self.session, self.cfg, self.log, page,
                    self.popup, self.humanizer, self._keepalive,
                )
                self.log.info(f"        [媒体] {status} / {note}")
                if status == "done":
                    self.report.video_count += 1
                elif status in ("failed", "manual"):
                    ok = False
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"        [媒体] 异常: {str(exc)[:100]}")

        # ---- 2. 单元测试 / 作业 ----
        if self.cfg.quiz.enabled:
            try:
                res = self.quiz_solver.run(page, label)
                if res.get("questions"):
                    self.report.quiz_stats["questions"] += res["questions"]
                    self.report.quiz_stats["answered"] += res.get("answered", 0)
                    self.report.quiz_stats["submitted"] += 1 if res.get("submitted") else 0
                    self.report.quiz_stats["learned"] += res.get("learned", 0)
            except Exception as exc:  # noqa: BLE001
                self.log.warning(f"        [答题] 异常: {str(exc)[:120]}")

        # ---- 3. 口语 ----
        if self.cfg.speaking.enabled:
            try:
                if SP.has_speaking_task(page):
                    res = SP.run(page, self.cfg, self.log,
                                 self.humanizer, self.popup, self._keepalive)
                    self.report.speaking_count += res.get("handled", 0)
            except Exception as exc:  # noqa: BLE001
                self.log.warning(f"        [口语] 异常: {str(exc)[:120]}")

        # ---- 4. 补足停留时长 ----
        target = self.budget.next_seconds()
        remain = max(0.0, target - spent)
        if remain >= 3:
            status, actual = dwell(
                self.session, self.cfg, self.log, page, remain, label,
                self.popup, self.humanizer, self._keepalive, has_media,
            )
            self.report.duration_spent += actual
            if status != "ok":
                ok = False
        else:
            self.log.info(f"        内容本身已消耗足够时间（目标 {target:.0f}s），不再额外停留")

        self._task_counter += 1
        self.humanizer.maybe_long_pause(self.log)
        gap = float(self.cfg.safety.min_task_interval_s or 0)
        if gap > 0:
            time.sleep(gap + random.uniform(0, 2))
        return ok

    # ==================================================================
    def _keepalive(self, context=""):
        """会话健康检查回调。返回 False 表示应当停止。"""
        if self._should_stop():
            return False
        now = time.time()
        interval = float(self.cfg.login.check_interval_s or 90)
        if now - self._last_check < interval:
            return True
        self._last_check = now

        status = self.session.probe_session()
        if status == "ok":
            return True
        if status == "offline":
            self.log.warning(f"  [{context}] 网络断开，等待恢复…")
            for delay in self.cfg.login.net_retry_delays_s:
                time.sleep(float(delay))
                if self.session.probe_session() == "ok":
                    self.log.info("  网络已恢复")
                    return True
                if self._should_stop():
                    return False
            self.log.error("  网络长时间未恢复，停止")
            return False
        if status == "logged_out":
            if not self.cfg.login.auto_relogin:
                self.log.error(f"  [{context}] 登录失效且未开启自动重登，停止")
                return False
            self.log.warning(f"  [{context}] 登录失效，尝试自动重登…")
            if L.relogin(self.session, self.cfg, self.log):
                self.log.info("  重登成功，继续")
                return True
            self.log.error("  重登失败，停止")
            return False
        return True

    def _should_stop(self):
        if self._stop:
            return True
        limit = float(self.cfg.safety.max_runtime_min or 0)
        if limit > 0 and (time.time() - self._start_ts) / 60.0 >= limit:
            self.log.warning(f"已达最长运行时间 {limit:.0f} 分钟，停止")
            self._stop = True
            return True
        return False

    # ------------------------------------------------------------------
    def _risk_detected(self, page):
        for fr in page.frames:
            try:
                text = fr.evaluate("() => (document.body ? document.body.innerText : '')") or ""
            except Exception:  # noqa: BLE001
                continue
            if any(h in text for h in RISK_HINTS):
                return True
        return False

    def _handle_risk(self):
        self.log.warning("!" * 62)
        self.log.warning("检测到风控 / 验证页面")
        self.log.warning("!" * 62)
        self.report.add_note("触发风控页面")
        if self.cfg.safety.stop_on_risk:
            self._stop = True
            return
        self.log.warning("请在浏览器中手动完成验证，程序等待 180 秒后继续…")
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(5)
            if not self._risk_detected(self.session.page):
                self.log.info("验证已通过，继续运行")
                return
        self.log.error("等待超时，停止运行")
        self._stop = True

    # ------------------------------------------------------------------
    def _try_save_tree(self):
        """定期把目录状态落盘，中断后可据此续跑。"""
        try:
            import json
            from pathlib import Path
            p = Path(self.cfg.state_dir) / "progress.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "course": self.report.course,
                "items": [l.to_dict() for l in self.report.leaves],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _summary(self):
        self.report.save()
        d = self.report.to_dict()
        c = d["counts"]
        self.log.info("=" * 62)
        self.log.info("运行结束")
        self.log.info(f"  课程      : {d['course'] or '(未识别)'}")
        self.log.info(f"  总耗时    : {d['elapsed_min']} 分钟")
        self.log.info(f"  学习时长  : {d['duration_spent_min']} 分钟")
        self.log.info(f"  可学习项  : {c['total']}（完成 {c['done']} / 失败 {c['failed']} / "
                      f"跳过 {c['skipped']} / 未处理 {c['pending']}）")
        self.log.info(f"  视频      : {d['video_played']} 个")
        self.log.info(f"  答题      : 识别 {d['quiz']['questions']} / "
                      f"作答 {d['quiz']['answered']} / 新学到 {d['quiz']['learned']}")
        if self.cfg.speaking.enabled:
            self.log.info(f"  口语      : {d['speaking_handled']} 处")
        self.log.info("=" * 62)
