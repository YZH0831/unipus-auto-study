# -*- coding: utf-8 -*-
"""讨论页 / 浏览页「标记已看」（submitType=2）。

实现依据：2026-11-07 实测 —— 用真实提交的 body 模板，把
quesDatas 置空、thirdPartyJudges 置 "[]"、submitType 改 2，
POST /course/api/v3/newExploration/submit 即返回 code:0，
对应叶子 course_progress.state.pass 从 0 变 1。

必需 header（前端 axios 拦截器加的，浏览器不会自动补）：
    authorization          SSO JWT
    x-annotator-auth-token ucontent JWT
    appid, content-type
这些由 tools/grab_submit_tpl.py 从一次真实提交里抓下来缓存到
state/submit_headers.json。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

JS_MARK = r"""
async (args) => {
  const [hdr, base, group] = args;
  const body = Object.assign({}, base, {
    groupId: group, quesDatas: [], isCompleted: [true],
    thirdPartyJudges: "[]", submitType: 2,
  });
  try {
    const r = await fetch('/course/api/v3/newExploration/submit', {
      method: 'POST', credentials: 'include', headers: hdr,
      body: JSON.stringify(body),
    });
    const t = await r.text();
    let j = {};
    try { j = JSON.parse(t); } catch (e) {}
    return {code: j.code, msg: j.message || j.msg || '', status: r.status,
            raw: t.slice(0, 200)};
  } catch (e) {
    return {error: String(e).slice(0, 150)};
  }
}
"""


def load_headers():
    """读缓存的提交头（含 courseId / openId / version 基础字段）。"""
    p = ROOT / "state" / "submit_headers.json"
    if not p.exists():
        # 兼容早期抓包产物
        alt = ROOT / "output" / "submit_tpl.json"
        if alt.exists():
            d = json.loads(alt.read_text(encoding="utf-8"))
            hdr = d.get("hdr") or {}
            body = d.get("body") or {}
            base = {k: v for k, v in body.items()
                    if k in ("courseId", "openId", "version", "hideLoading",
                             "associationGroupId")}
            if hdr and base:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"hdr": hdr, "base": base},
                                        ensure_ascii=False, indent=2),
                             encoding="utf-8")
                return hdr, base
        return None, None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("hdr"), d.get("base")


def _course_id_from_page(page):
    """从课件页 URL 的 hash 里取当前课程实例（course-v2:...）。

    ★ 缓存模板里的 courseId 是当初抓包那本书的（实测固定为「视听说」）。
      换到另一本书（如读写）后仍然带着它提交，接口照样返回 code:0，
      但写的是另一本书，当前叶子永远 pass=0 —— 日志却显示「✓ 成功」。
      所以每次都必须以页面当前所属课程为准。
    """
    try:
        url = getattr(page, "url", "") or ""
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"#/(course-v2:[^/]+?)/courseware", url)
    return m.group(1) if m else ""


def mark_seen(page, log, group_id, hdr=None, base=None, course_id=""):
    """给一个叶子打「已看」。成功返回 True。"""
    if hdr is None or base is None:
        hdr, base = load_headers()
    if not hdr or not base:
        log.warning("    缺提交头（先跑 tools/grab_submit_tpl.py），跳过标记已看")
        return False
    # 课程实例必须以当前页面为准，不能用缓存模板里那本书的
    cid = course_id or _course_id_from_page(page)
    cached_cid = str(base.get("courseId") or "")
    if cid and cid != cached_cid:
        base = dict(base)
        base["courseId"] = cid
        log.debug(f"    标记已看 courseId 校正: {cached_cid[-28:]} → {cid[-28:]}")
    try:
        r = page.evaluate(JS_MARK, [hdr, base, group_id]) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    标记已看异常: {str(exc)[:80]}")
        return False
    code = r.get("code")
    if code == 0:
        return True
    if code in (600001, 600002):
        log.warning(f"    标记已看触发限频（{code}），本次跳过")
    else:
        log.info(f"    标记已看未成功: code={code} {r.get('msg') or r.get('error')}")
    return False
