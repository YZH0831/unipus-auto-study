# -*- coding: utf-8 -*-
"""门户导航：从 U校园 主站一路点到「教程学习」的单元目录。

为什么需要这一层
----------------
实测（2026-11-04，山西农业大学·大学英语听说一级）真实入口是一条四跳链路：

    u.unipus.cn/newIndex
        └─ 底部「学习」tab
            └─ 「课程」子页（另有 作业 / 课堂互动、校内课程 / 自学教程）
                └─ 书架里的教材卡片（如《新视野大学英语（第四版）视听说教程1》）
                    └─「教程学习」按钮
                        └─ 真正的课程目录（ucontent 系的 Unit/Section/Micro）

用户不可能每次手点这么多层，所以这一层必须自动化。

设计取舍
--------
**全部用可见文字定位，不用 class。**
教材页、门户页是运营频繁改版的页面，class 名几乎每季度变一次；
但"学习""课程""教程学习"这些用户可见的文案是产品语义，改动频率低得多。
配合"文本 → 可点祖先"的向上查找，能扛住大部分改版。
"""
import re
import time

from . import selectors as S

# 底部 / 顶部导航
NAV_LEARN = [
    'a:has-text("学习")',
    'button:has-text("学习")',
    '[role="tab"]:has-text("学习")',
    'li:has-text("学习")',
    'div:has-text("学习")',
]

NAV_COURSE = [
    'a:has-text("课程")',
    '[role="tab"]:has-text("课程")',
    'li:has-text("课程")',
]

# 进入教材的两个入口按钮
ENTER_BOOK = [
    'button:has-text("教程学习")',
    'a:has-text("教程学习")',
    'div[role="button"]:has-text("教程学习")',
    'span:has-text("教程学习")',
    ':text("教程学习")',
]

# 可能出现的学习要求/详情弹窗上的关闭
CLOSE_HINTS = [
    'button:has-text("关闭")',
    'button:has-text("我知道了")',
    '.ant-modal-close',
    '[class*="close"]',
]

# 课程内容站（第四版/AI 版教材落在这里）
CONTENT_HOSTS = ("ucontent.unipus.cn", "ipub.unipus.cn", "uai.unipus.cn")

# ---------------------------------------------------------------- 学校认证
# 实测：SSO 登录成功后 u.unipus.cn/newIndex?school_id= 会弹「U校园认证 - 选择学校」，
# 不选就走不下去。这是最容易卡死的一步，必须自动处理。
SCHOOL_MODAL_HINTS = ("选择学校", "请选择你的学校", "U校园认证", "选择你的学校")
SCHOOL_DROPDOWN = [
    'input[placeholder*="学校"]',
    '[placeholder*="学校"]',
    '[class*="school"] input',
    '[class*="select"] input',
    '.ant-select-selection-search-input',
    '.ant-select-selector',
]
SCHOOL_CONFIRM = [
    'button:has-text("下一步")',
    'button:has-text("确定")',
    'button:has-text("确认")',
    'button:has-text("进入")',
    'button:has-text("完成")',
    'button:has-text("提交")',
    'button.ant-btn-primary',
    '[class*="modal"] button[class*="primary"]',
]


def school_modal_present(page):
    """页面上是否出现了「选择学校」认证弹窗。"""
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        return False
    return any(h in text for h in SCHOOL_MODAL_HINTS)


