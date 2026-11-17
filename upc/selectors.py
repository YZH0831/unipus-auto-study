# -*- coding: utf-8 -*-
"""U校园 DOM 选择器注册表（集中管理，便于标定）。

设计原则
--------
1. **一切可疑选择器都写成"有序回退链"**：从最精确的写到最宽松的。
   平台改版时通常不会一次全废，回退链能扛住大部分小改。
2. **不要在这里硬编码单一 class**。U校园用了 CSS Modules
   （类名带哈希后缀，如 `menu--u3menu-3Xu4h`），必须用 `[class*="..."]` 包含匹配。
3. 校准入口：`tools/probe.py` 会把当前页面的真实结构 dump 成 JSON，
   你可以据此增删本文件里的候选，而不必改任何业务逻辑。

选择器来源
----------
基于 U校园 AI版/智慧版（ucontent.unipus.cn + ipub.unipus.cn）公开脚本实测选择器，
并叠加了通用回退（antd 树 / ARIA role / 文本匹配）以兼容其它课程结构。
"""

# =========================================================================
# 站点
# =========================================================================
HOST_CONTENT = "ucontent.unipus.cn"      # 课程学习主站（目录、Tab、Task 在这一层）
HOST_IPUB = "ipub.unipus.cn"             # 课件站（iframe 内部，视频/题目在这一层）
HOST_MAIN = "u.unipus.cn"                # 统一登录入口
HOST_UAI = "uai.unipus.cn"               # AI 版首页

HOME_URL = "https://u.unipus.cn/"
CONTENT_HOME = "https://ucontent.unipus.cn/"

# 直接进 SSO 登录页（实测 HTTP 200 直出登录表单；
# 而 u.unipus.cn 是 301 跳到宣传落地页，容易让用户不知道该点哪儿）
SSO_LOGIN_URL = "https://sso.unipus.cn/sso/login?service=https%3A%2F%2Fu.unipus.cn%2F"

# 会话健康探测：登录失效时会被重定向到这些地方
LOGIN_URL_HINTS = (
    "passport", "/login", "signin", "sso", "cas", "authserver",
)
LOGIN_HOST_HINTS = ("u.unipus.cn", "passport.unipus.cn", "sso.unipus.cn")

# =========================================================================
# 目录树容器（左侧/右侧滑出菜单）
# =========================================================================
MENU_CONTAINER = [
    ".pc-slider-menu-container.show .pc-slider-content-menu",
    ".pc-slier-menu-container.show .pc-slider-content-menu",   # 平台自身拼写错误，保留
    ".pc-slider-menu-container .pc-slider-content-menu",
    ".pc-slier-menu-container .pc-slider-content-menu",
    "#part-menu-view .pc-slider-content-menu",
    "#part-menu-view .ant-tree",
    "#part-menu-view",
    ".pc-slider-content-menu",
    ".ant-tree",
    '[role="tree"]',
    ".ant-menu",
    ".menuRightTabContent",
]

# AI 版课本菜单（CSS Modules）
MENU_CONTAINER_U3 = [
    'ul[class*="menu--u3menu"]',
    'ul[class*="u3menu"]',
]

# ---- 目录节点（层级结构）----
NODE_UNIT = [".pc-slider-menu-unit"]                     # 单元
NODE_SECTION = [".pc-slider-menu-section"]               # 小节
NODE_MICRO = [".pc-slider-menu-micro"]                   # 叶子（微课）
NODE_PLAIN = [".pc-slider-menu-node"]                    # 普通节点 / 非 section 内

UNIT_LABEL = [".unit-label-item", '[class*="unit-label"]']
NODE_NAME = [".pc-menu-node-name", '[class*="menu-node-name"]']

# AI 版课本节点
NODE_U3_UNIT = ["li.unit"]
NODE_U3_GROUP = ["li.group.courseware"]
NODE_U3_LINK = ["span.name a", "a"]

# ---- 节点内"可点击"元素（按优先级）----
NODE_CLICKABLE = [
    ".pc-menu-node-name",
    ".ant-tree-node-content-wrapper",
    ".ant-menu-title-content",
    'a[role="button"]',
    "span.name a",
    "a",
    "span",
]

# ---- 通用树（antd tree / ARIA）----
TREE_GENERIC = [
    ".ant-tree",
    '[role="tree"]',
]
TREE_ITEM = [
    ".ant-tree-treenode",
    '[role="treeitem"]',
    ".ant-menu-item",
    ".ant-menu-submenu-title",
    'li[role="menuitem"]',
]

