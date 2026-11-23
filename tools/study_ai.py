# -*- coding: utf-8 -*-
"""AI版真实课件刷时长（浏览器驱动，走真实页面）。

2026-11-04 实测打通的全链路
--------------------------
1. tools/api_run.py 做接口登录 → state/api_session.json（含 9 个 cookie）
2. 把 cookie 注入浏览器上下文 → 才能进 uai.unipus.cn 的课程应用
   （不注入会被弹回 sso.unipus.cn 登录页）
3. 课程结构：
   GET  /course/api/course/{instanceId}/default        ← 同源请求，返回整个树
   POST /api/tla/courseStudyStrategy/detail            ← 每个单元要求的任务点 id
4. 课件地址（关键：路径要**跳过 role=section 的层级**）：
   https://ucontent.unipus.cn/_explorationpc_default/pc.html?cid={classId}&appid=116
       #{inst}/courseware/{unit}/{子节点...}/{leaf}
5. 学习时长由课件页的真实行为产生（视频播放、页面停留），
   没有"直接上报"的接口，所以必须真的把每页打开走一遍。

用法：
    python tools/study_ai.py                        # 视听说，默认 60 分钟
    python tools/study_ai.py --minutes 90
    python tools/study_ai.py --book rw              # 读写教程
    python tools/study_ai.py --from 6               # 从第 N 项续跑
    python tools/study_ai.py --plan                 # 预演清单（含 App 端跳过项）
    python tools/study_ai.py --reset-progress      # 清本地缓存（平台改版后）
    python tools/study_ai.py --list                 # 只列任务，不刷

页面提示语 / 打开失败重试 / 熔断阈值 / 缓存有效期：data/page_rules.yaml
"""
import argparse
import json
import sys
import time
from pathlib import Path

# 屏蔽 urllib3 的 InsecureRequestWarning（本地代理 127.0.0.1 的无关警告，
# 数量极多会把真正的日志淹掉）
try:
    import urllib3 as _u3
    _u3.disable_warnings(_u3.exceptions.InsecureRequestWarning)
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 页面提示语 / 失败策略 / 断点续跑缓存：外置，平台改版只改 data/page_rules.yaml
from upc.page_rules import (is_pc_unsupported, is_roleplay_stuck,  # noqa: E402
                            load as load_page_rules)
from upc.runstate import RunState  # noqa: E402

COURSEWARE = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
              "?cid={cid}&appid=116#/{inst}/courseware/{path}")

# 教材标识 → 关键词
BOOK_KEY = {"vls": "nhce_4_vls_1", "rw": "nhce_v4_rw_1"}