def handle_school_select(session, cfg, log, humanizer=None):
    """处理「U校园认证 - 选择学校」。返回是否处理成功（或本来就不需要）。

    优先用 config.account.school（如"山西农业大学"）；没配就退化为选第一个。
    """
    page = session.page
    if not school_modal_present(page):
        return True

    school = str(cfg.account.get("school") or "").strip()
    log.info(f"检测到「选择学校」认证弹窗（目标：{school or '第一个可选学校'}）")

    # ---- ① 原生 select ----
    try:
        for sel_el in page.query_selector_all("select"):
            if not sel_el.is_visible():
                continue
            if school:
                try:
                    sel_el.select_option(label=school)
                    log.info(f"  已在下拉框选择「{school}」（原生 select）")
                    return _confirm_school(page, log, humanizer)
                except Exception:  # noqa: BLE001
                    pass
            try:
                opts = sel_el.query_selector_all("option")
                for o in opts:
                    txt = (o.inner_text() or "").strip()
                    if not txt or "请选择" in txt:
                        continue
                    val = o.get_attribute("value")
                    sel_el.select_option(value=val)
                    log.info(f"  已选择「{txt}」（原生 select 兜底）")
                    return _confirm_school(page, log, humanizer)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # ---- ② 自定义下拉框：点开 → 输入过滤 → 选中 ----
    dropdown = S.first_in(page, SCHOOL_DROPDOWN, timeout_s=5)
    if dropdown is None:
        log.warning("  找不到学校下拉框，可能是页面结构变了")
        return False
    try:
        if humanizer:
            humanizer.click(page, dropdown, timeout=6000)
        else:
            dropdown.click(timeout=6000)
        page.wait_for_timeout(800)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  点开下拉框失败: {str(exc)[:70]}")
        return False

    # 输入关键词过滤
    if school:
        try:
            dropdown.fill(school)
            page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            try:
                dropdown.type(school, delay=80)
                page.wait_for_timeout(1200)
            except Exception:  # noqa: BLE001
                pass

    # 点选项
    picked = _click_option_by_text(page, log, school, humanizer)
    if not picked:
        log.warning(f"  下拉列表里没找到「{school or '任何学校'}」")
        return False

    page.wait_for_timeout(800)
    return _confirm_school(page, log, humanizer)


def _click_option_by_text(page, log, school, humanizer=None):
    """在展开的下拉列表里点选包含 school 的选项。"""
    try:
        hit = page.evaluate(r"""
        (school) => {
          const sels = ['.ant-select-item-option', '[class*="option"]',
                        '[class*="dropdown"] li', 'li[role="option"]',
                        '[role="option"]', 'li'];
          const seen = new Set();
          for (const s of sels) {
            let list = [];
            try { list = document.querySelectorAll(s); } catch (e) { continue; }
            for (const el of list) {
              if (seen.has(el)) continue;
              seen.add(el);
              const t = (el.innerText || '').trim();
              if (!t || t.length > 40) continue;
              const r = el.getBoundingClientRect();
              if (r.width < 30 || r.height < 12) continue;
              if (school && !t.includes(school)) continue;
              if (!school && (t.includes('请选择') || t.includes('搜索'))) continue;
              el.setAttribute('data-upc-school', '1');
              return t;
            }
          }
          return '';
        }
        """, school or "")
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    try:
        el = page.query_selector('[data-upc-school="1"]')
        if el is None or not el.is_visible():
            return False
        if humanizer:
            humanizer.click(page, el, timeout=6000)
        else:
            el.click(timeout=6000)
        log.info(f"  已选中「{hit[:30]}」")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  点击学校选项失败: {str(exc)[:70]}")
        return False


def _confirm_school(page, log, humanizer=None):
    """点确认/下一步，并确认弹窗消失。"""
    for sel in SCHOOL_CONFIRM:
        try:
            el = page.query_selector(sel)
            if el is None or not el.is_visible():
                continue
            label = (el.inner_text() or "").strip()[:12]
            if humanizer:
                humanizer.click(page, el, timeout=6000)
            else:
                el.click(timeout=6000)
            log.info(f"  已点击「{label}」")
            page.wait_for_timeout(2500)
            if not school_modal_present(page):
                log.info("  学校认证完成")
                return True
        except Exception:  # noqa: BLE001
            continue
    # 有些版本选完学校弹窗自动关闭
    if not school_modal_present(page):
        return True
    log.warning("  学校已选但弹窗没关掉，可能还需要点确认按钮")
    return False


# ------------------------------------------------------- 学号+姓名 学生认证
# 实测（2026-11-04 山西农业大学）：选完学校后还会弹「U校园认证」，
# 要求填 学号 + 姓名 跟学校上报的学籍库比对，对不上就报
#   「认证失败！1. 学号或姓名输入错误  2. 信息未导入U校园」
# 这一步卡住整条流程，必须自动填。
STUDENT_MODAL_HINTS = ("U校园认证", "我是学生", "我是教师", "认证失败")