# 目录是否已加载的"存在性"探针（任一命中即认为有目录）
MENU_PROBE = [
    ".pc-slider-menu-unit",
    ".pc-slider-menu-micro",
    ".pc-slider-menu-node",
    ".ant-tree-treenode",
    '[role="treeitem"]',
    '[class*="tree-menu"]',
    'ul[class*="u3menu"]',
]

# 展开/收起目录的开关（有些课程默认收起）
MENU_TOGGLE = [
    '[class*="slider-menu"] [class*="toggle"]',
    '[class*="menu-toggle"]',
    '[class*="collapse-btn"]',
    '[class*="sidebar-toggle"]',
    'button[aria-label*="目录"]',
    '[title*="目录"]',
    ".pc-slider-menu-container [class*='arrow']",
]

# =========================================================================
# 顶部 Tab / Task
# =========================================================================
TAB_CONTAINER = [
    ".pc-header-tabs-container",
    '[class*="header-tabs"]',
    '[class*="TabsBox"]',
    ".ant-tabs-nav",
]

TAB_ITEM = [
    ".pc-header-tabs-container .ant-col",
    ".pc-tab-view-container",
    ".ant-tabs-tab",
    "ul.TabsBox a.topTab",
    'ul[class*="TabsBox"] a[class*="topTab"]',
    '[role="tab"]',
]

# Tab 激活态判定（类名含有即视为选中）
TAB_ACTIVE_CLASSES = ("ant-tabs-tab-active", "active", "current", "selected")

TASK_ROW = [
    ".pc-header-tasks-row",
    '[class*="header-tasks"]',
    '[class*="task-row"]',
]

TASK_ITEM = [
    ".pc-header-tasks-row .pc-task",
    ".pc-task",
    '[class*="task-item"]',
    '[class*="taskItem"]',
]

TASK_ACTIVE_CLASSES = ("active", "pc-task-active", "current", "selected")

# Tab/Task 出现等待超时（毫秒）
TAB_APPEAR_TIMEOUT_MS = 8000
TASK_APPEAR_TIMEOUT_MS = 5000

# =========================================================================
# iframe / 课件
# =========================================================================
IFRAME_COURSEWARE = [
    "#ipublish-pc-book-easy-iframe",
    "iframe.ipublish-pc-iframe-container",
    'iframe[id*="ipublish"]',
    'iframe[id*="iframe"]',
    "iframe",
]

# 课件 iframe 内，内容是否已就绪的探针
COURSEWARE_READY = [
    "video",
    "audio",
    '[class*="question"]',
    '[class*="content"]',
    '[class*="page"]',
    "canvas",
]

# =========================================================================
# 弹窗 / 提示
# =========================================================================
POPUP_BUTTONS = [
    ".know-box .iKnow",
    ".ant-modal-confirm-btns .ant-btn-primary.system-info-cloud-ok-button",
    ".ant-modal-confirm-btns .ant-btn.ant-btn-primary",
    ".ipublish-modal-footer-ok",
    "button.ant-btn.ant-btn-default.ipublish-modal-footer-ok",
    ".ant-modal-close",
]

# 文本兜底：按钮文字命中即点（仅在这些标签上找）
POPUP_TEXT_TAGS = "button, .btn, a[role='button'], span[role='button'], div[role='button']"
POPUP_TEXTS = (
    "我知道了", "知道了", "确定", "确认", "好的", "继续", "关闭",
    "I know", "OK", "Confirm",
)

# 遮罩类（用于判断弹窗是否真的挡在前面）
POPUP_MASK = [
    ".ant-modal-mask",
    ".ant-modal-wrap",
    '[class*="modal-mask"]',
    '[class*="overlay"]',
]

# =========================================================================
# 视频 / 音频
# =========================================================================
VIDEO_ELEMENT = ["video.vjs-tech", "video", ".video-js video"]
AUDIO_ELEMENT = ["audio"]

VIDEO_CONTAINER = [".video-js", '[class*="video-player"]', '[class*="player"]']

# 被禁用/遮挡时移除的类（U校园播放器会挂 controls-disabled 阻止 JS 控制）
VIDEO_UNLOCK_CLASSES = ("vjs-controls-disabled", "vjs-user-inactive")

VIDEO_PLAY_BUTTON = [
    ".vjs-big-play-button",
    '[class*="big-play"]',
    '[class*="play-btn"]',
    '[class*="playBtn"]',
    "[aria-label*='播放']",
    ".vjs-play-control",
]

# 平台"必须真实观看/禁止倍速"的文案特征
NOSPEED_HINTS = (
    "禁止倍速", "不可快进", "不允许倍速", "不得快进", "不可拖拽",
    "需观看", "观看时长", "累计观看", "不可离开", "不可最小化",
    "防作弊", "拖动进度条", "无效",
)

