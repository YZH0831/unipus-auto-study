# -*- coding: utf-8 -*-
"""DOM 探针：把当前课程页面的真实结构 dump 出来，用于校准选择器。

什么时候用
----------
- 程序提示"未找到课程目录 / 目录结构未识别"
- 某个 Task 明明有视频却没播
- 单元测试识别不到题目

怎么用
------
    python tools/probe.py            # 打开浏览器，你自己登录+进课程页
    python tools/probe.py --auto     # 按 config 的 course.url 自动进课程

然后它会：
    1. 打印每个选择器命中了几个元素（一眼看出哪条链断了）
    2. 列出页面里所有 frame / iframe
    3. 把目录节点、Tab、Task 的 class 名和文本导出到 output/probe_*.json
    4. 把关键区域的外层 HTML 保存到 output/probe_*.html

拿这个 json + html 就能精确改 upc/selectors.py，或者直接发给我。
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


JS_PROBE = r"""
() => {
  const out = {
    url: location.href,
    title: document.title,
    bodyLen: (document.body ? document.body.innerText.length : 0),
    textSample: (document.body ? document.body.innerText : '').slice(0, 1500),
  };

  // 收集所有元素的 class 特征（找候选选择器用）
  const classCount = {};
  const tagCount = {};
  const els = document.querySelectorAll('*');
  for (const el of els) {
    const t = (el.tagName || '').toLowerCase();
    tagCount[t] = (tagCount[t] || 0) + 1;
    const cls = el.className;
    if (typeof cls === 'string' && cls.trim()) {
      for (const c of cls.trim().split(/\s+/)) {
        if (c.length > 2 && c.length < 60) classCount[c] = (classCount[c] || 0) + 1;
      }
    }
  }
  // 只保留出现次数不多（有辨识度）的类名
  out.classes = Object.entries(classCount)
    .filter(([k, v]) => v >= 1 && v <= 400)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 250)
    .map(([k, v]) => ({cls: k, n: v}));
  out.tags = Object.entries(tagCount).sort((a, b) => b[1] - a[1]).slice(0, 40)
             .map(([k, v]) => ({tag: k, n: v}));

  // 菜单 / 树
  const menuNodes = [];
  const menuSels = ['.pc-slider-menu-unit', '.pc-slider-menu-node', '.pc-slider-menu-section',
                    '.pc-slider-menu-micro', '.ant-tree-treenode', '[role="treeitem"]',
                    'li.unit', 'li.group', '[class*="menu"] [class*="item"]'];
  for (const s of menuSels) {
    let list = [];
    try { list = document.querySelectorAll(s); } catch (e) { continue; }
    for (const el of Array.from(list).slice(0, 40)) {
      menuNodes.push({
        sel: s,
        cls: typeof el.className === 'string' ? el.className : '',
        text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100),
        level: el.getAttribute('aria-level'),
        expanded: el.getAttribute('aria-expanded'),
        childNodes: el.querySelectorAll('.pc-slider-menu-micro, [role="treeitem"]').length,
      });
    }
  }
  out.menuNodes = menuNodes;

  // Tab / Task
  const tabs = [], tasks = [];
  for (const s of ['.pc-header-tabs-container .ant-col', '.ant-tabs-tab',
                   '[role="tab"]', 'a.topTab', '[class*="tabs"] a']) {
    let list = [];
    try { list = document.querySelectorAll(s); } catch (e) { continue; }
    for (const el of Array.from(list).slice(0, 30)) {
      tabs.push({sel: s, cls: typeof el.className === 'string' ? el.className : '',
                 text: (el.innerText || '').trim().slice(0, 60)});
    }
  }
  for (const s of ['.pc-task', '[class*="task"]', '.pc-header-tasks-row *']) {
    let list = [];
    try { list = document.querySelectorAll(s); } catch (e) { continue; }
    for (const el of Array.from(list).slice(0, 30)) {
      const txt = (el.innerText || '').trim();
      if (!txt) continue;
      tasks.push({sel: s, cls: typeof el.className === 'string' ? el.className : '',
                  text: txt.slice(0, 60)});
    }
  }
  out.tabs = tabs;
  out.tasks = tasks;

  // 媒体
  out.media = Array.from(document.querySelectorAll('video, audio')).map(el => ({
    tag: el.tagName.toLowerCase(),
    src: (el.currentSrc || el.getAttribute('src') || '').slice(0, 160),
    dur: (isFinite(el.duration) ? el.duration : 0),
    paused: !!el.paused,
    cls: typeof el.className === 'string' ? el.className : '',
  }));

  // 表单 / 题目
  out.inputs = Array.from(document.querySelectorAll(
    'input[type=radio], input[type=checkbox], input[type=text], textarea')).slice(0, 80)
    .map(el => ({
      type: el.type, name: el.name, id: el.id,
      cls: typeof el.className === 'string' ? el.className : '',
      placeholder: el.placeholder || '',
      visible: el.getClientRects().length > 0,
    }));

  // 按钮（找提交/录音/关闭）
  out.buttons = Array.from(document.querySelectorAll('button, [role="button"], .btn'))
    .slice(0, 120)
    .map(el => ({
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      cls: typeof el.className === 'string' ? el.className : '',
      id: el.id,
      visible: el.getClientRects().length > 0,
    }))
    .filter(b => b.text || b.cls);

  // iframe
  out.iframes = Array.from(document.querySelectorAll('iframe')).map(el => ({
    id: el.id, name: el.name,
    src: (el.src || '').slice(0, 200),
    cls: typeof el.className === 'string' ? el.className : '',
  }));

  return out;
}
"""


def count_selector(scope, sel):
    try:
        return len(scope.query_selector_all(sel))
    except Exception:  # noqa: BLE001
        return -1


def main():
    ap = argparse.ArgumentParser(description="U校园 DOM 探针")
    ap.add_argument("--auto", action="store_true", help="按 config 的 course.url 自动进课程")
    ap.add_argument("--wait", type=int, default=0, help="登录后等你操作多少秒再抓（默认等你回车）")
    args = ap.parse_args()

    from upc import selectors as S
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    log = setup_logging(cfg.log_dir, "INFO")
    log.info("=" * 60)
    log.info("DOM 探针启动")
    log.info("=" * 60)

    session = BrowserSession(cfg, log)
    page = session.page
    try:
        if args.auto and cfg.course.url:
            log.info(f"自动打开课程页: {cfg.course.url}")
            session.goto(cfg.course.url, context="课程页")
            page.wait_for_timeout(4000)
        else:
            log.info("已打开浏览器。请你手动完成以下两步：")
            log.info("  1) 登录 U校园")
            log.info("  2) 进入你的课程学习页（能看到左侧目录的那一页）")
            try:
                session.goto(S.HOME_URL, context="首页")
            except Exception:  # noqa: BLE001
                pass
            if args.wait > 0:
                log.info(f"等待 {args.wait} 秒后自动抓取…")
                time.sleep(args.wait)
            else:
                print()
                try:
                    input(">>> 到达课程页后，按【回车】开始抓取 >>> ")
                except (EOFError, KeyboardInterrupt):
                    pass
            # 切到当前最新的相关页面
            for p in session.context.pages:
                if "unipus.cn" in (p.url or ""):
                    page = p
            page.bring_to_front()
            page.wait_for_timeout(1500)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"probe_{stamp}.json"
        html_path = out_dir / f"probe_{stamp}.html"

        # ---------- 主页面 ----------
        log.info("抓取主页面结构…")
        try:
            main_data = page.evaluate(JS_PROBE)
        except Exception as exc:  # noqa: BLE001
            log.error(f"抓取失败: {exc}")
            return 1

        # ---------- 所有 frame ----------
        frames_data = []
        for fr in page.frames:
            item = {"url": fr.url[:200], "is_main": fr is page.main_frame}
            try:
                item["selector_hits"] = {
                    name: count_selector(fr, sel)
                    for name, sel in {
                        "menu_container": S.MENU_CONTAINER[0],
                        "unit": S.NODE_UNIT[0],
                        "micro": S.NODE_MICRO[0],
                        "tree_item": '[role="treeitem"]',
                        "video": "video",
                        "audio": "audio",
                        "radio": 'input[type="radio"]',
                        "checkbox": 'input[type="checkbox"]',
                        "textarea": "textarea",
                        "submit": "button",
                    }.items()
                }
                try:
                    item["data"] = fr.evaluate(JS_PROBE)
                except Exception:  # noqa: BLE001
                    item["data"] = None
            except Exception as exc:  # noqa: BLE001
                item["error"] = str(exc)[:120]
            frames_data.append(item)

        # ---------- 选择器体检 ----------
        print()
        print("=" * 68)
        print("选择器命中情况（主页面）")
        print("=" * 68)
        groups = [
            ("目录容器", S.MENU_CONTAINER),
            ("单元节点", S.NODE_UNIT),
            ("小节节点", S.NODE_SECTION),
            ("叶子节点", S.NODE_MICRO),
            ("普通节点", S.NODE_PLAIN),
            ("通用树项", S.TREE_ITEM),
            ("Tab", S.TAB_ITEM),
            ("Task", S.TASK_ITEM),
            ("视频", S.VIDEO_ELEMENT),
            ("弹窗按钮", S.POPUP_BUTTONS),
            ("提交按钮", S.SUBMIT_BUTTON),
            ("录音按钮", S.RECORD_BUTTON),
        ]
        verdict = {}
        for label, sels in groups:
            hits = []
            for sel in sels[:6]:
                n = count_selector(page, sel)
                if n > 0:
                    hits.append((sel, n))
            verdict[label] = hits
            if hits:
                best = hits[0]
                print(f"  [OK ] {label:<8} 命中 {best[1]} 个  ← {best[0]}")
            else:
                print(f"  [  -] {label:<8} 全部未命中")

        # ---------- 目录节点样本 ----------
        if main_data.get("menuNodes"):
            print()
            print("=" * 68)
            print("目录节点样本（前 15 个）")
            print("=" * 68)
            for n in main_data["menuNodes"][:15]:
                print(f"  {n['sel']:<32} | {n['text'][:40]:<40} | cls={n['cls'][:40]}")

        # ---------- Tab / Task ----------
        if main_data.get("tabs"):
            print()
            print("Tab 候选：")
            seen = set()
            for t in main_data["tabs"][:12]:
                sig = (t["text"], t["cls"][:30])
                if sig in seen:
                    continue
                seen.add(sig)
                print(f"  {t['sel']:<34} | {t['text'][:24]:<24} | cls={t['cls'][:36]}")
        if main_data.get("tasks"):
            print()
            print("Task 候选：")
            seen = set()
            for t in main_data["tasks"][:12]:
                sig = (t["text"], t["cls"][:30])
                if sig in seen:
                    continue
                seen.add(sig)
                print(f"  {t['sel']:<34} | {t['text'][:24]:<24} | cls={t['cls'][:36]}")

        # ---------- frame ----------
        print()
        print("=" * 68)
        print(f"页面共 {len(frames_data)} 个 frame")
        print("=" * 68)
        for i, f in enumerate(frames_data):
            tag = "主页面" if f["is_main"] else f"iframe#{i}"
            print(f"  [{tag}] {f['url'][:90]}")
            hits = {k: v for k, v in (f.get("selector_hits") or {}).items() if v > 0}
            if hits:
                print(f"          命中: {hits}")

        # ---------- 落盘 ----------
        payload = {
            "generated_at": stamp,
            "course_url": page.url,
            "selector_verdict": {k: v for k, v in verdict.items()},
            "main": main_data,
            "frames": frames_data,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        try:
            html = page.content()
            html_path.write_text(html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"保存 HTML 失败: {str(exc)[:80]}")

        print()
        print("=" * 68)
        print(f"结构已保存：\n  {json_path}\n  {html_path}")
        print("=" * 68)
        print("如果某些选择器全部未命中，把上面这两个文件发我，我直接改 selectors.py。")
        print()
        return 0
    finally:
        try:
            input(">>> 按回车关闭浏览器 >>> ")
        except (EOFError, KeyboardInterrupt):
            pass
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