# 尝试关掉认证弹窗的入口：很多人根本不需要认证也能用课程
MODAL_CLOSE = [
    '.ant-modal-close',
    '[class*="modal"] [class*="close"]',
    '[class*="dialog"] [class*="close"]',
    '[class*="auth"] [class*="close"]',
    'button:has-text("关闭")',
    'button:has-text("跳过")',
    'button:has-text("暂不")',
    'button:has-text("以后再说")',
    'button:has-text("稍后")',
    'span:has-text("跳过")',
    'a:has-text("跳过")',
    '[aria-label*="关闭"]',
    '[aria-label*="close"]',
]
SKIP_TEXTS = ("跳过", "暂不", "以后再说", "稍后", "关闭", "取消")

JS_CLOSE_MODAL = r"""
() => {
  // 在弹窗里找 × / 关闭 / 跳过
  const modals = Array.from(document.querySelectorAll(
    '[class*="modal"], [class*="dialog"], [class*="auth"]'));
  for (const m of modals) {
    const r = m.getBoundingClientRect();
    if (r.width < 100 || r.height < 60) continue;
    // 标题栏右侧的小方块，通常是 ×
    const cands = Array.from(m.querySelectorAll('i, span, a, button, div'));
    for (const el of cands) {
      const t = (el.innerText || '').trim();
      const cls = String(el.className || '');
      const aria = el.getAttribute('aria-label') || '';
      const isX = /close|×|✕|cancel/i.test(cls) || /close|关闭/i.test(aria);
      const isSkip = ['跳过', '暂不认证', '以后再说', '稍后', '取消'].includes(t) ||
                     /跳过|暂不/.test(t);
      if (!isX && !isSkip) continue;
      const rr = el.getBoundingClientRect();
      if (rr.width < 6 || rr.width > 200 || rr.height < 6 || rr.height > 80) continue;
      el.setAttribute('data-upc-close', '1');
      return t || (isX ? '×' : '');
    }
  }
  return '';
}
"""


def dismiss_verify_modal(page, log, humanizer=None):
    """尝试关掉「U校园认证」弹窗。能关掉就返回 True。

    先试语义化选择器，再退到 JS 精确定位（标题栏右侧的 ×）。
    关掉后如果弹窗又冒出来，说明是强制的，调用方再走填写流程。
    """
    if not student_modal_present(page):
        return True

    for sel in MODAL_CLOSE:
        try:
            els = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text() or "").strip()[:8]
                if humanizer:
                    humanizer.click(page, el, timeout=3000)
                else:
                    el.click(timeout=3000)
                log.info(f"  尝试关闭认证弹窗（{label or '×'}）")
                page.wait_for_timeout(1200)
                if not student_modal_present(page):
                    log.info("  认证弹窗已关闭，无需认证即可继续 ✓")
                    return True
            except Exception:  # noqa: BLE001
                continue

    # JS 兜底
    try:
        hit = page.evaluate(JS_CLOSE_MODAL)
    except Exception:  # noqa: BLE001
        hit = ""
    if hit is not None:
        try:
            el = page.query_selector('[data-upc-close="1"]')
            if el is not None and el.is_visible():
                if humanizer:
                    humanizer.click(page, el, timeout=3000)
                else:
                    el.click(timeout=3000)
                log.info(f"  尝试关闭认证弹窗（JS: {hit or '×'}）")
                page.wait_for_timeout(1200)
                if not student_modal_present(page):
                    log.info("  认证弹窗已关闭，无需认证即可继续 ✓")
                    return True
        except Exception:  # noqa: BLE001
            pass

    log.info("  认证弹窗关不掉（应该是强制的），改为自动填写")
    return False
STUDENT_TAB = [
    'div:has-text("我是学生")',
    '[role="tab"]:has-text("我是学生")',
    'span:has-text("我是学生")',
    ':text("我是学生")',
]
STUDENT_CONFIRM = [
    'button:has-text("确定")',
    'button:has-text("认证")',
    'button:has-text("提交")',
    'button:has-text("下一步")',
    'button.ant-btn-primary',
]