# 播放器的可拖动进度条（用于判断是否被禁用）
PROGRESS_BAR = [".vjs-progress-control", '[class*="progress"]']

# =========================================================================
# 题目（单元测试 / 作业）
# =========================================================================
QUESTION_BLOCK = [
    '[class*="question-item"]',
    '[class*="questionItem"]',
    '[class*="question"]',
    ".item-content",
    ".problem-content",
    ".ant-form-item",
]

QUESTION_STEM = [
    ".question-content",
    ".question-text",
    ".problem-content",
    ".item-content",
    ".stem",
    '[class*="stem"]',
    '[class*="title"]',
]

OPTION_LABEL = [
    '[class*="option"] label',
    '[class*="option"]',
    "label.ant-radio-wrapper",
    "label.ant-checkbox-wrapper",
    ".ant-radio-wrapper",
    ".ant-checkbox-wrapper",
    "label",
]

OPTION_TEXT = [".option-text", '[class*="option-content"]', "span"]

RADIO_INPUT = 'input[type="radio"]'
CHECKBOX_INPUT = 'input[type="checkbox"]'
TEXT_INPUT = ['input[type="text"]', 'input:not([type])', "textarea"]
TEXTAREA = ["textarea"]

SUBMIT_BUTTON = [
    "button:has-text('提交')",
    "button:has-text('交卷')",
    "button:has-text('Submit')",
    ".ant-btn-primary:has-text('提交')",
    '[class*="submit"]',
    'button[type="submit"]',
]

NEXT_BUTTON = [
    "button:has-text('下一题')",
    "button:has-text('下一步')",
    "button:has-text('Next')",
    '[class*="next"]',
]

# 提交后反馈区（正确答案通常出现在这里）
FEEDBACK_AREA = [
    ".correct-answer",
    ".answer-display",
    ".result-content",
    ".feedback",
    ".explanation",
    '[class*="correct"]',
    '[class*="answer"]',
    '[class*="result"]',
    '[class*="analysis"]',
]

# 页面级"正确答案：X"类文本
ANSWER_TEXT_HINTS = (
    "正确答案", "参考答案", "标准答案", "答案", "解析",
)

# =========================================================================
# 口语 / 录音
# =========================================================================
RECORD_BUTTON = [
    "button:has-text('录音')",
    "button:has-text('开始录音')",
    "button:has-text('跟读')",
    "button:has-text('朗读')",
    "button:has-text('Record')",
    '[class*="record"]',
    '[class*="recordBtn"]',
    '[class*="mic"]',
    '[aria-label*="录音"]',
]

STOP_RECORD_BUTTON = [
    "button:has-text('停止')",
    "button:has-text('结束录音')",
    "button:has-text('Stop')",
    '[class*="stop-record"]',
]

RECORD_SUBMIT = [
    "button:has-text('提交')",
    "button:has-text('上传')",
    "button:has-text('确认')",
    '[class*="submit"]',
]

# 麦克风被拒绝/需要授权的提示
MIC_DENIED_HINTS = ("麦克风", "录音权限", "microphone", "允许", "权限")

# =========================================================================
# 工具函数
# =========================================================================


def first_in(scope, selectors, timeout_s=0.0, require_visible=True):
    """在 scope（Page 或 Frame）里按顺序尝试选择器，返回第一个命中的元素。

    timeout_s > 0 时会在多个选择器之间做总时长等待（轮询），
    适合"元素稍后才出现"的场景；默认 0 表示只做一次瞬时探测。
    """
    if not selectors:
        return None
    import time as _t

    deadline = _t.time() + max(0.0, float(timeout_s))
    while True:
        for sel in selectors:
            try:
                els = scope.query_selector_all(sel)
            except Exception:  # noqa: BLE001  选择器语法不被支持
                continue
            for el in els:
                try:
                    if require_visible and not el.is_visible():
                        continue
                    return el
                except Exception:  # noqa: BLE001
                    continue
        if _t.time() >= deadline:
            return None
        _t.sleep(0.25)


def count_in(scope, selectors):
    """统计命中元素数量（用第一个能命中的选择器）。"""
    for sel in selectors:
        try:
            n = len(scope.query_selector_all(sel))
        except Exception:  # noqa: BLE001
            continue
        if n:
            return n
    return 0


def all_in(scope, selectors):
    """返回第一个能命中的选择器的全部元素列表。"""
    for sel in selectors:
        try:
            els = scope.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        if els:
            return els
    return []


def has_any_text(scope, hints):
    """页面（或 frame）正文里是否含任一关键词。"""
    try:
        text = scope.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        return False
    text = text.replace(" ", "")
    return any(h.replace(" ", "") in text for h in hints)