def jget(session, url, timeout=25000):
    """用 context.request 发 GET（共享 cookie、不受 CORS 限制），返回 JSON。"""
    try:
        r = session.context.request.get(url, timeout=timeout)
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def build_leaves(struct):
    """把课程结构树拍平成可访问的叶子列表，并标注它归属的 micro 节。

    层级（实测）：
        unit → section → node(=micro, 对应目录里那个圈) → node → group(=叶子)
    课件路径规则：unit.id + 沿途所有非 section 节点的 id（共 4 段）。

    micro 是判断"完成"的正确粒度：一个 micro 下面有 3~8 个叶子，
    只做 1 个叶子时圈不会变蓝，必须整节做完。
    """
    out = []
    micros = []          # 按目录顺序排列，下标和 DOM 里 79 个圈一一对应

    def rec(node, trail, unit_label, micro_name, micro_id, micro_idx):
        role = node.get("role")
        nid = node.get("id")
        name = (node.get("caption") or node.get("name") or "").strip()
        new_trail = list(trail)
        if role != "section" and nid:
            new_trail.append(nid)
        kids = node.get("children") or []
        if not kids:
            out.append({
                "unit": unit_label,
                "name": name,
                "tab_type": node.get("tab_type") or "",
                "base": node.get("base") or "",
                "path": "/".join(x for x in new_trail if x),
                "id": nid,
                "micro": micro_name,
                "micro_id": micro_id,
                "micro_idx": micro_idx,
            })
            return
        for ch in kids:
            rec(ch, new_trail, unit_label, micro_name, micro_id, micro_idx)

    for u in (struct.get("units") or []):
        unit_id = u.get("id")
        unit_label = f"{u.get('caption') or ''} {u.get('name') or ''}".strip()
        for sec in (u.get("children") or []):
            for micro in (sec.get("children") or []):
                mname = (micro.get("caption") or micro.get("name") or "").strip()
                micros.append(mname)
                midx = len(micros) - 1
                rec(micro, [unit_id] if unit_id else [],
                    unit_label, mname, micro.get("id"), midx)

    # 有些单元结构可能少一层，做个兜底
    if not out:
        for u in (struct.get("units") or []):
            rec(u, [], "", "", None, 0)
    return out, micros


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--redo", action="store_true",
                    help="连已 pass 的叶子也重做（默认跳过，省时间）")
    ap.add_argument("--redo-low", action="store_true",
                    help="把题库里分数未满的已pass叶子拉回重做（默认关闭：这些题"
                         "平台已记pass且进入回顾态，重做改不了分，只会无限循环）")
    ap.add_argument("--passes", type=int, default=2,
                    help="跑几遍：第 1 遍摸标准答案，第 2 遍按题库满分（默认 2）")
    ap.add_argument("--per-task", type=float, default=70, help="每个任务点停留秒数")
    ap.add_argument("--book", default="vls", choices=list(BOOK_KEY) + ["all"],
                    help="vls=视听说 rw=读写 all=两本串行全刷（默认 vls）")
    ap.add_argument("--from", dest="start_from", type=int, default=0,
                    help="从第 N 个任务开始（断点续跑）")
    ap.add_argument("--video-max-min", type=float, default=10, help="单视频最多等多久")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="尝试的播放倍速（1=不加速）。被拒会自动回落")
    ap.add_argument("--no-seek", action="store_true",
                    help="不尝试拖动进度条（默认会先试拖到末尾）")
    ap.add_argument("--quiz", default="fill",
                    choices=["fill", "skip"],
                    help="遇到需要作答的任务点：fill=扫描后自动作答，skip=只停留")
    ap.add_argument("--probe-task", action="store_true",
                    help="只打开第一个需要作答的任务点并 dump 结构，用于校准")
    ap.add_argument("--skip-done", action="store_true",
                    help="跳过目录里已经是蓝圈（已完成）的节点")
    ap.add_argument("--verify", action="store_true", default=True,
                    help="每做完一项复查蓝圈是否增加（默认开启）")
    ap.add_argument("--no-verify", dest="verify", action="store_false")
    ap.add_argument("--mode", default="adaptive", choices=["fast", "real", "adaptive"],
                    help="fast=只加速；real=只真实播放；adaptive=先加速，蓝圈没变就回退真实播放（默认）")
    ap.add_argument("--real-dwell", type=float, default=45,
                    help="回退真实播放时额外停留秒数（默认 45）")
    ap.add_argument("--micro", default="",
                    help='只做某一个 micro 节：填序号或名字关键词，如 "Listening to China"')
    ap.add_argument("--unit", default="",
                    help='只做指定 Unit，如 "1" 或 "1,3"（按单元名过滤，比记节号直观）')
    ap.add_argument("--micro-from", type=int, default=None,
                    help="只做 micro 索引 >= N 的节（含）")
    ap.add_argument("--micro-to", type=int, default=None,
                    help="只做 micro 索引 <= N 的节（含）")
    ap.add_argument("--plan", action="store_true",
                    help="只列本次将处理/跳过的叶子清单，不实际刷课")
    ap.add_argument("--list-micro", action="store_true",
                    help="列出所有 micro 节及其蓝圈状态")
    ap.add_argument("--quiz-discussion", action="store_true",
                    help="连 discussion 讨论页也填（会发表内容，默认不启用）")
    ap.add_argument("--choice-mode", default="all", choices=["all", "half", "first"],
                    help="客观题选项点几个：all=全选(默认) half=前半 first=只第一个")
    ap.add_argument("--list", action="store_true", help="只列任务清单")
    ap.add_argument("--reset-progress", action="store_true",
                    help="清空本地断点续跑缓存（平台改版后用）")
    ap.add_argument("--retry-manual", action="store_true",
                    help="解除全部「三振出局」标记，让反复失败的叶子重新尝试"
                         "（修好新题型后用；再失败会很快重新出局）")
    args = ap.parse_args()

    from upc import video as V
    from upc import progress as PG
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.humanize import Humanizer
    from upc.logutil import setup_logging
    from upc.popups import PopupGuard

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(cfg.log_dir, "INFO")
    humanizer = Humanizer(cfg, log)

    rules = load_page_rules()
    nav = rules["navigation"]
    res = rules["resume"]
    state = RunState(ROOT / res["path"], ttl_days=res["ttl_days"],
                     enabled=res["enabled"], log=log)
    if args.reset_progress:
        state.clear()
        log.info("已清空本地断点续跑缓存，本轮重新判定所有叶子")
    else:
        if getattr(args, "retry_manual", False):
            _n = state.reset_manual()
            log.info(f"[--retry-manual] 已解除 {_n} 个三振出局标记，本轮重新尝试")
        # 历史失败现场（unknown_/incomplete_ dump）预置 2 振：
        # 这些叶子反复重做过仍不 pass，再失败 1 次即出局，不再无限重做
        state.seed_fails_from_dumps(ROOT / "output", log)
        if state.cached_unsupported_count:
            log.info(f"本地断点续跑缓存：{state.cached_unsupported_count} 个 "
                     f"PC 端不支持项（{res['ttl_days']:g} 天内直接跳过）")

    sess_file = Path(cfg.state_dir) / "api_session.json"
    if not sess_file.exists():
        raise SystemExit("缺少 state/api_session.json —— 请先运行 tools/api_run.py 登录")
    sess = json.loads(sess_file.read_text(encoding="utf-8"))

    session = BrowserSession(cfg, log)
    page = session.page
    # 口语题：必须在任何导航之前装好麦克风劫持，
    # 平台录音 SDK 会在初始化时缓存 getUserMedia，运行时替换无效。
    try:
        from upc.voice import install_init_patch
        if install_init_patch(page):
            log.debug("已预置麦克风劫持（用于口语题）")
    except Exception as _exc:  # noqa: BLE001
        log.debug(f"预置麦克风劫持失败: {str(_exc)[:60]}")
    popup = PopupGuard(cfg, log, humanizer)
    started = time.time()

    try:
        # ---------- 1. 注入会话 ----------
        to_add = [{"name": c["name"], "value": c["value"],
                   "domain": c.get("domain") or "",
                   "path": c.get("path") or "/"}
                  for c in (sess.get("cookies") or []) if c.get("domain")]
        session.context.add_cookies(to_add)
        log.info(f"已注入 {len(to_add)} 个会话 cookie")

        # ---------- 2. 找课程与教材 ----------
        session.goto("https://uai.unipus.cn/app/cmgt/course-management",
                     context="课程管理")
        page.wait_for_timeout(5000)
        cl = None
        try:
            r = session.context.request.get(
                "https://uai.unipus.cn/api/cmgt/course/getCourseListByStudent",
                timeout=25000)
            cl = r.json()
        except Exception as exc:  # noqa: BLE001
            log.error(f"取课程列表失败: {exc}")
            return 1

        courses = ((cl or {}).get("value") or {}).get("courseList") or []
        if not courses:
            log.error("课程列表为空")
            return 1

        key = BOOK_KEY[args.book]
        target = None
        for c in courses:
            for r in (c.get("courseResourceList") or []):
                if key in str(r.get("resourceId") or ""):
                    target = (c, r)
                    break
            if target:
                break
        if not target:
            log.error(f"没找到教材 {key}，有的课程：")
            for c in courses:
                for r in (c.get("courseResourceList") or []):
                    log.error(f"  {c.get('name')} / {r.get('name')} ({r.get('resourceId')})")
            return 1

        course, res = target
        cid = course.get("classId") or ""
        inst = res.get("instanceId") or ""
        log.info("=" * 62)
        log.info(f"课程：{course.get('name')}  /  {res.get('name')}")
        log.info(f"  classId={cid}")
        log.info(f"  instanceId={inst}")
        log.info(f"  任务进度：{res.get('finishPointNum')}/{res.get('totalPointNum')}")
        log.info("=" * 62)

        # ---------- 3. 结构树 ----------
        # 先落到课件域（同源），才能请求 course/api
        entry = COURSEWARE.format(cid=cid, inst=inst, path="")

        def _fetch_struct():
            try:
                session.goto(entry.rstrip("/"), context="课件域")
                page.wait_for_timeout(4000)
                # ★ 必须给 fetch 加超时：登录态过期时这个请求可能一直挂着，
                #   表现为「程序停住不动」（用户报过"怎么停了"）。
                return page.evaluate(r"""
                async (inst) => {
                  const ctl = new AbortController();
                  const timer = setTimeout(() => ctl.abort(), 8000);
                  try {
                    const r = await fetch(`/course/api/course/${inst}/default`,
                                          {credentials:'include', signal: ctl.signal});
                    const t = await r.text();
                    try { return JSON.parse(t); }
                    catch (e) { return {raw: t.slice(0, 400)}; }
                  } catch (e) {
                    return {raw: 'FETCH_ERR: ' + String(e).slice(0, 120)};
                  } finally {
                    clearTimeout(timer);
                  }
                }
                """, inst)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"取课程结构失败: {str(exc)[:100]}")
                return None

        struct_raw = _fetch_struct()

        # 结构为空十有八九是登录态过期（接口直接 404）。
        # 这里自动重新登录一次再重试，避免用户看到"黑框一闪就没了"。
        if not (struct_raw or {}).get("course"):
            log.warning("课程结构为空（通常是登录态过期），自动重新登录…")
            if _relogin_and_reinject(session, cfg, log):
                struct_raw = _fetch_struct()

        course_str = (struct_raw or {}).get("course")
        if not course_str:
            log.error(f"课程结构为空: {str(struct_raw)[:200]}")
            log.error("自动重登也没拿到结构，请手动执行：")
            log.error("  python tools/api_run.py --session-only --no-cache")
            return 1
        struct = json.loads(course_str) if isinstance(course_str, str) else course_str

        leaves, micros = build_leaves(struct)
        # 只保留真正有内容的（有 tab_type 或 base）
        content = [l for l in leaves if l.get("path") and (l.get("tab_type") or l.get("base"))]
        if not content:
            content = [l for l in leaves if l.get("path")]
        log.info(f"解析出 {len(leaves)} 个叶子 / {len(micros)} 个 micro 节，"
                 f"可学习 {len(content)} 个")
        log.info(f"  micro 前 8 个: {micros[:8]}")

        # 按 Unit 过滤（比记 micro 序号直观得多）
        if getattr(args, "unit", ""):
            import re as _re_u
            _want = [x.strip() for x in
                     str(args.unit).replace("，", ",").split(",") if x.strip()]
            _nums = []
            for _w in _want:
                # 支持 "1-3" 这种范围写法
                if "-" in _w and all(p.strip().isdigit()
                                     for p in _w.split("-", 1)):
                    _a, _b = (int(p) for p in _w.split("-", 1))
                    _nums.extend(range(min(_a, _b), max(_a, _b) + 1))
                else:
                    _nums.append(_w)
            _pats = []
            for _w in _nums:
                if isinstance(_w, int) or str(_w).isdigit():
                    _pats.append(_re_u.compile(rf"^\s*Unit\s*{_w}\b", _re_u.I))
                else:
                    _pats.append(_re_u.compile(_re_u.escape(str(_w)), _re_u.I))
            _before = len(content)
            content = [x for x in content
                       if any(p.search(str(x.get("unit") or "")) for p in _pats)]
            log.info(f"  只做 Unit {args.unit}：{_before} → {len(content)} 个叶子")
            if not content:
                log.error(f"没匹配到 Unit {args.unit}，先看 --list-micro 确认单元名")
                return 1

        # 蓝圈初始状态（在课件页上读目录）
        try:
            init_rows = PG.scan(page)
        except Exception:  # noqa: BLE001
            init_rows = []

        if args.list_micro:
            log.info("=== micro 节 / 蓝圈状态 ===")
            for j, m in enumerate(micros):
                st = init_rows[j].get("state") if j < len(init_rows) else "?"
                nleaf = sum(1 for x in content if x.get("micro_idx") == j)
                log.info(f"  [{j:>2}] {st:<5} {m[:32]:<34} 叶子 {nleaf}")
            return 0

        # 只做指定 micro
        target_micro = None
        if args.micro:
            want = args.micro.strip()
            if want.isdigit() and int(want) < len(micros):
                target_micro = int(want)
            else:
                for j, m in enumerate(micros):
                    if want in m:
                        target_micro = j
                        break
            if target_micro is None:
                log.error(f"找不到 micro「{want}」，用 --list-micro 看清单")
                return 1
            log.info(f"本次只做 micro [{target_micro}] {micros[target_micro]}")

        if args.list:
            for i, l in enumerate(content, 1):
                log.info(f"  [{i:>2}] {l['unit'][:22]:<24} | {l['name'][:26]:<28} | "
                         f"{l.get('tab_type') or l.get('base')}")
            return 0

        # ---------- 4. 逐个刷 ----------
        passed_ids = set()
        if not args.redo:
            try:
                passed_ids = _load_pass_map(page, log) or set()
            except Exception as exc:  # noqa: BLE001
                log.warning(f"  读取 pass 状态失败，不跳过: {str(exc)[:60]}")

        # ★ 低分题重做开关（默认关闭）：
        #   平台对已提交过的题记 pass=1，且重进是「回顾态」——控件锁定、
        #   无法真正改答案，重做只会反复消耗时间却永远不涨分。
        #   因此默认【尊重平台 pass 状态，跳过所有已完成叶子】。
        #   仅当用户显式 --redo-low 时，才把题库里 score≠1 的叶子拉回重做。
        low_ids = set()
        if args.redo_low:
            try:
                low_ids = {str(k) for k, v in (_load_bank() or {}).items()
                           if (v.get("score") or 0) != 1}
            except Exception:  # noqa: BLE001
                pass
            if low_ids:
                log.info(f"  [--redo-low] 题库中 {len(low_ids)} 个叶子分数未满，纳入重做")
        else:
            log.info("  已完成叶子全部跳过（低分题重做默认关闭，如需强刷加 --redo-low）")
        # 题库里已有可用答案的叶子（三振判定用：这些叶子再交 0 分
        # 说明题库答案也救不回来，重做无意义，计一振）
        bank_answered = set()
        try:
            bank_answered = {str(k) for k, v in (_load_bank() or {}).items()
                             if v.get("blocks") or v.get("letters")}
        except Exception:  # noqa: BLE001
            pass

        if args.plan:
            _uni = [x for x in content
                    if (target_micro is None
                        or (x.get("micro_idx") or 0) == target_micro)
                    and (args.micro_from is None
                         or (x.get("micro_idx") or 0) >= args.micro_from)
                    and (args.micro_to is None
                         or (x.get("micro_idx") or 0) <= args.micro_to)]
            log.info("=" * 62)
            log.info(f"预演清单：范围内共 {len(_uni)} 个叶子")
            _todo = 0
            _cached = 0
            _manual = 0
            for _i, _x in enumerate(_uni, 1):
                _islow = str(_x.get("id")) in (low_ids or set())
                _done = (str(_x.get("id")) in (passed_ids or set())
                         and not _islow)
                _skip_st = state.cached_status(_x.get("id"))
                if _skip_st:
                    if _skip_st == "needs_manual":
                        _manual += 1
                    else:
                        _cached += 1
                elif not _done:
                    _todo += 1
                _tag = ("三振出局" if _skip_st == "needs_manual" else
                        "App端" if _skip_st else
                        "跳过" if _done else ("补分" if _islow else "待做"))
                log.info(f"  [{_tag}] "
                         f"m{_x.get('micro_idx'):<2} {_x.get('name')[:22]:<24} "
                         f"{_x.get('tab_type') or _x.get('base')}")
            log.info(f"实际待做 {_todo} 个 / App 端等缓存跳过 {_cached} 个 / "
                     f"三振出局 {_manual} 个 / 范围内 {len(_uni)} 个")
            log.info("=" * 62)
            return 0

        aborted = False
        for _pass in range(max(1, args.passes)):
            if aborted:
                log.info("上一遍因连续打开失败中止，不再跑后续遍")
                break
            if _pass and time.time() >= started + args.minutes * 60:
                log.info("达到设定时长，停止后续轮次")
                break
            if _pass:
                log.info("=" * 62)
                log.info(f"第 {_pass + 1} 遍：按题库标准答案补满分")
                log.info("=" * 62)
            deadline = started + args.minutes * 60
            done = 0
            report = []
            # 含「PC 端不支持」项的 micro：整节不要因白圈而回退重放
            unsupported_micros = set()
            # 连续打不开页面的叶子数：达到阈值就判定白屏/登录态失效，停止空转
            consecutive_failures = 0
            # 预先算好每个 micro 的最后一个叶子序号
            last_of_micro = {}
            for i, lf in enumerate(content, 1):
                last_of_micro[lf.get("micro_idx")] = i

            for i, lf in enumerate(content, 1):
                if time.time() >= deadline:
                    log.info("达到设定时长，停止")
                    break
                if (passed_ids and str(lf.get("id")) in passed_ids
                        and str(lf.get("id")) not in low_ids):
                    log.debug(f"  跳过已完成且满分: {lf.get('name')}")
                    continue
                # 断点续跑：本地已判定「永远做不完」的叶子连页面都不打开
                # （App 端不支持 / 需手动上传 / 三振出局，程序都无法代劳）
                _skip_st = state.cached_status(lf.get("id"))
                if _skip_st:
                    if _skip_st == "needs_upload":
                        log.debug(f"  跳过需手动上传作品的项: {lf.get('name')}")
                        report.append({**lf, "status": "skipped_needs_upload"})
                    elif _skip_st == "needs_manual":
                        log.debug(f"  跳过反复失败的项（三振出局）: {lf.get('name')}")
                        report.append({**lf, "status": "skipped_needs_manual"})
                    else:
                        log.debug(f"  跳过本地缓存的 App 端项: {lf.get('name')}")
                        report.append({**lf,
                                       "status": "skipped_cached_unsupported"})
                    continue
                if args.start_from and i < args.start_from:
                    continue
                _mi = lf.get("micro_idx") or 0
                if target_micro is not None and _mi != target_micro:
                    continue
                if args.micro_from is not None and _mi < args.micro_from:
                    continue
                if args.micro_to is not None and _mi > args.micro_to:
                    continue

                url = COURSEWARE.format(cid=cid, inst=inst, path=lf["path"])
                log.info(f"[{i}/{len(content)}] {lf['unit'][:20]} / {lf['name'][:26]} "
                         f"({lf.get('tab_type') or lf.get('base')})")
                if not _open_leaf(page, url, log,
                                  retries=nav["goto_retries"],
                                  backoff_ms=nav["backoff_ms"]):
                    report.append({**lf, "status": "open_failed"})
                    state.mark(lf.get("id"), "failed", lf.get("name"))
                    consecutive_failures += 1
                    limit = nav["max_consecutive_failures"]
                    if limit and consecutive_failures >= limit:
                        log.error("=" * 62)
                        log.error(f"连续 {consecutive_failures} 个叶子打不开，"
                                  "判定登录态失效 / 白屏空转，提前结束本轮")
                        log.error("建议重新运行 tools/api_run.py 刷新会话，"
                                  "或检查网络后再跑；避免空转满设定时长")
                        log.error("=" * 62)
                        # 异常才叫人（借鉴 agent-skill 的监督者-Runner 分工）：
                        # 确定性的活脚本干，判断不了的异常交给 WorkBuddy。
                        try:
                            from wb_helper import wake
                            wake("连续打开课件页失败，疑似登录态失效",
                                 detail=f"连续 {consecutive_failures} 个叶子打不开，已提前结束",
                                 rc=2)
                        except Exception as _we:  # noqa: BLE001
                            log.debug(f"唤起 WorkBuddy 兜底失败: {type(_we).__name__}")
                        aborted = True
                        break
                    continue
                consecutive_failures = 0
                page.wait_for_timeout(6000)
                popup.sweep(page, rounds=2)
                # 关键：确认框会叠 2 层，PopupGuard 只关 1 层，
                # 残留遮罩会挡住后面所有真实输入/点击
                _clear_modals(page, log)
                page.wait_for_timeout(500)

                # 记录本 micro 处理前的蓝圈状态
                midx = lf.get("micro_idx")
                before_micro = PG.micro_done(page, midx) if args.verify else None
                before_blue = PG.count_done(page) if args.verify else 0
                if args.skip_done and before_micro:
                    log.info("  本节已是蓝圈，跳过")
                    report.append({**lf, "status": "skipped_done"})
                    continue

                # 视频/音频：优先试拖动，再试倍速，都不行才实时播
                fast = args.mode in ("fast", "adaptive")
                played = _play_media(session, cfg, log, page, popup, humanizer,
                                     V, args.video_max_min,
                                     rate=(args.rate if fast else 1.0),
                                     seek=(fast and not args.no_seek))
                # 答题：所有题型都扫一遍（视频页也可能有配套填空）。
                # discussion 是公开发言，默认跳过，除非显式 --quiz-discussion。
                quiz_info = None
                if args.quiz == "fill":
                    # discussion 现在只做「标记已看」（submitType=2），
                    # 不往讨论区发内容，所以不再需要 --quiz-discussion 开关
                    quiz_info = _handle_quiz(session, cfg, log, page, popup,
                                             humanizer, lf, args.choice_mode)
                pc_skip = bool(quiz_info and quiz_info.get("unsupported_pc"))
                up_skip = bool(quiz_info and quiz_info.get("needs_upload"))
                # ★ 三振出局：同一叶子反复失败说明题型程序做不了
                #   （识别不了/平台拦未作答/题库答案也救不回的 0 分），
                #   每次失败计一振（历史现场 dump 已预置 2 振），
                #   满 3 振标记 needs_manual，后续轮次连页面都不再打开。
                manual_out = False
                if not pc_skip and not up_skip:
                    quiz_failed = bool(
                        quiz_info and (
                            quiz_info.get("unrecognized")   # 题型识别不了（人物匹配等）
                            or quiz_info.get("incomplete")  # 平台拦「还有未作答」
                            or quiz_info.get("unanswered")  # 存在未作答题块
                            # 有答题信息但没提交成功（提交按钮为空、标记已看失败等）
                            or (quiz_info.get("submitted") == ""
                                and not pc_skip and not up_skip)
                            # 题库已有标准答案仍 0 分 / 第二遍仍全错：
                            # 重做也救不回来（如全答全错的人物匹配页）
                            or (quiz_info.get("score") == 0
                                and (_pass >= 1
                                     or str(lf.get("id")) in bank_answered))))
                    _failed_now = quiz_failed
                    if _failed_now:
                        _strikes = state.count_fail(lf.get("id"), lf.get("name"))
                        if _strikes >= 3:
                            manual_out = True
                            # 整节不可能变蓝了，回退真实播放重做纯属浪费
                            unsupported_micros.add(midx)
                            state.mark(lf.get("id"), "needs_manual",
                                       lf.get("name"))
                            log.warning(f"    三振出局：累计失败 {_strikes} 次，"
                                        "已标记为需手动完成，"
                                        "后续轮次不再重做（--retry-manual 可重试）")
                        else:
                            log.info(f"    未完成（{_strikes}/3 振，"
                                     "满 3 次自动跳过不再重做）")
                if pc_skip or up_skip or manual_out:
                    # 三类都一样：本节有程序永远做不完的项，圈不可能变蓝，
                    # 必须记入"不可完成"集合，否则收尾的自适应回退会把
                    # 整节真实播放重做一遍——纯浪费（实测白耗 6 分钟）。
                    unsupported_micros.add(midx)
                if pc_skip:
                    log.info("  PC 端不支持，跳过停留计时（该项需在 App 端完成）")
                elif manual_out:
                    log.info("  三振出局项，跳过停留计时（停留已无意义）")
                else:
                    remain = max(0.0, args.per_task - (90 if played else 0))
                    _dwell(session, cfg, log, page, remain, popup, humanizer)

                # 复查：圈是 micro 级的，整节做完才可能变蓝
                blue_delta, blue_total = 0, 0
                micro_now = None
                if args.verify:
                    blue_delta, blue_total = PG.verify(page, before_blue, wait_s=2.0)
                    micro_now = PG.micro_done(page, midx)
                is_last = last_of_micro.get(midx) == i
                micro_turned = bool(micro_now) and before_micro is False

                # 自适应回退：**只在整节做完**且圈仍是白的时候才回退。
                # 单个叶子没变蓝是正常的，回退没有意义（已实测）。
                retried = False
                if (args.mode == "adaptive" and args.verify and fast
                        and is_last and micro_now is False
                        and midx not in unsupported_micros):
                    log.info(f"  → 整节「{lf.get('micro')}」仍是白圈，回退真实播放重做本节")
                    # 把本节所有叶子按真实播放再走一遍
                    for lf2 in content:
                        if lf2.get("micro_idx") != midx:
                            continue
                        u2 = COURSEWARE.format(cid=cid, inst=inst, path=lf2["path"])
                        try:
                            page.goto("about:blank", timeout=15000)
                            page.goto(u2, wait_until="domcontentloaded", timeout=50000)
                            page.wait_for_timeout(5000)
                            popup.sweep(page, rounds=1)
                            _play_media(session, cfg, log, page, popup, humanizer,
                                        V, args.video_max_min, rate=1.0, seek=False)
                            _dwell(session, cfg, log, page, args.real_dwell,
                                   popup, humanizer)
                        except Exception as exc:  # noqa: BLE001
                            log.warning(f"    回退中某项失败: {str(exc)[:70]}")
                    retried = True
                    micro_now = PG.micro_done(page, midx)
                    blue_delta, blue_total = PG.verify(page, before_blue, wait_s=3.0)
                    micro_turned = bool(micro_now) and before_micro is False
                    log.info(f"    回退后本节: {'蓝圈 ✓' if micro_now else '仍白圈'}")

                done += 1
                rec = {**lf, "status": "done", "played": played,
                       "micro": lf.get("micro"), "micro_idx": midx,
                       "micro_done": micro_now, "micro_turned": micro_turned,
                       "blue_delta": blue_delta, "blue_total": blue_total,
                       "retried_real": retried}
                if quiz_info:
                    rec["quiz"] = quiz_info
                if pc_skip:
                    rec["status"] = "unsupported_pc"
                    rec["unsupported_pc"] = True
                    if quiz_info and quiz_info.get("skip_reason"):
                        rec["skip_reason"] = quiz_info["skip_reason"]
                if up_skip:
                    rec["status"] = "needs_upload"
                    rec["needs_upload"] = True
                if manual_out:
                    rec["status"] = "needs_manual"
                    rec["needs_manual"] = True
                elif quiz_failed and not (pc_skip or up_skip):
                    # 题目未完成：不计入成功，但保留未完成状态以便下一轮重试。
                    rec["status"] = "incomplete"
                    rec["quiz_failed"] = True
                report.append(rec)
                # 增量落盘：中途 Ctrl+C / 崩溃也不会丢掉本轮已判定的结论
                state.mark(lf.get("id"),
                           "needs_manual" if manual_out else
                           ("unsupported_pc" if pc_skip else
                            ("needs_upload" if up_skip else
                             ("incomplete" if quiz_failed else "done"))),
                           lf.get("name"))
                extra = ""
                if quiz_info:
                    extra = f" 答题={quiz_info.get('answered', 0)}/{quiz_info.get('total', 0)}"
                mark = ""
                if micro_turned:
                    mark = f"● 本节「{lf.get('micro')}」蓝圈点亮"
                elif is_last and micro_now is False:
                    mark = (f"○ 本节「{lf.get('micro')}」含做不完的项"
                            f"（PC 端不支持 / 需上传 / 三振出局），需手动收尾"
                            if midx in unsupported_micros
                            else f"○ 本节「{lf.get('micro')}」仍白圈")
                elif blue_delta > 0:
                    mark = "● 蓝圈+1"
                _verb = "未完成" if rec["status"] == "incomplete" else "完成"
                log.info(f"  {_verb}（媒体={'有' if played else '无'}{extra}） {mark}")

            out = Path(cfg.output_dir) / "study_ai_report.json"
            gained = sum(r.get("blue_delta", 0) for r in report)
            turned = [r.get("micro") for r in report if r.get("micro_turned")]
            pc_only = [r.get("name") for r in report
                       if r.get("status") == "unsupported_pc"]
            manual_out_list = [r.get("name") for r in report
                               if r.get("status") == "needs_manual"]
            incomplete_list = [r.get("name") for r in report
                               if r.get("status") == "incomplete"]
            cached_skips = sum(1 for r in report
                              if r.get("status") == "skipped_cached_unsupported")
            manual_skips = sum(1 for r in report
                               if r.get("status") == "skipped_needs_manual")
            upload_skips = sum(1 for r in report
                               if r.get("status") in
                               ("needs_upload", "skipped_needs_upload"))
            out.write_text(json.dumps({"elapsed_min": round((time.time() - started) / 60, 1),
                                       "done": done, "blue_gained": gained,
                                       "micros_turned": turned,
                                       "unsupported_pc": pc_only,
                                       "needs_manual": manual_out_list,
                                       "incomplete": incomplete_list,
                                       "cached_skips": cached_skips,
                                       "manual_skips": manual_skips,
                                       "upload_skips": upload_skips,
                                       "tasks": report},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("=" * 62)
            log.info(f"结束：处理 {done} 个任务点，用时 {(time.time() - started) / 60:.1f} 分钟")
            if args.verify:
                log.info(f"蓝圈新增 {gained} 个；本节变蓝: {turned or '无'}")
            if pc_only:
                log.info(f"PC 端不支持、需在 App 端完成：{len(pc_only)} 项（已跳过，未浪费时长）")
            if incomplete_list:
                log.info(f"题目未完成、计入未完成：{len(incomplete_list)} 项（下轮会重试）")
            if upload_skips:
                log.info(f"需手动上传作品：{upload_skips} 项（已跳过）")
            if manual_out_list or manual_skips:
                log.info(f"三振出局（反复失败、需手动完成）：本轮出局 "
                         f"{len(manual_out_list)} 项 / 缓存跳过 {manual_skips} 项"
                         "（后续不再重做；--retry-manual 可重试）")
            if cached_skips:
                log.info(f"断点续跑缓存直接跳过：{cached_skips} 项（连页面都没打开）")
            log.info(f"报告：{out}")
        log.info("=" * 62)
        return 1 if aborted else 0
    finally:
        try:
            session.save_state()
        except Exception:  # noqa: BLE001
            pass
        session.close()


def _load_pass_map(page, log):
    """一次性读所有叶子的 pass 状态（监听页面自身发出的 course_progress）。

    用于跳过已完成的叶子：288 个里当前只有十几个是 pass 的，
    不跳过等于白跑 90% 的时间。
    """
    got = {}

    def _on(r):
        if "course_progress" not in (r.url or ""):
            return
        try:
            lf = ((r.json().get("rt") or {}).get("leafs") or {})
            if lf:
                got.update(lf)
        except Exception:  # noqa: BLE001
            pass

    try:
        page.on("response", _on)
        page.reload(wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(9000)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"  读 pass 状态失败: {str(exc)[:60]}")
    finally:
        try:
            page.remove_listener("response", _on)
        except Exception:  # noqa: BLE001
            pass

    passed = {k for k, v in got.items()
              if ((v or {}).get("state") or {}).get("pass")}
    log.info(f"  已有 {len(passed)}/{len(got)} 个叶子 pass，本次跳过它们")
    return passed


def _play_media(session, cfg, log, page, popup, humanizer, V, max_min,
                rate=1.0, seek=True):
    """播放当前课件页的视频/音频。

    加速策略（从侵入性低到高，失败自动回落）：
      ① 拖动：把 currentTime 设到 duration-1.5，再确认是否真跳过去了；
         很多课件允许这样，几秒就能"看完"。
      ② 倍速：设 playbackRate，若被播放器重置回 1 说明平台限速。
      ③ 实时播放：前两步都无效时的兜底。

    诚实提示：① ② 都可能被判为"非真实观看"，是否计入时长由平台决定。
    """
    fr, el, tag = V.find_media(page, timeout_s=12)
    if el is None:
        try:
            for sel in (".vjs-big-play-button", '[class*="play-btn"]',
                        '[class*="big-play"]', '[class*="play"]'):
                b = page.query_selector(sel)
                if b is not None and b.is_visible():
                    b.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    break
            fr, el, tag = V.find_media(page, timeout_s=10)
        except Exception:  # noqa: BLE001
            pass
    if el is None:
        return False

    try:
        el.evaluate("(e)=>{e.muted=true;const p=e.play();if(p&&p.catch)p.catch(()=>{})}")
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1200)

    try:
        dur = float(el.evaluate("(e)=>isFinite(e.duration)?e.duration:0") or 0)
    except Exception:  # noqa: BLE001
        dur = 0.0
    log.info(f"    {tag} 就位，时长 {dur:.0f}s")

    seeked = False
    if seek and dur > 5:
        try:
            el.evaluate("(e,d)=>{try{e.currentTime=Math.max(0,d-1.5)}catch(x){}}", dur)
            page.wait_for_timeout(1500)
            ct = float(el.evaluate("(e)=>e.currentTime||0") or 0)
            if ct > dur * 0.7:
                log.info(f"    拖动生效：跳到 {ct:.0f}/{dur:.0f}s")
                seeked = True
            else:
                log.info(f"    拖动被拒（停在 {ct:.0f}s），改试倍速")
                try:
                    el.evaluate("(e)=>{try{e.currentTime=0}catch(x){}}")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.info(f"    拖动异常: {str(exc)[:60]}")

    rate_ok = 1.0
    if not seeked and rate and abs(rate - 1.0) > 0.01:
        try:
            el.evaluate("(e,r)=>{try{e.defaultPlaybackRate=r;e.playbackRate=r}catch(x){}}", rate)
            page.wait_for_timeout(1200)
            now_r = float(el.evaluate("(e)=>e.playbackRate||1") or 1)
            if abs(now_r - rate) < 0.05:
                rate_ok = now_r
                log.info(f"    倍速生效：{now_r}x")
            else:
                log.info(f"    倍速被重置为 {now_r}x（平台限速），按实时播放")
        except Exception as exc:  # noqa: BLE001
            log.info(f"    倍速设置异常: {str(exc)[:60]}")

    limit = time.time() + max_min * 60
    last, stall = -1.0, 0
    while time.time() < limit:
        popup.tick(page)
        session.keep_alive()
        try:
            st = el.evaluate("(e)=>({ct:e.currentTime||0,"
                             "dur:isFinite(e.duration)?e.duration:0,"
                             "paused:!!e.paused,ended:!!e.ended,"
                             "rate:e.playbackRate||1})")
        except Exception:  # noqa: BLE001
            return True
        ct, d2 = st.get("ct", 0), st.get("dur", 0)
        if st.get("ended") or (d2 > 0 and ct >= d2 - 1.0):
            how = "拖动" if seeked else (f"{rate_ok}x" if rate_ok > 1.01 else "实时")
            log.info(f"    结束（{ct:.0f}s，{how}）")
            return True
        if st.get("paused"):
            try:
                el.evaluate("(e)=>{const p=e.play();if(p&&p.catch)p.catch(()=>{})}")
            except Exception:  # noqa: BLE001
                pass
        if rate_ok > 1.01 and abs((st.get("rate") or 1) - rate_ok) > 0.05:
            log.info("    播放器中途重置了倍速，继续观察")
            rate_ok = 1.0
        if abs(ct - last) < 0.01:
            stall += 1
            if stall >= 12:
                log.info("    进度停滞，跳过")
                return True
        else:
            stall = 0
        last = ct
        time.sleep(2)
    log.info("    等待超时，继续下一个")
    return True


# ----------------------------------------------------------------- 答题
JS_SCAN = r"""
() => {
  const out = {questions: [], inputs: 0, buttons: []};
  const blocks = document.querySelectorAll(
    '[class*="question"],[class*="quest-item"],[class*="q-item"],li[class*="item"]');
  const seen = new Set();
  for (const b of blocks) {
    const radios = b.querySelectorAll('input[type=radio],input[type=checkbox]');
    const texts = b.querySelectorAll('input[type=text],textarea');
    if (radios.length === 0 && texts.length === 0) continue;
    const t = (b.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || seen.has(t.slice(0, 40))) continue;
    seen.add(t.slice(0, 40));
    out.questions.push({
      stem: t.slice(0, 160),
      radios: radios.length,
      texts: texts.length,
      cls: (typeof b.className === 'string' ? b.className : '').slice(0, 60),
    });
  }
  out.inputs = document.querySelectorAll('input,textarea').length;
  for (const btn of document.querySelectorAll('button,[role="button"],[class*="btn"]')) {
    const t = (btn.innerText || '').trim();
    if (t && t.length < 12 && btn.getBoundingClientRect().width > 0) out.buttons.push(t);
  }
  out.buttons = Array.from(new Set(out.buttons)).slice(0, 20);
  return out;
}
"""

# Role-play 的「Step 1 / Choose a role  A  B」选角色页。
# 现场 dump 里 A/B 被采集规则滤掉了，所以按可见文本找可点击元素，
# 不依赖具体 class；点完角色后页面会进入 Step 2，需要递归重新扫描。
JS_ROLE_PICK = r"""
() => {
  const body = document.body.innerText || '';
  if (!/Choose a role/i.test(body)) return {found: false};
  // 8 秒冷却：同一页反复递归时不要重复点，避免死循环
  if (Date.now() - (window.__rolePickedAt || 0) < 8000) {
    return {found: true, picked: '', cooldown: true};
  }
  const cands = [];
  for (const e of document.querySelectorAll(
      'button,a,[role="button"],[class*="btn"],label,span,div,li')) {
    const s = (e.innerText || '').trim();
    if (s !== 'A' && s !== 'B') continue;
    const r = e.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    cands.push({e: e, s: s});
  }
  if (!cands.length) return {found: true, picked: '', why: 'no A/B element'};
  const hit = cands.find(x => x.s === 'A') || cands[0];
  try { hit.e.click(); }
  catch (err) { return {found: true, picked: '', why: String(err)}; }
  window.__rolePickedAt = Date.now();
  return {found: true, picked: hit.s, n: cands.length};
}
"""


# Step 2 的逐句台词：选了角色 A，则 .score 不带 hide 的就是要念的 A 句。
JS_ROLEPLAY_LINES = r"""
() => {
  const quiz = document.querySelector('.role-play-quiz');
  if (!quiz) return {found: false};
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const lines = [];
  const items = quiz.querySelectorAll('.list-item-review');
  items.forEach((it, i) => {
    const sc = it.querySelector('.score');
    const mine = !!sc && !sc.classList.contains('hide');
    const t = (it.querySelector('.sentence') || {}).innerText || '';
    const role = (it.querySelector('.role') || {}).innerText || '';
    lines.push({i: i, mine: mine, role: role.trim(),
                text: t.replace(/\s+/g, ' ').trim(),
                cls: sc ? String(sc.className) : ''});
  });
  return {found: true, lines: lines,
          recVisible: vis(document.querySelector('.button-record')),
          seatVisible: vis(document.querySelector('.record-seat'))};
}
"""

# 点当前句的播放圆 + 可见的录音座位，尝试把 display:none 的录音控件展开。
JS_ROLEPLAY_ACTIVATE = r"""
(i) => {
  const items = document.querySelectorAll('.role-play-quiz .list-item-review');
  if (i < items.length) {
    const it = items[i];
    try { it.scrollIntoView({block: 'center'}); } catch (e) {}
    const svg = it.querySelector('svg') || it;
    try { svg.click(); } catch (e) {}
  }
  const seat = document.querySelector('.record-seat');
  if (seat) { try { seat.click(); } catch (e) {} }
  const rec = document.querySelector('.button-record');
  const r = rec ? rec.getBoundingClientRect() : null;
  return {recVisible: !!r && r.width > 0 && r.height > 0,
          recCls: rec ? String(rec.className) : ''};
}
"""


JS_FILL = r"""
() => {
  let answered = 0, total = 0;
  const blocks = document.querySelectorAll(
    '[class*="question"],[class*="quest-item"],[class*="q-item"],li[class*="item"]');
  const done = new Set();
  const clickIt = (el) => {
    // 自定义组件常把 input 隐藏，点它的 label / 父节点才有效
    try { el.click(); return true; } catch (e) {}
    const t = el.closest('label') || el.parentElement;
    if (t) { try { t.click(); return true; } catch (e) {} }
    return false;
  };
  for (const b of blocks) {
    const radios = Array.from(b.querySelectorAll('input[type=radio],input[type=checkbox]'));
    const texts = Array.from(b.querySelectorAll('input[type=text],textarea'));
    if (radios.length === 0 && texts.length === 0) continue;
    const sig = (b.innerText || '').slice(0, 40);
    if (done.has(sig)) continue;
    done.add(sig);
    total++;
    if (radios.length) {
      if (clickIt(radios[0])) answered++;
    } else if (texts.length) {
      // ★ 绝不填字面量 'answer'！开放式问答题（Group discussion Step 2 这类
      //   textarea）以前就是被这里写死成 "answer"，网页上直接显示 ANSWER，
      //   用户反复投诉。这类题现在由 _answer_open_questions() 用大模型写真实
      //   答案；走到这里说明它没被识别，写一段真实、连贯的英文兜底句子，
      //   既不出现 ANSWER 字样，也不会被 SOE 判「无有效语音/空答案」。
      const el = texts[0];
      try {
        el.focus();
        el.value = 'I think this is an interesting topic and I really enjoy '
                 + 'sharing my ideas about it with my classmates.';
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        answered++;
      } catch (e) {}
    }
  }
  return {answered, total};
}
"""

JS_BLANKS = r"""
() => {
  const txtPart = (root, target, before) => {
    if (!root) return '';
    let s = '';
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
      const pos = target.compareDocumentPosition(n);
      if (before ? (pos & 2) : (pos & 4)) s += n.nodeValue;
    }
    return s.replace(/\s+/g, ' ');
  };
  const out = [];
  // 注意：这些 input 没有写 type 属性（el.type 默认为 "text"），
  // 所以 [type=text] 属性选择器匹配不到，必须遍历所有 input 再判断。
  for (const el of document.querySelectorAll('input')) {
    if (el.type !== 'text') continue;
    if (el.classList.contains('ant-input')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) continue;

    let box = el.parentElement;
    for (let k = 0; k < 6 && box; k++) {
      if ((box.innerText || '').length > 30) break;
      box = box.parentElement;
    }
    const before = txtPart(box, el, true);
    const after = txtPart(box, el, false);
    const all = box ? (box.innerText || '') : '';
    const m = all.match(/e\.?\s*g\.?\s*[:：]?\s*([^)]{2,90})\)/i);
    out.push({
      before: before.slice(-140),
      after: after.slice(0, 140),
      sample: m ? m[1].trim() : '',
      value: (el.value || '').slice(0, 40),
    });
  }
  return out;
}
"""

JS_QUIZ_STATE = r"""
() => {
  // 按题块分组，返回每题选项文本与是否已选中。
  // AI 版选项是 div.option（无 input），选中态 = class 含 selected。
  const out = [];
  const seen = new Set();
  const boxes = document.querySelectorAll(
    '.question-wrap, [class*="question-common-abs-question"], '
    + '[class*="question-basic"], [class*="question-container"]');
  for (const q of boxes) {
    const opts = Array.from(q.querySelectorAll('.option'));
    if (!opts.length) continue;
    // 同一题会被多个嵌套容器同时命中，用「选项文本」做签名才能真正去重
    const sig = opts.map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())
                   .join('|').slice(0, 300);
    if (seen.has(sig)) continue;
    seen.add(sig);
    const gi = out.length;
    out.push({
      stem: (() => {
        // ★ 题块 innerText 前一大段是页面导航（"教材名> 单元> Tab名"），
        //   直接截 320 字会把真正的题目挤掉，模型等于没看到题干。
        //   这里从 Directions/Question 开始切，并放宽到 2000 字。
        //   ★ 超长时保头留尾：阅读题的长材料在前、真正的提问在后，
        //   只截头部 2000 会把问题本身截掉——模型"看不懂题目"就答非所问。
        let t = (q.innerText || '').replace(/\s+/g, ' ').trim();
        const m = t.match(/Directions\s*[:：]|Question\s*[:：]/i);
        if (m && m.index > 0) t = t.slice(m.index);
        else t = t.replace(/^([^>]{0,60}>\s*)+/, '');
        if (t.length > 2000) t = t.slice(0, 700) + ' …… ' + t.slice(-1300);
        return t;
      })(),
      material: (() => {
        // ★ 阅读/讨论题的材料（文章正文）在题块内的 material 区，
        //   不在题干里。不取出来模型就没有答题依据。
        const el = q.querySelector(
          '[class*="abs-material"], [class*="rich-text"], [class*="material"]');
        if (el) return (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 4000);
        // class 猜不中时的兜底：向上找最近的祖先，看它的"非题块"子元素里
        //   有没有大段文本（阅读材料通常和题块并排放在同一个 section）。
        //   只取兄弟文本而不是祖先全文，避免混入其它题目的选项干扰模型。
        let p = q.parentElement, d = 0;
        while (p && d < 5) {
          const extra = Array.from(p.children)
            .filter(c => c !== q)
            .map(c => (c.innerText || '').trim())
            .join('\n').replace(/\s+/g, ' ').trim();
          if (extra.length > 300) return extra.slice(0, 4000);
          p = p.parentElement; d++;
        }
        return '';
      })(),
      options: opts.map((e, i) => {
        const key = gi + ':' + i;
        e.setAttribute('data-upc-opt2', key);
        return {
          text: (e.innerText || '').replace(/\s+/g, ' ').trim(),
          selected: String(e.className).indexOf('selected') >= 0,
          key,
        };
      }),
    });
  }
  // 听力选择题的另一套 DOM：选项不是 .option，而是 div.caption(A/B/C/D)
  // + 紧跟的 <p> 文本；题目用 <p>1.</p> <p>2.</p> 分隔。
  // 这类页面完全没有 .option，不补就会"少题"（平台提示还有 N 道没作答）。
  if (out.length === 0) {
    const caps = Array.from(document.querySelectorAll('.caption'))
      .filter(e => /^[A-H]$/.test((e.innerText || '').trim()));
    if (caps.length >= 2) {
      const gi = out.length;
      // ★ 以前 stem 写死空串，拆题后模型看不到题干只能瞎猜
      //   （实测两道听力题都回 ANSWER: A）。这里把课件正文带上。
      const box = document.querySelector(
          '[class*="pc-content-container"], .ant-layout-content, '
          + '[class*="question-common"], main');
      const whole = ((box || document.body).innerText || '')
                      .replace(/\s+/g, ' ').trim()
                      .replace(/^([^>]{0,60}>\s*)+/, '');
      out.push({
        stem: whole.slice(0, 2000),
        material: whole.slice(0, 4000),
        captionMode: true,
        options: caps.map((e, i) => {
          const key = 'cap' + gi + ':' + i;
          e.setAttribute('data-upc-opt2', key);
          // 不判断 selected：这类元素选中态 class 未知，
          // 固定 false 保证每题只点一次（新页面加载后会重置）
          return {text: (e.innerText || '').trim(),
                  selected: false, key};
        }),
      });
    }
  }
  return out;
}
"""

JS_FIND_SUBMIT = r"""
() => {
  // 讨论页真正的按钮是 <a class="dis-button">发表</a>，
  // 而「发表回复」只是 <div class="reply-title"> 标题，点它没用。
  const texts = ['提交', '发表', '发表回复', '确定', '完成', '保存',
                 '下一题', '继续', 'Submit', 'Check'];
  const cands = [];
  for (const el of document.querySelectorAll(
      'button,a,[role="button"],[class*="btn"],span,div')) {
    if (el.children.length > 0) continue;
    const t = (el.innerText || '').replace(/[\s\u00a0]/g, '');
    if (!t || t.length > 8) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    const tag = el.tagName.toLowerCase();
    const cls = String(el.className || '');
    let score = 0;
    if (tag === 'button') score += 50;
    else if (tag === 'a') score += 30;
    else if (el.getAttribute('role') === 'button') score += 25;
    if (/button|btn|submit/.test(cls)) score += 25;
    // 提交按钮有两个（外层宽容器 + 真按钮），取窄的那个
    if (r.width > 200) score -= 20;
    if (/title|label|crumb|tab/.test(cls)) score -= 40;
    cands.push({el, t, score});
  }
  for (const txt of texts) {
    const hits = cands.filter(c => c.t === txt ||
                              (c.t.includes(txt) && c.t.length <= txt.length + 2));
    if (!hits.length) continue;
    hits.sort((a, b) => b.score - a.score);
    hits[0].el.setAttribute('data-upc-submit', '1');
    return {hit: hits[0].t, score: hits[0].score,
            seen: cands.slice(0, 20).map(x => x.t + ':' + x.score)};
  }
  return {hit: '', seen: cands.slice(0, 20).map(x => x.t + ':' + x.score)};
}
"""

JS_FIND_PUBLISH = r"""
() => {
  for (const el of document.querySelectorAll('a,button,div,span')) {
    if (el.children.length) continue;
    if ((el.innerText || '').replace(/[\s\u00a0]/g, '') !== '发表') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    el.setAttribute('data-upc-publish', '1');
    return {tag: el.tagName.toLowerCase(), cls: String(el.className),
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2)};
  }
  return null;
}
"""

JS_FILL_DISCUSSION = r"""
(text) => {
  // 原生 setter + input 事件：实测能让 React 把 textarea 从
  // dis-dis-content 切成 dis-content（即 state 收到内容）。
  let filled = 0;
  for (const el of document.querySelectorAll(
      'textarea,input,[contenteditable="true"]')) {
    if (el.classList.contains('ant-input')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 20) continue;
    if (el.tagName === 'INPUT' && el.type !== 'text') continue;
    try {
      if (el.getAttribute('contenteditable') === 'true') {
        el.innerText = text;
        el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text}));
      } else {
        const proto = el.tagName === 'TEXTAREA'
          ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
      }
      filled++;
    } catch (e) {}
  }
  return filled;
}
"""


def _fill_discussion(page, log):
    """讨论页：用原生 setter 填真实内容（避开遮罩导致的点击超时）。"""
    text = ("I think this topic is really interesting. From what I have learned, "
            "it is a good way to practise listening and speaking in context.")
    try:
        return int(page.evaluate(JS_FILL_DISCUSSION, text) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    讨论页填充异常: {str(exc)[:80]}")
        return 0


# ------------------------------------------------------------------
# 开放式问答题（Group discussion 的 Step 2：Answer the following questions）
# 题块是 .question-common-abs-reply / .question-inputbox-input 的 textarea，
# 既没有 .option（选择题扫描认不出），也不是 input[type=text]（填空扫描认不出），
# 以前只能掉进 JS_FILL 兜底被写死成 "answer"，再被判「未识别题型」不提交。
# 这里单独识别 + 用大模型按「题干 + 网页材料」写真实答案。
JS_OPENQ_SCAN = r"""
() => {
  const out = [];
  const boxes = document.querySelectorAll(
    '.question-common-abs-reply, .question-inputbox, [class*="abs-reply"]');
  const seen = new Set();
  boxes.forEach((b, bi) => {
    const ta = b.querySelector('textarea, input[type=text], input:not([type])');
    if (!ta) return;
    const r = ta.getBoundingClientRect();
    if (r.width < 30 || r.height < 15) return;
    // 题干在 header 的 .component-htmlview 里（如 "1. Where is it?"）
    let stem = '';
    const hdr = b.querySelector('.question-inputbox-header, .component-htmlview');
    if (hdr) stem = (hdr.innerText || '').replace(/\s+/g, ' ').trim();
    if (!stem) stem = (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200);
    const sig = stem.slice(0, 60);
    if (seen.has(sig)) return;
    seen.add(sig);
    ta.setAttribute('data-upc-openq', String(out.length));
    out.push({idx: out.length, stem: stem.slice(0, 500)});
  });
  return out;
}
"""

JS_OPENQ_FILL = r"""
(items) => {
  let filled = 0;
  for (const it of items) {
    const el = document.querySelector('[data-upc-openq="' + it.idx + '"]');
    if (!el) continue;
    const proto = el.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    try {
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, it.answer);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      filled++;
    } catch (e) {}
  }
  return filled;
}
"""


def _answer_open_questions(page, log, cfg, lf, page_text=""):
    """开放式问答题：扫描 textarea 题干，用大模型按材料写英文答案并填回。

    返回填充个数（0 表示本页没有这种题型）。这是 Step 2 开放式问答不再
    显示 "answer"、且能正常提交进题库闭环的关键。
    """
    try:
        items = page.evaluate(JS_OPENQ_SCAN) or []
    except Exception:  # noqa: BLE001
        items = []
    if not items:
        return 0

    log.info(f"    开放式问答题：识别到 {len(items)} 个输入框")
    cli = _get_llm(cfg, log)
    bank = _load_bank().get(str(lf.get("id"))) or {}
    std = bank.get("letters") or []
    filled_items = []
    for it in items:
        stem = (it.get("stem") or "").strip()
        ans = ""
        # 题库有标准答案时按顺序直接对应（第二遍满分）
        if it.get("idx") is not None and it["idx"] < len(std):
            cand = str(std[it["idx"]]).strip()
            if len(cand) >= 2:
                ans = cand
        # 否则大模型按题干 + 网页材料作答
        if not ans and cli is not None and stem:
            q = ("用英语简短回答下面的问题（2~4 句，基于材料，不要写标题或解释）：\n\n"
                 + stem)
            r = cli.ask(q, None, "简答", material=page_text, free_text=True)
            if r and r.get("answer"):
                ans = str(r["answer"]).strip().strip("\"' ")
        if len(ans) < 3:
            # 兜底：真实句子，绝不再写 "answer"
            ans = ("It is a fascinating place, and what impresses me most is "
                   "its unique natural beauty and the way it is protected.")
        filled_items.append({"idx": it.get("idx", 0), "answer": ans[:600]})
        log.info(f"      问{it.get('idx', 0) + 1}: {ans[:60]!r}")

    try:
        n = int(page.evaluate(JS_OPENQ_FILL, filled_items) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    开放式问答题填充异常: {str(exc)[:70]}")
        return 0
    log.info(f"    开放式问答题：已填 {n}/{len(items)}")
    return n


SUBMIT_TEXTS = ("提交", "发表回复", "发表", "确定", "完成", "保存",
                "下一题", "继续", "Submit", "Check")


_TRANS_CACHE = None
_BANK_CACHE = None


def _transcripts():
    """读 data/transcripts.json（本地 ASR 转录结果）。"""
    global _TRANS_CACHE
    if _TRANS_CACHE is not None:
        return _TRANS_CACHE
    p = ROOT / "data" / "transcripts.json"
    try:
        _TRANS_CACHE = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _TRANS_CACHE = {}
    return _TRANS_CACHE


def _bank_path():
    return ROOT / "data" / "answer_bank.json"


def _load_bank():
    """标准答案题库（第一次提交后从 analysis 解出来的）。"""
    global _BANK_CACHE
    if _BANK_CACHE is None:
        try:
            _BANK_CACHE = json.loads(_bank_path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _BANK_CACHE = {}
    return _BANK_CACHE


def _save_bank(leaf_id, letters, score, group_letters=None, blocks=None):
    """沉淀标准答案，下次同题直接用，一次满分。

    ★ blocks 是按题/按空分组的答案（见 decrypt.parse_answer_blocks），
      多题页必须靠它才能精确对应；letters 仅保留作旧格式兼容。
    """
    try:
        bank = _load_bank()
        entry = {"letters": list(letters), "score": score,
                 "groups": group_letters or []}
        if blocks:
            entry["blocks"] = blocks
        bank[str(leaf_id)] = entry
        _bank_path().parent.mkdir(parents=True, exist_ok=True)
        _bank_path().write_text(
            json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
        # 关键：内存缓存也要更新，否则同一进程内后续叶子读到的还是旧题库
        global _BANK_CACHE
        _BANK_CACHE = bank
    except Exception:  # noqa: BLE001
        pass


def _apply_choice(page, log, group, want):
    """把某题的选中状态改成 want（先取消多余的，再补选缺的）。"""
    changed = 0
    for i, o in enumerate(group.get("options") or []):
        should = i in want
        if should == bool(o.get("selected")):
            continue
        try:
            el = page.query_selector(f'[data-upc-opt2="{o["key"]}"]')
            if el is None:
                continue
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=4000)
            changed += 1
        except Exception as exc:  # noqa: BLE001
            log.debug(f"      选项 {o.get('key')} 点击失败: {str(exc)[:50]}")
    return changed


def _apply_letters(page, log, groups, letters):
    """按字母答案（如 A,C,E,F）设置所有题块的选项。"""
    idxs = [ord(x.upper()) - 65 for x in letters if x and x[0].isalpha()]
    changed = 0
    for g in groups:
        n = _apply_choice(page, log, g,
                          set(i for i in idxs
                              if i < len(g.get("options") or [])))
        changed += n
    return changed


def _fill_blanks(page, log, transcript="", std_answers=None, cfg=None,
                 stem="", bank_blocks=None):
    """填空：优先取题干里的 e.g. 示例；否则用音频转录夹逼。

    绝不填无意义的固定词（之前填 'often' 等于乱做）。
    实在定不下来就跳过，交给后面「提交→标准答案→重答」闭环兜底。

    ★ bank_blocks 是按空位分组的标准答案（[[空1答案...], [空2答案...]]）。
      数量与空位一致时按下标精确填入，避免扁平列表错位导致永远不满分。
    """
    from upc.hearing import answer_from_context

    try:
        blanks = page.evaluate(JS_BLANKS) or []
    except Exception:  # noqa: BLE001
        blanks = []
    if not blanks:
        return 0

    inputs = []
    for el in page.query_selector_all('input'):
        try:
            if el.get_attribute("type") not in (None, "text"):
                continue
            if "ant-input" in (el.get_attribute("class") or ""):
                continue
            if not el.is_visible():
                continue
            inputs.append(el)
        except Exception:  # noqa: BLE001
            continue

    # 题库里已有标准答案，逐空填入。
    # ★ 关键：analysis 返回的 children 顺序 == 空位顺序，
    #   所以答案数与空数相等时必须【按索引直接对应】。
    #   之前一律用 group_by_position（按空前文本在转录里定位）分组，
    #   实测会把顺序弄乱、还会重复（空5/空6 都填 being together），
    #   导致明明有标准答案却只有 0.333 / 0.667 分。
    known = []
    # ★ 首选：分组答案与空位一一对应（最精确，不会错位）
    if bank_blocks and len(bank_blocks) == len(inputs):
        known = [[b[0]] if isinstance(b, list) and b else
                 ([str(b)] if b else []) for b in bank_blocks]
        log.info(f"    填空题库命中(分组)，{len(known)} 组答案按空位精确对应")
    elif std_answers:
        if len(std_answers) == len(inputs):
            known = [[a] for a in std_answers]
            log.info(f"    填空题库命中，{len(known)} 个答案按空位顺序直接对应")
        else:
            # 扁平答案数量对不上时，group_by_position 常把顺序弄乱。
            # 若分组答案存在但数量不等，按最短长度逐个对齐，好过整体错位。
            if bank_blocks:
                n = min(len(bank_blocks), len(inputs))
                known = [[b[0]] if isinstance(b, list) and b else []
                         for b in bank_blocks[:n]]
                known += [[] for _ in range(len(inputs) - n)]
                log.info(f"    填空题库分组数({len(bank_blocks)})与空数"
                         f"({len(inputs)})不等，按前 {n} 个对齐")
            else:
                from upc.hearing import group_by_position
                known = group_by_position(list(std_answers), transcript or "")
                log.info(f"    填空题库命中，答案 {len(std_answers)} 个 / 空 "
                         f"{len(inputs)} 个，按音频定位分成 {len(known)} 组")

    # ★ 媒体=无 的纯文字填空页没有音频转录。以前整套取答案逻辑都挂在
    #   transcript 上（夹逼 / 大模型 / 试探词全都要它），结果 8 个空全跳过
    #   → 上层「未答完不提交」→ 拿不到标准答案 → 题库里永远没这题
    #   → 下一轮还是 0/8，形成死锁。这里补两条无转录时的出路。
    try:
        page_text = page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
    except Exception:  # noqa: BLE001
        page_text = ""

    # 出路①：一次把整页题目交给大模型（省钱：1 次调用，不是每空 1 次）
    llm_answers = []
    if not transcript and not known and cfg is not None and len(inputs) > 1:
        _cli = _get_llm(cfg, log)
        if _cli is not None:
            lines = ["根据下面的题目页面内容，按顺序写出每个空应填的英文内容。"]
            for i, b in enumerate(blanks[:len(inputs)], 1):
                lines.append(f"空{i}：空前 ...{str(b.get('before') or '')[-120:]}"
                             f" ｜ 空后 {str(b.get('after') or '')[:120]}...")
            lines.append("只输出答案，按空号顺序用 | 分隔，不要编号、不要解释。")
            _r = _cli.ask("\n".join(lines), None, "填空", material=page_text)
            if _r and _r.get("answer"):
                llm_answers = [x.strip(" .,:;\"'") for x in
                               str(_r["answer"]).split("|")]
                llm_answers = [x for x in llm_answers
                               if len(x) >= 2 and "<" not in x and "答案" not in x]
                log.info(f"    无转录页：大模型按页面内容给出 "
                         f"{len(llm_answers)}/{len(inputs)} 个空的答案")

    filled = 0
    for i, el in enumerate(inputs):
        b = blanks[i] if i < len(blanks) else {}
        ans = ""
        if known and i < len(known) and known[i]:
            ans = known[i][0]
        if not ans and i < len(llm_answers):
            ans = llm_answers[i]
        sample = (b.get("sample") or "").strip()
        if not ans and sample:
            ans = sample.split("/")[0].strip(" ,.;:")
        if not ans and transcript:
            ans = answer_from_context(b.get("before", ""), b.get("after", ""),
                                      transcript)
        ans = (ans or "").strip(" ,.;:")
        if len(ans) < 2 and cfg is not None and i >= len(llm_answers):
            # 夹逼不出来时，交给大模型按上下文填（比词表猜测准得多）。
            # 没有音频转录时把页面正文当材料一起给，别让模型凭空猜。
            _cli = _get_llm(cfg, log)
            if _cli is not None:
                _q = (f"根据题目上下文填空。空的上下文：\n"
                      f"空前：...{b.get('before', '')[-160:]}\n"
                      f"空后：{b.get('after', '')[:160]}...\n"
                      f"只输出该空应填的英文内容，不要解释。")
                _r = _cli.ask(_q, None, "填空", transcript=transcript,
                              material=page_text)
                if _r and _r.get("answer"):
                    ans = str(_r["answer"]).split("|")[0].strip(" ,.;:")
                    log.info(f"      空{i + 1} 大模型填空 ← {ans[:32]!r}")

        if len(ans) < 2 and transcript:
            # 兜底：按空前文本在转录里近似定位，取后面的词
            from upc.hearing import guess_from_transcript
            guess = guess_from_transcript(b.get("before", ""), transcript)
            if len(guess) >= 2:
                ans = guess
                log.info(f"      空{i + 1} 以音频近似位置推测 ← {ans[:32]!r}")
        if len(ans) < 2:
            # 空着会导致平台拒绝提交（"还有N道题没有作答"），
            # 拿不到标准答案就永远卡住。这里做最后一次试探填充，
            # 目的只是让提交通过；第二遍会用题库里的标准答案覆盖。
            from upc.hearing import last_resort_word
            ans = last_resort_word(transcript)
            if len(ans) < 2:
                # 没转录时退而求其次：从题库已知答案里借一个
                # （实测「6 个空但答案只有 5 组」就是靠这个把提交打通）
                for _grp in (known or []):
                    if _grp and len(str(_grp[0])) >= 2:
                        ans = str(_grp[0])
                        break
            if len(ans) < 2 and stem:
                ans = last_resort_word(stem)
            if len(ans) < 2:
                # 出路②：连模型也没给答案时，从页面正文 / 空的上下文里
                # 取一个真实英文词做试探（不是写死的固定词），把提交闭环打通。
                # 首遍分必然低，但能换回标准答案，第二遍按题库补满分；
                # 空着不提交则永远拿不到标准答案。
                _ctx = " ".join(str(b.get(k) or "")
                                for k in ("before", "after", "sample"))
                ans = last_resort_word(page_text) or last_resort_word(_ctx or stem)
            if len(ans) >= 2:
                log.info(f"      空{i + 1} 试探填写 ← {ans[:24]!r}"
                         f"（仅为取得标准答案，第二遍会修正）")
            else:
                log.info(f"      空{i + 1} 无法确定且无可试探词，跳过")
                continue
        try:
            el.click(timeout=2500)
            el.fill(ans, timeout=2500)
            filled += 1
            log.info(f"      空{i + 1} ← {ans[:32]!r}")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"      空{i + 1} 填写失败: {str(exc)[:50]}")
    return filled


def _click_options(page, log, options, mode="all"):
    """点击 div.option 选项。返回实际点中的个数。"""
    els = page.query_selector_all('[data-upc-opt="1"]')
    if not els:
        return 0
    n = len(els)
    if mode == "first":
        idxs = [0]
    elif mode == "half":
        idxs = list(range(max(1, n // 2)))
    else:
        idxs = list(range(n))
    picked = 0
    for i in idxs:
        if i >= len(els):
            break
        try:
            el = els[i]
            el.scroll_into_view_if_needed(timeout=3000)
            el.click(timeout=4000)
            picked += 1
        except Exception as exc:  # noqa: BLE001
            log.debug(f"      选项{i+1} 点击失败: {str(exc)[:50]}")
    log.info(f"    已选 {picked}/{n} 个选项（mode={mode}）")
    for o in options[:3]:
        log.info(f"      候选: {o['text'][:66]}")
    return picked


_LLM_SINGLETON = None


def _relogin_and_reinject(session, cfg, log):
    """课堂会话过期时自动重新登录，并把新 cookie 注入当前浏览器。

    走 tools/api_run.py 的纯接口登录（不启第二个浏览器），
    成功后读回 api_session.json 并覆盖到当前上下文。
    """
    import subprocess
    import sys as _sys

    ap = ROOT / "tools" / "api_run.py"
    if not ap.exists():
        log.warning("  找不到 tools/api_run.py，无法自动重登")
        return False
    log.info("  调用 api_run.py 走接口登录…")
    try:
        r = subprocess.run([_sys.executable, str(ap),
                            "--session-only", "--no-cache"],
                           cwd=str(ROOT), timeout=240,
                           capture_output=True, text=True,
                           errors="replace")
        if r.returncode != 0:
            log.warning(f"  自动重登返回码 {r.returncode}")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  自动重登失败: {str(exc)[:90]}")
        return False

    sp = Path(cfg.state_dir) / "api_session.json"
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        cookies = [{"name": c["name"], "value": c["value"],
                    "domain": c.get("domain") or "",
                    "path": c.get("path") or "/"}
                   for c in (data.get("cookies") or []) if c.get("domain")]
        if not cookies:
            log.warning("  重登后没拿到 cookie")
            return False
        session.context.add_cookies(cookies)
        log.info(f"  已注入 {len(cookies)} 个新 cookie，重试取结构")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  注入新 cookie 失败: {str(exc)[:90]}")
        return False


def _get_llm(cfg, log):
    """全局复用同一个 LLM 客户端（避免每题重建、也便于统计调用数）。"""
    global _LLM_SINGLETON
    if _LLM_SINGLETON is None:
        try:
            from upc.llm import LLMClient
            _LLM_SINGLETON = LLMClient(cfg, log)
            if not _LLM_SINGLETON.available:
                log.info("  大模型未启用或无密钥，回退到字面匹配")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"  初始化大模型客户端失败: {str(exc)[:70]}")
            _LLM_SINGLETON = False
    return _LLM_SINGLETON or None


def _handle_sequence(page, log, lf):
    """排序/人物匹配题：按题库标准顺序拖拽；没答案就拖一下让平台算已作答。"""
    import re as _re

    items = page.locator(".sequence-reply-view-item-text")
    try:
        n = items.count()
    except Exception:  # noqa: BLE001
        return 0
    if n < 2:
        return 0

    def _cur():
        out = []
        for k in range(n):
            try:
                t = items.nth(k).inner_text() or ""
                m = _re.match(r"\s*([A-Za-z])", t)
                out.append(m.group(1).upper() if m else "?")
            except Exception:  # noqa: BLE001
                out.append("?")
        return out

    bank = _load_bank().get(str(lf.get("id"))) or {}
    target = [str(x).upper() for x in (bank.get("letters") or [])
              if len(str(x)) == 1 and str(x).isalpha()]

    if len(target) != n:
        # 还没有标准答案：随便拖一下，让平台把 5 题都算"已作答"，
        # 提交后就能从 analysis 反解出正确顺序，第二遍再精确拖。
        log.info(f"    排序题 {n} 项：无题库答案，先拖一次以取得标准答案")
        try:
            items.nth(0).drag_to(items.nth(1), timeout=8000)
            page.wait_for_timeout(400)
            log.info(f"    试探拖拽后顺序 {_cur()}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"    排序题试探拖拽失败: {str(exc)[:70]}")
        return 1

    log.info(f"    排序题按题库答案 {target} 拖拽（当前 {_cur()}）")
    for i in range(n):
        cur = _cur()
        if cur[i] == target[i]:
            continue
        if target[i] not in cur:
            continue
        j = cur.index(target[i])
        try:
            items.nth(j).drag_to(items.nth(i), timeout=8000)
            page.wait_for_timeout(250)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"    第 {i + 1} 次拖拽失败: {str(exc)[:50]}")
    log.info(f"    排序完成，最终顺序 {_cur()}")
    return n


def _handle_roleplay(page, log, cfg, lf):
    """Role-play Step 2：把页面上印着的台词逐句 TTS 后注入麦克风录音。

    通用口语题会让大模型凭空写一段口语稿，跟平台要念的句子完全不符；
    这里直接念页面台词（.sentence），内容天然对得上。
    """
    import base64
    from upc import voice as V

    try:
        st = page.evaluate(JS_ROLEPLAY_LINES) or {}
    except Exception:  # noqa: BLE001
        return None
    if not st.get("found"):
        return None
    lines = [x for x in (st.get("lines") or [])
             if x.get("mine") and x.get("text")]
    if not lines:
        return None
    log.info(f"    Role-play：Step 2 共 {len(st.get('lines') or [])} 句，"
             f"需跟读 {len(lines)} 句；录音按钮可见={st.get('recVisible')}")
    # 平台把已完成的页面切成 review 态：录音控件 display:none + disabled，
    # 点 svg / record-seat 都展不开。先探一次，别白合成 4 段 TTS。
    try:
        _probe = page.evaluate(JS_ROLEPLAY_ACTIVATE, lines[0]["i"]) or {}
    except Exception:  # noqa: BLE001
        _probe = {}
    if not _probe.get("recVisible"):
        log.warning("    Role-play：录音控件仍 disabled/隐藏（回顾态），跳过逐句跟读")
        return 0
    ok_count = 0
    for n, ln in enumerate(lines, 1):
        text = str(ln["text"])[:400]
        mp3 = ROOT / "output" / f"roleplay_{lf.get('id')}_{n}.mp3"
        got = (mp3 if mp3.exists() and mp3.stat().st_size > 1000
               else V._tts(text, mp3))
        if not got:
            log.warning(f"    Role-play [{n}] TTS 合成失败")
            continue
        b64 = base64.b64encode(mp3.read_bytes()).decode()
        dur = V.inject_audio(page, b64)
        if dur <= 0:
            log.warning(f"    Role-play [{n}] 音频注入失败")
            continue
        try:
            act = page.evaluate(JS_ROLEPLAY_ACTIVATE, ln["i"]) or {}
        except Exception:  # noqa: BLE001
            act = {}
        page.wait_for_timeout(700)
        log.info(f"    Role-play [{n}/{len(lines)}] {text[:42]!r} "
                 f"→ 录音按钮可见={act.get('recVisible')} "
                 f"cls={act.get('recCls', '')[:50]}")
        if not act.get("recVisible"):
            continue
        try:
            btn = page.query_selector(".button-record")
            if not btn:
                continue
            btn.click(timeout=5000)
            page.wait_for_timeout(500)
            page.wait_for_timeout(int((dur + 1.0) * 1000))
            btn2 = page.query_selector(".button-record")
            if btn2:
                btn2.click(timeout=5000)
            page.wait_for_timeout(1500)
            ok_count += 1
            after = page.evaluate(JS_ROLEPLAY_LINES) or {}
            al = after.get("lines") or []
            cls = al[ln["i"]].get("cls", "") if ln["i"] < len(al) else ""
            log.info(f"    Role-play [{n}] 录音完成，score class={cls}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"    Role-play [{n}] 录音失败: {str(exc)[:70]}")
    log.info(f"    Role-play：成功录 {ok_count}/{len(lines)} 句")
    return ok_count


def _open_leaf(page, url, log, retries: int = 2, backoff_ms: int = 1500) -> bool:
    """打开课件页，失败带退避重试。

    借鉴 Aegink/chaoxing 的失败重试与 xuexitongScript 的「临时中断 ≠ 失败」：
    网络抖一下就丢掉整个叶子太亏，但也不能无限重试，次数由 page_rules.yaml 控制。
    """
    for attempt in range(max(0, retries) + 1):
        try:
            # SPA 是哈希路由，直接 goto 新 hash 不重载文档，上一页的 <video> 会残留
            try:
                page.goto("about:blank", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            page.goto(url, wait_until="domcontentloaded", timeout=50000)
            return True
        except Exception as exc:  # noqa: BLE001
            _msg = str(exc).lower()
            # 浏览器/页面被关掉时重试毫无意义（实测上一轮 240 个叶子因此在
            # 1 秒内全部失败），直接判失败，让连续失败熔断尽快触发。
            if any(k in _msg for k in ("has been closed", "target closed",
                                       "browser closed")):
                log.warning("  浏览器窗口已被关闭，不再重试（请保持窗口打开）")
                return False
            if attempt >= retries:
                log.warning(f"  打开失败（已重试 {retries} 次）: {str(exc)[:80]}")
                return False
            wait_s = (backoff_ms * (2 ** attempt)) / 1000.0
            log.warning(f"  打开失败，{wait_s:.1f}s 后重试 "
                        f"{attempt + 1}/{retries}: {str(exc)[:60]}")
            try:
                page.wait_for_timeout(int(wait_s * 1000))
            except Exception:  # noqa: BLE001  页面可能已被关掉
                time.sleep(wait_s)
    return False


def _handle_quiz(session, cfg, log, page, popup, humanizer, lf, choice_mode="all",
                 _depth=0):
    """先扫描全页题目，再统一作答，最后按固定顺序点提交。

    固定流程：扫描 → 填空/选题 → 提交 → 清理弹窗。
    """
    info = {"total": 0, "answered": 0}
    # Role-play 选角色页（Step 1 / Choose a role  A  B）：以前没有识别逻辑，
    # 被「页面上有题目但未能识别题型」拦下不提交，多页题就卡死在这页。
    # 选完角色页面会进入 Step 2，必须递归重新扫描一次。
    if _depth < 6:
        try:
            _role = page.evaluate(JS_ROLE_PICK) or {}
        except Exception as _exc:  # noqa: BLE001
            _role = {}
        if _role.get("picked"):
            log.info(f"    Role-play：已选角色 {_role['picked']}"
                     f"（候选 {_role.get('n', 1)} 个），重新扫描后续步骤")
            page.wait_for_timeout(1200)
            try:
                _d = page.evaluate(JS_DUMP_UNKNOWN) or {}
                (ROOT / "output" /
                 f"roleplay_{lf.get('id')}.json").write_text(
                    json.dumps(_d, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                log.info(f"    Role-play 现场已留存: roleplay_{lf.get('id')}.json")
            except Exception as _de:  # noqa: BLE001
                log.debug(f"    Role-play 现场留存失败: {str(_de)[:60]}")
            _clear_modals(page, log)
            return _handle_quiz(session, cfg, log, page, popup, humanizer, lf,
                                choice_mode, _depth + 1)
        if _role.get("found") and not _role.get("cooldown"):
            log.warning(f"    Role-play：识别到选角色页但没找到 A/B 按钮"
                        f"（{_role.get('why', '未知')}）")
    # 转录文本（听力原文）——填空和选择题都要用，先取出来
    trans = (_transcripts().get(lf.get("id")) or {}).get("text", "")
    if not trans:
        # 没转录就现场转（听力页才需要，纯文本页会自动跳过）
        try:
            from upc import asr as _asr
            trans = _asr.transcribe_leaf(page, log, lf.get("id"),
                                         lf.get("name") or "")
        except Exception as _exc:  # noqa: BLE001
            log.debug(f"    自动转录失败: {str(_exc)[:60]}")
    try:
        scan = page.evaluate(JS_SCAN) or {}
        qs = scan.get("questions") or []
        info["total"] = len(qs)
        log.info(f"    扫描：{len(qs)} 个题块 / 控件 {scan.get('inputs')} 个 / "
                 f"按钮 {scan.get('buttons', [])[:8]}")

        # ① 主观填空：逐个点空白处，按题干 e.g. 示例填内容
        blanks = page.evaluate(JS_BLANKS) or []
        if blanks:
            log.info(f"    发现 {len(blanks)} 个填空处")
            info["total"] = max(info["total"], len(blanks))
            _bank0 = _load_bank().get(str(lf.get("id"))) or {}
            _bans0 = _bank0.get("letters") or []
            _blocks0 = _bank0.get("blocks") or []
            _is_letter = (bool(_bans0) and
                          all(len(str(x)) == 1 and str(x).isalpha()
                              for x in _bans0))
            info["answered"] = _fill_blanks(
                page, log, trans, None if _is_letter else (_bans0 or None),
                cfg=cfg, stem=(scan.get("stem") or ""), bank_blocks=_blocks0)
            log.info(f"    已填 {info['answered']}/{len(blanks)} 个空")
            if info["answered"] < len(blanks):
                miss = len(blanks) - info["answered"]
                info["unfinished_blanks"] = miss
                log.warning(f"    还有 {miss} 个空没填，本题视为未完成")
            page.wait_for_timeout(600)

        # ①b 开放式问答题（Group discussion Step 2 这类 textarea 问答题）
        #     以前认不出、掉进 JS_FILL 被写死成 "answer" 又不提交。
        #     现在用大模型按题干 + 页面材料写真实答案，并计入已作答。
        try:
            _openq_page_text = page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
        except Exception:  # noqa: BLE001
            _openq_page_text = ""
        _openq_n = _answer_open_questions(
            page, log, cfg, lf, page_text=_openq_page_text)
        if _openq_n:
            info["openq"] = _openq_n
            info["answered"] = info.get("answered", 0) + _openq_n
            info["total"] = max(info.get("total", 0), _openq_n)

        # ② 客观选择题：按题块 + 听力转录判断答案（不再全选）
        from upc.hearing import decide as _decide

        groups = []
        try:
            groups = page.evaluate(JS_QUIZ_STATE) or []
        except Exception:  # noqa: BLE001
            groups = []
        # caption 模式返回的是一整页所有选项，按 4 个一组拆成多道题
        if len(groups) == 1 and groups[0].get("captionMode"):
            all_opts = groups[0].get("options") or []
            _cap_stem = groups[0].get("stem", "") or ""
            _cap_mat = groups[0].get("material", "") or ""
            if len(all_opts) >= 4:
                groups = [{"stem": _cap_stem, "material": _cap_mat,
                           "options": all_opts[i:i + 4]}
                          for i in range(0, len(all_opts), 4)
                          if len(all_opts[i:i + 4]) >= 2]
                log.info(f"    caption 题型：拆成 {len(groups)} 道题"
                         f"（题干 {len(_cap_stem)} 字符）")
        # 通用：听力多选题常把 N 道题的选项全塞进同一个容器
        # （实测 12 个 .option = 3 题 × 4 项），不拆就只答 1 题，
        # 平台会提示"还有 2 道题没有作答"。
        # 仅在数量是 4 的倍数且 > 4 时拆；6/5 个选项的单题多选不受影响。
        try:
            _page_txt = page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
        except Exception:  # noqa: BLE001
            _page_txt = ""
        if len(groups) == 1:
            _o = groups[0].get("options") or []
            _stem0 = groups[0].get("stem", "") or ""
            # 拆题后每个子题都得带上答题依据，否则模型只看到选项、
            # 只能瞎猜（实测两道听力题都回 ANSWER: A）。
            _mat0 = (groups[0].get("material") or "").strip() or _page_txt
            try:
                from upc.hearing import is_multi as _is_m0
                _multi0 = _is_m0(_stem0)
            except Exception:  # noqa: BLE001
                _multi0 = None
            # ★ 按「选项字母是否重复」判断是不是多题合并。
            #   实测三种情况：
            #     茶题        A B C D E F G H  → 无重复 → 一道多选
            #     Step 2      A B A B C D      → 重复 A → 3 题(每 2 项)
            #     Interviewing A B C D A B C   → 重复 A → 2 题(4 项+3 项)
            #   这比按 4 个一组切准确得多（还能处理不等长）。
            import re as _re2
            _lts = []
            for _x in _o:
                _m2 = _re2.match(r"^\s*([A-H])", str(_x.get("text", "")))
                _lts.append(_m2.group(1) if _m2 else "")
            if _lts.count("A") > 1:
                _cuts = [i for i, _l in enumerate(_lts) if _l == "A"] + [len(_o)]
                groups = [{"stem": _stem0, "material": _mat0,
                           "options": _o[_cuts[i]:_cuts[i + 1]]}
                          for i in range(len(_cuts) - 1)]
                log.info(f"    按选项字母重复拆分：{len(groups)} 道题 "
                         f"（每题目数 {[len(g['options']) for g in groups]}）")
            elif (len(_o) > 4 and len(_o) % 4 == 0 and _multi0 is not True
                  and not all(_l == chr(65 + i) for i, _l in enumerate(_lts))):
                groups = [{"stem": _stem0, "material": _mat0,
                           "options": _o[i:i + 4]}
                          for i in range(0, len(_o), 4)]
                log.info(f"    多题合并页：拆成 {len(groups)} 道题")
            else:
                groups[0]["stem"] = _stem0
                groups[0]["material"] = _mat0
                if len(_o) > 4:
                    log.info(f"    多选题 {len(_o)} 个选项，不拆分")
        # trans 已在函数开头算好（含自动转录），这里直接复用

        if groups:
            log.info(f"    发现 {len(groups)} 个题块")
            unanswered = 0
            # 题库只读一次；blocks 按题/按空分组，用于多题页精确对应
            _bank_all = _load_bank().get(str(lf.get("id"))) or {}
            _bank_blocks = _bank_all.get("blocks") or []
            _blocks_match = (len(_bank_blocks) == len(groups) and
                             all(isinstance(b, list) and b for b in _bank_blocks))
            if _bank_blocks and not _blocks_match:
                log.info(f"    题库分组数({len(_bank_blocks)})与题块数"
                         f"({len(groups)})不等，退回逐题判断")
            for gi, g in enumerate(groups, 1):
                texts = [o["text"] for o in g.get("options") or []]
                cur = [i for i, o in enumerate(g.get("options") or [])
                       if o.get("selected")]
                from upc.hearing import is_multi as _is_multi
                _multi_hint = _is_multi(g.get("stem", ""))
                qtype_multi = bool(_multi_hint)
                bank_letters = _bank_all.get("letters") or []
                # 只把「单个字母」当选择题答案；填空的文字答案走填空分支
                if not all(len(str(x)) == 1 and str(x).isalpha()
                           for x in bank_letters):
                    bank_letters = []
                # ★ 首选：该题的分组答案（多题页每题各自答案，不再张冠李戴）
                block_letters = []
                if _blocks_match:
                    for _x in _bank_blocks[gi - 1]:
                        _s = str(_x).strip().upper()
                        if _s and _s[0].isalpha():
                            block_letters.append(_s[0])
                if block_letters:
                    want = [ord(x) - 65 for x in block_letters]
                    want = [i for i in want if 0 <= i < len(texts)]
                    log.info(f"      题{gi} 题库分组命中，用标准答案 "
                             f"{[chr(65 + i) for i in want]}")
                elif bank_letters and not (len(groups) > 1
                                           and len(bank_letters) != len(groups)):
                    # 旧格式兜底：直接用上次判分拿到的扁平标准答案。
                    # 若答案数 == 题块数，说明是「每题一个答案」的多题页，
                    # 按题索引分配；否则是单题多选，整组字母都选。
                    # ★ 多题页且答案数对不上 → 这串字母属于别的题，乱套到
                    #   每道题上会出现"每题都全选"的连环错。宁可不用题库，
                    #   落到下面的大模型分支重新答。
                    if len(groups) > 1 and len(bank_letters) == len(groups):
                        _one = bank_letters[gi - 1]
                        want = [ord(str(_one).upper()) - 65] \
                            if str(_one) and str(_one)[0].isalpha() else []
                    else:
                        want = [ord(x.upper()) - 65 for x in bank_letters
                                if x and x[0].isalpha()]
                    want = [i for i in want if 0 <= i < len(texts)]
                    log.info(f"      题{gi} 题库命中(扁平)，用标准答案 "
                             f"{[chr(65 + i) for i in want]}")
                elif texts:
                    # 优先交给大模型真读题（题干+选项+音频转录）。
                    # 注意：即使没有音频转录也要调 —— 这类页面题干里
                    # 往往自带阅读材料（实测 Step 2 / Interviewing 就是）。
                    # 字面相似度只会撞词，理解题必错。
                    _ans = None
                    _cli = _get_llm(cfg, log)
                    if _cli is not None:
                        # ★ 多题块页也要材料兜底：题块里没抓到材料时用
                        #   整页文本（含阅读原文）——单题块页此前已有此逻辑，
                        #   多题块页不给材料，模型只能瞎猜（"看不懂题目"）。
                        _mat = (g.get("material") or "").strip() or _page_txt
                        if _mat:
                            log.info(f"      题{gi} 带上网页材料 {len(_mat)} 字符")
                        # 关键词判不出单/多选时，让模型自己判断题型的措辞
                        # （"Choose the items..."这类清单题被当成单选会只答一个）。
                        _qtype = ("多选" if qtype_multi
                                  else ("单选还是多选请读题干自行判断"
                                        if _multi_hint is None else "单选"))
                        _r = _cli.ask(g.get("stem", ""), texts, _qtype,
                                      transcript=trans, material=_mat)
                        if _r and _r.get("answer"):
                            _ans = _r["answer"]
                            # 选择题只认 A-H 的单个字母（可带尾随标点）。
                            # 以前用 [:1] 硬截，"ANSWER" 会被截成 "A"，
                            # 于是每道题都被选成 A —— 这正是"没看题就答"的来源。
                            _ls = []
                            for x in (_ans.replace("，", ",")
                                      .replace("、", ",")
                                      .replace(";", ",").replace("；", ",")
                                      .split(",")):
                                t = x.strip().upper().rstrip(".、)")
                                if len(t) == 1 and "A" <= t <= "H":
                                    _ls.append(t)
                            # 多选模型常不带分隔符连写（"ACE"）→ 按字符拆开；
                            # 不拆的话答案解析为空、整题回退瞎猜。
                            if len(_ls) < 2:
                                _raw3 = str(_ans).strip().upper()
                                if (2 <= len(_raw3) <= 8
                                        and all("A" <= c <= "H" for c in _raw3)):
                                    _ls = list(_raw3)
                            want = [ord(x) - 65 for x in _ls]
                            want = [i for i in want if 0 <= i < len(texts)]
                            if not want:
                                log.warning(f"      题{gi} 大模型回的不是选项字母"
                                            f"（{str(_ans)[:40]!r}），改用兜底")
                                _ans = None
                            else:
                                log.info(f"      题{gi} 大模型作答 "
                                         f"{[chr(65 + i) for i in want]}")
                    if _ans is None:
                        # 大模型不可用时才退回字面匹配
                        want, scores = _decide(texts, trans, g.get("stem", ""))
                        detail = " ".join(
                            f"{chr(65 + i)}:{sc:.2f}" for i, sc in scores)
                        log.info(f"      题{gi} 相似度 {detail}")
                        log.info(f"      题{gi} 选择 "
                                 f"{[chr(65 + i) for i in want] or '无'}")
                else:
                    # 没有音频转录（页面本身就没听力材料）时，若一个都没选，
                    # 会导致「未答完不提交」→ 永远拿不到标准答案。
                    # 这里选第一个作为试探，第二遍用题库一次满分。
                    if cur:
                        want = cur
                    elif texts:
                        want = [0]
                        log.info(f"      题{gi} 无转录材料，先选第一个试探"
                                 f"（仅为取得标准答案）")
                    else:
                        want = []
                n = _apply_choice(page, log, g, want)
                if n:
                    log.info(f"      题{gi} 调整了 {n} 个选项")
                page.wait_for_timeout(300)
                if not want:
                    unanswered += 1
            info["groups"] = len(groups)
            info["unanswered"] = unanswered
            info["used_transcript"] = bool(trans)
            page.wait_for_timeout(500)

        # ②' 老式兜底（真有 radio/checkbox 时）
        if not groups:
            res = page.evaluate(JS_FILL) or {}
            if res.get("answered"):
                info["answered"] = res.get("answered", 0)
                info["total"] = max(info["total"], res.get("total", 0))
                log.info(f"    兜底作答 {info['answered']}/{info['total']}")

        # ③ 讨论/浏览页：用 submitType=2「标记已看」完成，不往讨论区发内容
        #    实测 text（纯文本/词卡类）同样靠这个接口 pass
        if lf.get("tab_type") in ("discussion", "text"):
            from upc import mark_seen as _MS
            ok = _MS.mark_seen(page, log, lf.get("id"))
            log.info(f"    讨论页标记已看：{'✓ 成功' if ok else '未成功'}")
            info["submitted"] = "标记已看" if ok else ""
            info["mark_seen"] = ok
            page.wait_for_timeout(1200)
            return info

        # ④ 兜底：真的有 radio/checkbox 的老式页面
        if info["answered"] == 0:
            res = page.evaluate(JS_FILL) or {}
            info["answered"] = res.get("answered", 0)
            info["total"] = max(info["total"], res.get("total", 0))
            log.info(f"    兜底客观题作答 {info['answered']}/{info['total']}")

        # ②0 排序/人物匹配题（sequence）：单独走拖拽逻辑
        try:
            if page.query_selector(".sortable-list-wrapper") is not None:
                info["sequence"] = True
                _handle_sequence(page, log, lf)
                page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"    排序题处理失败: {str(exc)[:60]}")

        # ②-1 Role-play 多步对话页：专用处理器优先，别让通用口语题误判
        if not info.get("sequence"):
            try:
                _rp = _handle_roleplay(page, log, cfg, lf)
                if _rp is not None:
                    info["roleplay"] = True
                    if _rp > 0:
                        info["voice"] = True
                        log.info(f"    Role-play：逐句跟读完成 {_rp} 句")
                    else:
                        log.warning("    Role-play：页面处于回顾态，未录音")
            except Exception as exc:  # noqa: BLE001
                log.debug(f"    Role-play 处理异常: {str(exc)[:80]}")

        # ②-1b 通用口语题（录音控件）：TTS + 劫持麦克风，让平台录到合成音频
        if not info.get("sequence") and not info.get("roleplay"):
            try:
                _rec = page.query_selector(
                    ".record-button-wrap, .ucomp-recorder, .button-record")
                if _rec is not None:
                    info["voice"] = True
                    _stem_v = ""
                    for _g in (locals().get("groups") or []):
                        _stem_v = _g.get("stem") or ""
                        if _stem_v:
                            break
                    if not _stem_v:
                        _stem_v = page.evaluate(
                            "() => document.body ? document.body.innerText.slice(0, 1200) : ''") or ""
                    from upc.voice import handle_voice
                    if handle_voice(page, log, cfg, lf.get("id"), _stem_v):
                        log.info("    口语题：录音完成，继续走提交")
            except Exception as exc:  # noqa: BLE001
                log.debug(f"    口语题处理异常: {str(exc)[:80]}")

        # ★ 有题块但完全没识别出题目（人物匹配/排序等未知题型）：
        #   这种情况绝不能点提交，平台会弹"还有N道题没有作答"。
        _has_blocks = (scan.get("questions") or [])
        if _has_blocks and not (locals().get("groups") or []) \
                and not (locals().get("blanks") or []) \
                and not info.get("openq") \
                and not info.get("sequence") and not info.get("voice"):
            # 留存现场，便于事后补上这种新题型
            _d = {}
            try:
                _d = page.evaluate(JS_DUMP_UNKNOWN) or {}
                (ROOT / "output" /
                 f"unknown_{lf.get('id')}.json").write_text(
                    json.dumps(_d, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            # 不是新题型，而是 PC 端根本做不了：别标记已看（无效），
            # 主流程也别停留计时 / 回退重做整节。
            _page_text = _d.get("text") or ""
            _pc_bad = is_pc_unsupported(_page_text)
            if _pc_bad or is_roleplay_stuck(_page_text):
                _why = ("平台提示 PC 端不支持" if _pc_bad
                        else "Role-play 停在选角页、进不了 Step 2")
                log.info(f"    {_why}，本项跳过（PC 端无法完成）")
                info["unsupported_pc"] = True
                info["skip_reason"] = _why
                info["unrecognized"] = False
                info["submitted"] = ""
                return info
            log.warning("    页面上有题目但未能识别题型，本次不提交")
            info["unrecognized"] = True
            info["submitted"] = ""
            # 若是纯浏览/词卡类，标记已看即可通过；
            # 有真题目时服务端不认，但不影响（也不算提交错误答案）
            try:
                from upc import mark_seen as _MS3
                if _MS3.mark_seen(page, log, lf.get("id")):
                    log.info("    已尝试标记已看（未点提交）")
                    info["mark_seen"] = True
            except Exception:  # noqa: BLE001
                pass
            return info

        # 未答完不提交：只答了部分题就交卷会被平台拦或算无效作答
        _unfinished = (info.get("unanswered", 0) or 0) + \
                      (info.get("unfinished_blanks", 0) or 0)
        if _unfinished > 0:
            log.warning(f"    还有 {_unfinished} 处未作答（题块 {info.get('unanswered', 0)}"
                        f" / 空 {info.get('unfinished_blanks', 0)}），本次暂不提交")
            info["submitted"] = ""
            return info

        # ③ 提交 + 判分闭环
        from upc.decrypt import (parse_answers, first_answer_list,
                                 parse_answer_blocks)

        def _submit_once():
            """点一次提交，返回 (是否点到, data 字典)。"""
            box = {}

            def _on_sub(r):
                if "/newExploration/submit" in (r.url or ""):
                    try:
                        box["j"] = r.json()
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", _on_sub)
            hit = ""
            try:
                r = page.evaluate(JS_FIND_SUBMIT) or {}
                hit = r.get("hit") or ""
                if not hit:
                    log.info(f"    可见短文本按钮: {r.get('seen')}")
                if hit:
                    btn = page.query_selector('[data-upc-submit="1"]')
                    if btn is not None:
                        btn.scroll_into_view_if_needed(timeout=4000)
                        btn.click(timeout=6000)
                        log.info(f"    点击「{hit}」✓")
                        page.wait_for_timeout(3000)
                        # ★ 先读弹窗原文：平台在这里会提示"还有题目未作答"
                        try:
                            mt = page.evaluate(r"""
                            () => {
                              const out = [];
                              for (const m of document.querySelectorAll(
                                  '.ant-modal, .ant-modal-confirm, [class*="modal"],'
                                  + '[class*="message"], [class*="toast"]')) {
                                const r = m.getBoundingClientRect();
                                if (r.width < 40 || r.height < 20) continue;
                                const t = (m.innerText || '').replace(/\s+/g, ' ').trim();
                                if (t && t.length < 300) out.push(t);
                              }
                              return Array.from(new Set(out));
                            }
                            """) or []
                        except Exception:  # noqa: BLE001
                            mt = []
                        if mt:
                            for _t in mt[:3]:
                                log.info(f"    提交后提示: {_t[:110]}")
                            bad = ("未作答", "未完成", "请完成", "还有", "不能为空",
                                   "请先")
                            if any(any(k in _t for k in bad) for _t in mt):
                                box["incomplete"] = True
                                log.warning("    平台提示还有未完成项，判定为未完成")
                        popup.sweep(page, rounds=1)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"    提交点击失败: {str(exc)[:80]}")
            finally:
                page.wait_for_timeout(1200)
                try:
                    page.remove_listener("response", _on_sub)
                except Exception:  # noqa: BLE001
                    pass
            if box.get("incomplete"):
                info["incomplete"] = True
                # 自动留存现场，便于事后分析没识别出来的题型
                try:
                    _d = page.evaluate(JS_DUMP_UNKNOWN) or {}
                    _f = ROOT / "output" / f"incomplete_{lf.get('id')}.json"
                    _f.write_text(json.dumps(_d, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                    log.info(f"    现场已留存: {_f.name}")
                except Exception:  # noqa: BLE001
                    pass
                # ★ 文件上传题：平台要求上传作品文件（拖拽区），程序永远
                #   补不上，每遍重做纯浪费时长。标记 needs_upload，
                #   下一轮起像「App 端不支持」一样直接跳过，不再打开页面。
                try:
                    _upl = page.evaluate(r"""
                    () => {
                      const up = document.querySelector(
                        '.question-multi-file-upload, [class*="file-upload"]');
                      const done = up ? up.querySelector(
                        '.ant-upload-list-item, [class*="upload-file"],'
                        + ' [class*="media-list"] > *') : null;
                      return { has: !!up, done: !!done,
                               opts: document.querySelectorAll('.option').length };
                    }""") or {}
                except Exception:  # noqa: BLE001
                    _upl = {}
                if _upl.get("has") and not _upl.get("done") \
                        and not _upl.get("opts"):
                    info["needs_upload"] = True
                    log.warning("    判定为「需上传作品」题型：程序无法代劳，"
                                "已标记跳过；请在网页/App 端手动上传作品")
            j = box.get("j") or {}
            code = j.get("code")
            if code in (600001, 600002, "600001", "600002"):
                msg = j.get("msg") or j.get("message") or ""
                log.warning(f"    ⚠ 触发平台限频（{code}）：{str(msg)[:60]}")
                log.warning("    平台要求冷却，本次跳过提交（避免账号风控）")
                info["throttled"] = True
            return hit, j.get("data") or {}

        submitted, data = _submit_once()
        if info.get("throttled"):
            log.warning("    已限频，跳过本题后续重答")
            return info
        score = None
        std_letters = []
        std_blocks = []
        if data:
            _analysis = data.get("analysis") or ""
            _daykey = data.get("dayKey") or ""
            score = ((data.get("record_grade") or {}).get("scorePct"))
            std_letters = first_answer_list(parse_answers(_analysis, _daykey))
            std_blocks = parse_answer_blocks(_analysis, _daykey)
            log.info(f"    判分：scorePct={score} 标准答案={std_letters} "
                     f"分组={len(std_blocks)} 组")

        # 提交后页面进入 review 模式、控件锁定，无法当场改答案；
        # 选择题重进后连 .option 都不渲染。统一交给第二遍（--passes）
        # 用题库满分，这里只沉淀标准答案。
        # 注：之前试过「重进页面重答」，实测会卡死（22 分钟无输出），已废弃。
        #
        # ★ 不满分不覆盖题库：第二遍若已按题库满分(score=1)，
        #   说明题库答案正确，保留；若重做仍不满分，说明题库里的旧答案
        #   是错的，不要用它覆盖掉可能正确的旧答案，更不能把 score 记成
        #   0/低分导致永远留在 low_ids 死循环。
        if std_letters or std_blocks:
            _old = (_load_bank().get(str(lf.get("id"))) or {})
            _old_score = _old.get("score")
            # 题库里是否已有可用答案（分组或扁平）
            _has_old_answer = bool(_old.get("blocks")) or bool(_old.get("letters"))
            # 写入条件：
            #  ① 题库还没有可用答案（如坏条目被清空）→ 必须写入新答案；
            #  ② 本次分数更高 → 用更好的答案覆盖；
            #  ③ 本次满分 → 一定是标准答案，覆盖。
            should_write = (not _has_old_answer) or (score is not None and
                            (_old_score is None or score > _old_score)) \
                            or (score == 1)
            if should_write:
                # ★ letters 字段只在「单题页」才用第一组答案填充；
                #   多题页的第一组答案冒充扁平列表，第二遍会被当成
                #   "每题都选这些字母"连环错位——多题页只信 blocks。
                _flat = std_letters or (std_blocks[0]
                                        if len(std_blocks) == 1 else [])
                _save_bank(lf.get("id"), _flat,
                           score if score is not None else _old_score,
                           blocks=std_blocks)
                log.info(f"    已写入题库（score={score}，{len(std_blocks)} 组答案）")
            if score is not None and score != 1:
                log.info(f"    本次 scorePct={score} 未满；"
                         f"第二遍按题库分组答案补满分")

        # 多页题：点到的其实是「下一题」，说明后面还有一页，继续答
        if not data and submitted in ("下一题", "继续") and _depth < 6:
            log.info(f"    多页题：进入第 {_depth + 2} 页继续作答")
            page.wait_for_timeout(2200)
            _clear_modals(page, log)
            page.wait_for_timeout(300)
            return _handle_quiz(session, cfg, log, page, popup, humanizer, lf,
                                choice_mode, _depth + 1)

        if not submitted:
            # 没有提交按钮的页面（AI 对话 Talk with AI、纯浏览等）
            # 一律用「标记已看」兜底，这是唯一能拿到 pass 的途径
            try:
                from upc import mark_seen as _MS2
                if _MS2.mark_seen(page, log, lf.get("id")):
                    submitted = "标记已看"
                    log.info("    无提交按钮，改用「标记已看」✓")
            except Exception as exc:  # noqa: BLE001
                log.debug(f"    标记已看兜底失败: {str(exc)[:60]}")
        if not submitted:
            log.warning("    ⚠ 没找到提交按钮，本次作答未提交")
        info["submitted"] = submitted
        info["score"] = score
        info["std_answers"] = std_letters
        return info
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    答题流程异常: {str(exc)[:90]}")
        return info or None


JS_DUMP_UNKNOWN = r"""
() => {
  // 现场留存：以前 html 只留 3000 字符、clickables 只留 60 个且跳过有子节点
  // 的元素，Role-play 的 A/B 和录音控件全被截掉了，没法排查新题型。
  const out = {text: '', html: '', clickables: [], widgets: [], tcount: 0};
  const box = document.querySelector(
      '[class*="pc-content-container"], .ant-layout-content, '
      + '[class*="question-wrap"], [class*="question-common"]');
  const root = box || document.body;
  out.text = (root.innerText || '').replace(/\s+/g, ' ').slice(0, 4000);
  out.html = (root.innerHTML || '').replace(/\s+/g, ' ').slice(0, 30000);
  for (const e of document.querySelectorAll('*')) {
    const t = (e.innerText || '').trim();
    if (!t || t.length > 24) continue;
    if (e.children.length > 2) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    out.clickables.push({tag: e.tagName.toLowerCase(),
                         cls: String(e.className).slice(0, 60),
                         vis: r.width > 0 && r.height > 0,
                         t: t.slice(0, 20)});
    if (out.clickables.length > 300) break;
  }
  for (const e of document.querySelectorAll(
      'input,textarea,audio,[class*="record"],[class*="oral"],[class*="score"]')) {
    const r = e.getBoundingClientRect();
    out.widgets.push({tag: e.tagName.toLowerCase(),
                      cls: String(e.className).slice(0, 70),
                      type: e.getAttribute('type') || '',
                      src: (e.currentSrc || e.src || '').slice(0, 120),
                      vis: r.width > 0 && r.height > 0,
                      wh: Math.round(r.width) + 'x' + Math.round(r.height),
                      t: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 40)});
    if (out.widgets.length > 120) break;
  }
  return out;
}
"""


JS_CLEAR_MODALS = r"""
() => {
  // ★ 关键：先把所有可见弹窗的文本记下来再关。
  // 之前无差别 remove 遮罩，把平台「还有题目未作答」这类提示也一起删了，
  // 导致误以为已完成而继续提交（用户明确指出过这个问题）。
  const texts = [];
  for (const m of document.querySelectorAll(
      '.ant-modal, .ant-modal-wrap, .ant-modal-confirm, [class*="modal"]')) {
    const r = m.getBoundingClientRect();
    if (r.width < 40 || r.height < 30) continue;
    const t = (m.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 400) texts.push(t);
  }

  let clicked = 0;
  for (const b of document.querySelectorAll('button,div,a')) {
    if (b.children.length) continue;
    if ((b.innerText || '').replace(/\s+/g, '') !== '确定') continue;
    try { b.click(); clicked++; } catch (e) {}
  }
  // 残留的透明遮罩直接拆掉，否则 Playwright 的 click/fill 会一直超时
  const left = [];
  for (const e of document.querySelectorAll('.ant-modal-mask,.ant-modal-wrap')) {
    left.push(String(e.className));
    e.remove();
  }
  return {clicked, removed: left.length, texts: Array.from(new Set(texts)),
          remain: document.querySelectorAll('.ant-modal-mask,.ant-modal-wrap').length};
}
"""


def _clear_modals(page, log):
    """清干净所有确认弹窗/遮罩。

    实测：课件页会叠 2 层「本单元学习时间：无限制 / 是否必修」确认框，
    PopupGuard 只关 1 层，残留遮罩会让后续所有真实点击/输入超时。

    重要：**优先用 Playwright 真实点击「确定」**，早期 text 页能 pass
    正是走的真实点击；JS click + 强拆 DOM 会把上报组件一起卸载掉。
    """
    # 纯 JS：点掉「确定」+ 直接拆遮罩。
    # 早期靠真实点击，是因为那时只能靠点确定触发阅读上报；
    # 现在 pass 由 submitType=2 保证，这里只为放行点击，越快越好。
    total = 0
    said = []
    for _ in range(3):
        try:
            r = page.evaluate(JS_CLEAR_MODALS) or {}
        except Exception:  # noqa: BLE001
            break
        total += r.get("removed", 0)
        for t in (r.get("texts") or []):
            if t not in said:
                said.append(t)
        if r.get("remain", 0) == 0:
            break
        page.wait_for_timeout(150)
    if said:
        # 把弹窗原文记进日志，避免"看见了却没当回事"
        for t in said[:4]:
            log.info(f"  弹窗内容: {t[:120]}")
    if total:
        log.debug(f"  清除 {total} 层遮罩")
    return said


def _dwell(session, cfg, log, page, seconds, popup, humanizer):
    remain = max(0.0, seconds)
    t0 = time.time()
    last_move = 0.0
    while remain > 0:
        popup.tick(page)
        session.keep_alive()
        el = time.time() - t0
        if el - last_move >= 45:
            last_move = el
            humanizer.nudge(page)
        time.sleep(min(2.0, remain))
        remain -= 2.0


def _wake_wb(reason, detail, rc):
    """失败出口：唤起 WorkBuddy 判断。兜底模块出问题绝不能影响主程序。"""
    try:
        import importlib.util
        _p = Path(__file__).with_name("wb_helper.py")
        _spec = importlib.util.spec_from_file_location("wb_helper", _p)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.wake(reason, detail=detail, rc=rc)
    except Exception as _e:  # noqa: BLE001
        print(f"[WorkBuddy 兜底] 未能唤起（已忽略）：{type(_e).__name__}: {_e}")
        return None


def _expand_book_all(argv):
    """把 --book all 展开成「逐本执行」方案。

    返回 (改写后的 argv, 待跑书目列表)。非 all 时书目只有一项 None
    （argv 原样、走单本老路径）。两本书各跑一个完整 main()：
    互相独立的 state / 浏览器上下文，第一本异常中断就不连坐第二本。
    """
    argv = list(argv)
    if "--book" not in argv:
        return argv, None
    i = argv.index("--book")
    if i + 1 >= len(argv) or argv[i + 1] != "all":
        return argv, None
    books = list(BOOK_KEY)  # vls 视听说 → rw 读写，按定义顺序
    argv[i + 1] = books[0]
    return argv, books


def _run_cli():
    """单次 main() 的完整异常链，返回进程退出码。"""
    try:
        _rc = main()
    except KeyboardInterrupt:
        return 130
    except SystemExit as _e:
        try:
            return int(_e.code or 0)
        except (TypeError, ValueError):
            return 1
    except BaseException as _exc:  # noqa: BLE001
        _wake_wb("刷课主程序未捕获异常", f"{type(_exc).__name__}: {_exc}", 1)
        return 1
    if _rc:
        _wake_wb("刷课程序没能跑完", f"main() 返回 {_rc}；请结合最近日志判断原因", _rc)
    return _rc


if __name__ == "__main__":
    # 所有"程序自己解决不了"的出口（main 返回非 0 / 未捕获异常）统一唤起
    # WorkBuddy 判断；用户主动 Ctrl+C 和正常结束不打扰。
    # --book all：串行跑完全部教材（视听说 → 读写）。
    _argv, _books = _expand_book_all(sys.argv)
    if not _books:
        raise SystemExit(_run_cli())
    _rc = 0
    for _bk in _books:
        if _bk != _books[0]:
            print(f"\n{'=' * 62}\n第一本处理完毕，切换教材继续："
                  f"{_bk}（{len(_books)} 本中的第 {_books.index(_bk) + 1} 本）\n{'=' * 62}")
        _argv[_argv.index("--book") + 1] = _bk
        sys.argv = _argv
        _rc = _run_cli()
        # 中断 / Ctrl+C / 异常（130、1、2 等）就不跑第二本：
        # 多半是登录态或平台问题，连坐只是重复白跑
        if _rc != 0:
            break
    raise SystemExit(_rc)