JS_FIND_VERIFY_FIELDS = r"""
() => {
  // 按"输入框附近有没有 学号/姓名 字样"来定位两个输入框
  const inputs = Array.from(document.querySelectorAll('input')).filter(el => {
    if (el.type && ['hidden','checkbox','radio','submit','button'].includes(el.type)) return false;
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 12;
  });
  const out = {sid: -1, name: -1, sid_sel: '', name_sel: ''};
  inputs.forEach((el, i) => {
    let p = el.parentElement, txt = '';
    for (let k = 0; k < 5 && p; k++) { txt += ' ' + (p.innerText || ''); p = p.parentElement; }
    const ph = (el.placeholder || '') + ' ' + txt;
    if (/学号|工号|学籍号/.test(ph) && out.sid < 0) out.sid = i;
    else if (/姓名|名字/.test(ph) && out.name < 0) out.name = i;
    // 单靠 label 可能抓不到，兜底按顺序：第一个输入=学号，第二个=姓名
  });
  if (out.sid < 0 && inputs.length >= 2) out.sid = 0;
  if (out.name < 0 && inputs.length >= 2) out.name = 1;
  inputs.forEach((el, i) => {
    if (i === out.sid) el.setAttribute('data-upc-sid', '1');
    if (i === out.name) el.setAttribute('data-upc-name', '1');
  });
  return {count: inputs.length, sid: out.sid, name: out.name};
}
"""


def student_modal_present(page):
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        return False
    return any(h in text for h in STUDENT_MODAL_HINTS)


def _dismiss_error_popup(page, log, humanizer=None):
    """关掉「认证失败」那个报错小弹窗。"""
    for sel in ('button:has-text("确定")', 'button:has-text("知道了")',
                '.ant-modal-close', '[class*="dialog"] [class*="close"]'):
        try:
            els = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        for el in els:
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text() or "").strip()[:8]
                if humanizer:
                    humanizer.click(page, el, timeout=3000)
                else:
                    el.click(timeout=3000)
                log.info(f"  已关闭报错弹窗（{label or 'X'}）")
                page.wait_for_timeout(700)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def dump_verify_dialog(page, out_dir, log):
    """把「U校园认证」弹窗的完整结构 dump 下来，用于诊断到底卡在哪。

    dump 内容：tab 激活状态、全部输入框（含 placeholder / 邻近 label）、
    全部按钮、弹窗容器 HTML、页面可见文本。
    """
    import json
    from datetime import datetime
    from pathlib import Path

    data = {"generated_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "url": page.url}
    try:
        data["detail"] = page.evaluate(r"""
        () => {
          const out = {};
          out.text = (document.body ? document.body.innerText : '').slice(0, 3000);

          // tab 状态
          out.tabs = Array.from(document.querySelectorAll(
            '[role="tab"], [class*="tab"]')).slice(0, 40).map(el => ({
              text: (el.innerText || '').trim().slice(0, 20),
              cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
              selected: el.getAttribute('aria-selected'),
              active: /active|selected|checked|current/i.test(String(el.className || '')),
            })).filter(t => t.text);

          // 输入框
          out.inputs = Array.from(document.querySelectorAll('input, textarea, select'))
            .slice(0, 40).map(el => {
              let p = el.parentElement, near = '';
              for (let i = 0; i < 4 && p; i++) { near += ' ' + (p.innerText || ''); p = p.parentElement; }
              const r = el.getBoundingClientRect();
              return {
                tag: el.tagName.toLowerCase(), type: el.type || '',
                name: el.name || '', id: el.id || '',
                placeholder: el.placeholder || '',
                value: (el.value || '').slice(0, 30),
                cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                visible: r.width > 0 && r.height > 0,
                nearText: near.replace(/\s+/g, ' ').slice(0, 120),
              };
            });

          // 下拉框当前值
          out.selects = Array.from(document.querySelectorAll('select')).map(el => ({
            value: el.value,
            selectedText: el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '',
          }));

          // 按钮
          out.buttons = Array.from(document.querySelectorAll(
            'button, [role="button"], a')).slice(0, 60).map(el => ({
              text: (el.innerText || '').trim().slice(0, 20),
              cls: (typeof el.className === 'string' ? el.className : '').slice(0, 60),
              visible: el.getBoundingClientRect().width > 0,
            })).filter(b => b.text);

          // 弹窗容器 HTML
          const cands = Array.from(document.querySelectorAll(
            '[class*="modal"], [class*="dialog"], [class*="auth"], [role="dialog"]'));
          out.modalHtml = cands.slice(0, 3).map(el => el.outerHTML.slice(0, 8000));

          return out;
        }
        """)
    except Exception as exc:  # noqa: BLE001
        data["error"] = str(exc)[:200]

    try:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        f = p / f"verify_dump_{data['generated_at']}.json"
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"认证弹窗结构已 dump: {f}")
        # 顺手把关键信息打到日志，方便当场判断
        d = data.get("detail") or {}
        for t in (d.get("tabs") or [])[:6]:
            log.info(f"  tab: {t.get('text')}  active={t.get('active')}  aria={t.get('selected')}")
        for i in (d.get("inputs") or []):
            if i.get("visible"):
                log.info(f"  输入框: type={i.get('type')} ph='{i.get('placeholder')}' "
                         f"value='{i.get('value')}' near='{i.get('nearText')[:60]}'")
        for b in (d.get("buttons") or [])[:12]:
            if b.get("visible"):
                log.info(f"  按钮: {b.get('text')}")
        return f
    except Exception as exc:  # noqa: BLE001
        log.warning(f"dump 失败: {str(exc)[:80]}")
        return None


def handle_student_verify(session, cfg, log, humanizer=None):
    """自动填 学号 + 姓名 完成「U校园认证」。返回 True 表示已完成/无需完成。"""
    page = session.page
    if not student_modal_present(page):
        return True

    sid = str(cfg.account.get("student_id") or "").strip()
    real_name = str(cfg.account.get("real_name") or "").strip()

    log.info("检测到「U校园认证」弹窗")
    # 先 dump 一份原始结构，便于诊断（tab 状态、学校字段、有无跳过入口）
    dump_verify_dialog(page, cfg.get("output_dir") or "output", log)
    # 先把上次失败留下的报错框关掉
    if "认证失败" in (page.inner_text("body") or ""):
        _dismiss_error_popup(page, log, humanizer)

    # 切到「我是学生」
    try:
        tab = S.first_in(page, STUDENT_TAB, timeout_s=3)
        if tab is not None:
            if humanizer:
                humanizer.click(page, tab, timeout=4000)
            else:
                tab.click(timeout=4000)
            page.wait_for_timeout(800)
            log.info("  已切到「我是学生」")
    except Exception:  # noqa: BLE001
        pass

    if not sid or not real_name:
        log.warning("  config 里没填 account.student_id / real_name，无法自动认证")
        log.warning("  请手动填写学号和姓名，或在 config.yaml 里补上")
        return False

    # 定位两个输入框
    try:
        info = page.evaluate(JS_FIND_VERIFY_FIELDS) or {}
    except Exception:  # noqa: BLE001
        info = {}
    log.debug(f"  认证表单字段信息: {info}")

    ok_sid = _fill_marked(page, "data-upc-sid", sid, log, "学号")
    ok_name = _fill_marked(page, "data-upc-name", real_name, log, "姓名")
    if not (ok_sid and ok_name):
        log.warning("  没能填完认证表单（找不到学号/姓名输入框）")
        return False

    # 勾协议（如果有）
    try:
        from .login import check_agreement
        check_agreement(page, log, auto_check=True)
    except Exception:  # noqa: BLE001
        pass

    # 提交
    for sel in STUDENT_CONFIRM:
        try:
            el = page.query_selector(sel)
            if el is None or not el.is_visible():
                continue
            label = (el.inner_text() or "").strip()[:8]
            if humanizer:
                humanizer.click(page, el, timeout=6000)
            else:
                el.click(timeout=6000)
            log.info(f"  已提交认证（{label}）")
            page.wait_for_timeout(3000)
            break
        except Exception:  # noqa: BLE001
            continue

    # 判断结果
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        text = ""
    if "认证失败" in text:
        log.error("  认证仍然失败。若学号姓名确认无误，说明学校没把你的信息导入U校园，")
        log.error("  需要联系学校教务/管理员导入。这属于后台数据问题，程序无法绕过。")
        dump_verify_dialog(page, cfg.get("output_dir") or "output", log)
        _dismiss_error_popup(page, log, humanizer)
        return False
    if student_modal_present(page):
        log.warning("  认证弹窗还在，可能还在处理中")
        return False
    log.info("  学生认证完成 ✓")
    return True


def _fill_marked(page, attr, value, log, label):
    try:
        el = page.query_selector(f'[{attr}="1"]')
    except Exception:  # noqa: BLE001
        el = None
    if el is None:
        return False
    try:
        el.click(timeout=3000)
        el.fill("")
        el.type(str(value), delay=60)
        log.info(f"  已填写{label}：{value}")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  填写{label}失败: {str(exc)[:70]}")
        return False


def _click_text(page, log, texts, humanizer=None, timeout=6000, exact=False):
    """按可见文字点击，找不到返回 False。

    比 `:has-text` 更宽松：先精确匹配按钮/链接，再退化到任意含该文字的元素，
    并向上找最近的可点祖先（很多页面把文字包在 span 里）。
    """
    for txt in texts:
        # ① 语义化标签优先
        for sel in (f'button:has-text("{txt}")', f'a:has-text("{txt}")',
                    f'[role="tab"]:has-text("{txt}")',
                    f'[role="button"]:has-text("{txt}")'):
            try:
                els = page.query_selector_all(sel)
            except Exception:  # noqa: BLE001
                continue
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    label = (el.inner_text() or "").strip()
                    if exact and label != txt:
                        continue
                    if len(label) > 24:      # 太长说明是容器，不是按钮
                        continue
                    if humanizer:
                        humanizer.click(page, el, timeout=timeout)
                    else:
                        el.click(timeout=timeout)
                    log.info(f"    点击「{txt}」")
                    return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def _click_by_js(page, log, texts, humanizer=None):
    """JS 兜底：找含指定文字的叶子节点，点它或它最近的可点祖先。"""
    try:
        hit = page.evaluate(r"""
        (texts) => {
          const clickable = (el) => {
            let n = el;
            for (let i = 0; i < 5 && n; i++) {
              const tag = (n.tagName || '').toLowerCase();
              const role = n.getAttribute && n.getAttribute('role');
              if (tag === 'button' || tag === 'a' || role === 'button' ||
                  role === 'tab' || (n.onclick) ||
                  (n.className && String(n.className).match(/btn|button|tab|item/i))) {
                return n;
              }
              n = n.parentElement;
            }
            return el;
          };
          const all = Array.from(document.querySelectorAll(
            'button, a, [role="button"], [role="tab"], li, span, div'));
          for (const txt of texts) {
            for (const el of all) {
              if (el.children.length > 0) continue;   // 只要叶子
              const t = (el.innerText || '').trim();
              if (t !== txt) continue;
              const r = el.getBoundingClientRect();
              if (r.width === 0 || r.height === 0) continue;
              const target = clickable(el);
              target.setAttribute('data-upc-portal', '1');
              return txt;
            }
          }
          return '';
        }
        """, texts)
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    try:
        els = page.query_selector_all('[data-upc-portal="1"]')
        el = next((e for e in els if e.is_visible()), None)
        if el is None:
            return False
        if humanizer:
            humanizer.click(page, el, timeout=6000)
        else:
            el.click(timeout=6000)
        log.info(f"    点击「{hit}」（JS 兜底）")
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_any(page, pred, timeout_s=15, interval=0.8):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False


def on_courseware(session):
    """当前是否已经在真正的课程目录页（而不是门户）。"""
    from . import menu as M
    page = session.page
    try:
        url = (page.url or "")
    except Exception:  # noqa: BLE001
        return False
    if not any(h in url for h in CONTENT_HOSTS) and "u.unipus.cn" not in url:
        return False
    if M.menu_present(page):
        return True
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        try:
            if M.menu_present(fr):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def enter_courseware(session, cfg, log, humanizer=None, popup=None, max_rounds=3):
    """从门户导航到课程目录页。返回 True/False。

    配置项（都有默认）：
        course.name_contains  课程名关键词，如 "大学英语听说"
        course.book_contains  教材名关键词，如 "视听说教程1"
    """
    page = session.page
    course_key = (cfg.course.get("name_contains") or "").strip()
    book_key = (cfg.course.get("book_contains") or "").strip()

    # 0) 先过「U校园认证 - 选择学校」，否则后面全是白点
    try:
        handle_school_select(session, cfg, log, humanizer)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"学校认证处理异常（继续尝试导航）: {str(exc)[:100]}")

    # 0.5) 认证弹窗：**先试关掉**（很多情况不认证也能用），关不掉再自动填
    try:
        if student_modal_present(page):
            if not dismiss_verify_modal(page, log, humanizer):
                handle_student_verify(session, cfg, log, humanizer)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"认证弹窗处理异常（继续尝试导航）: {str(exc)[:100]}")

    if on_courseware(session):
        log.info("已经在课程目录页，无需导航")
        return True

    log.info("从门户导航到课程…")
    for rnd in range(1, max_rounds + 1):
        log.info(f"  第 {rnd} 轮导航（{page.url[:70]}）")
        if popup:
            popup.sweep(page, rounds=1)

        # ① 底部「学习」
        if _click_text(page, log, ["学习"], humanizer) or \
                _click_by_js(page, log, ["学习"], humanizer):
            page.wait_for_timeout(2500)

        if on_courseware(session):
            return True

        # ② 「课程」子页
        if _click_text(page, log, ["课程"], humanizer) or \
                _click_by_js(page, log, ["课程"], humanizer):
            page.wait_for_timeout(2500)

        # ③ 选课：先按课程名，再按教材名，都没有就直接找教材卡片
        picked = False
        for key in [k for k in (course_key, book_key) if k]:
            if _click_text(page, log, [key], humanizer):
                page.wait_for_timeout(3000)
                picked = True
                break
        if not picked:
            picked = _click_card_like(page, log, course_key or book_key, humanizer)

        # ④ 「教程学习」
        if _click_text(page, log, ["教程学习"], humanizer) or \
                _click_by_js(page, log, ["教程学习"], humanizer):
            log.info("    已点击「教程学习」，等待课程目录加载…")

        if _wait_any(page, lambda: on_courseware(session), timeout_s=25):
            log.info(f"  已进入课程目录：{page.url[:80]}")
            return True

        # 有些课程点「教程学习」会开新标签页
        try:
            for p in session.context.pages:
                if any(h in (p.url or "") for h in CONTENT_HOSTS):
                    p.bring_to_front()
                    session.page = p
                    page = p
                    if _wait_any(p, lambda: on_courseware(session), timeout_s=20):
                        log.info(f"  新标签页已进入课程目录：{p.url[:80]}")
                        return True
        except Exception:  # noqa: BLE001
            pass

        time.sleep(2)

    log.warning("门户导航失败。可改用更直接的方式：手动点到课程目录页后，")
    log.warning("把该页地址填进 config.yaml 的 course.url，下次直接跳转。")
    return False


def _click_card_like(page, log, key, humanizer=None):
    """教材卡片：文字在多个层级里，按"含关键词且尺寸合理"挑一个点。"""
    try:
        hit = page.evaluate(r"""
        (key) => {
          const all = Array.from(document.querySelectorAll(
            'div, li, a, section, article'));
          const cands = [];
          for (const el of all) {
            const t = (el.innerText || '').trim();
            if (!t || t.length > 200) continue;
            if (key && !t.includes(key)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 60 || r.height < 40) continue;      // 太小不是卡片
            if (r.height > 700) continue;                     // 太大是整页
            cands.push({el, area: r.width * r.height, text: t.slice(0, 40)});
          }
          if (!cands.length) return null;
          // 取面积最小的（最贴近目标文字的卡片）
          cands.sort((a, b) => a.area - b.area);
          cands[0].el.setAttribute('data-upc-card', '1');
          return cands[0].text;
        }
        """, key or "")
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    try:
        el = page.query_selector('[data-upc-card="1"]')
        if el is None or not el.is_visible():
            return False
        if humanizer:
            humanizer.click(page, el, timeout=8000)
        else:
            el.click(timeout=8000)
        log.info(f"    点击卡片「{hit}」")
        page.wait_for_timeout(3000)
        return True
    except Exception:  # noqa: BLE001
        return False
