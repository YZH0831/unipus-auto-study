# -*- coding: utf-8 -*-
"""配置加载：YAML -> 带默认值的配置对象。

所有键都有默认值，config.yaml 里只写你要改的部分即可。
"""
import copy
import sys
from pathlib import Path

import yaml

DEFAULTS = {
    # ------------------------------------------------------------------ 账号
    "account": {
        # 手机号 / 邮箱 / 用户名（U校园统一登录）
        "username": "",
        "password": "",
        # 学校全名，用于过「U校园认证 - 选择学校」弹窗；留空则选列表第一个
        "school": "",
        # 学号 + 真实姓名，用于过「U校园认证 - 学号姓名校验」弹窗
        # 必须与学校上报的学籍库完全一致（多一个空格都会失败）
        "student_id": "",
        "real_name": "",
        # manual   ：打开浏览器后你手动登录一次，程序记住登录态（推荐，最稳）
        # password ：用上面的账号密码自动填表（有图形验证码时仍需手动完成）
        "login_method": "manual",
    },

    # ---------------------------------------------------------------- 浏览器
    "browser": {
        "channel": "auto",          # auto | msedge | chrome | chromium
        "headless": False,          # 刷时长必须保持窗口可见，否则平台可能不计时
        "slow_mo": 50,
        "window_width": 1600,
        "window_height": 900,
        "page_timeout_ms": 45000,
        "nav_retry": 3,
        # launch：程序自己启动浏览器（用 state/storage_state.json 复用登录态）
        # attach：接管你已开着的浏览器（需先带 --remote-debugging-port=9222 启动）
        "mode": "launch",
        "cdp_url": "http://127.0.0.1:9222",
        # 伪麦克风：口语题需要。默认开启，录音内容为静音（能过流程，分数看平台）
        "fake_media": True,
        "fake_audio_wav": "",       # 可选：指定一个 wav 让伪麦克风播放它
    },

    # ------------------------------------------------------------------ 登录
    "login": {
        "reuse_state": True,        # 有登录态就先复用
        "manual_wait_s": 300,       # 手动登录最长等待（秒）
        "nav_timeout_ms": 60000,
        "max_retry": 3,
        # 掉线自愈
        "auto_relogin": True,
        "check_interval_s": 90,     # 会话健康探测间隔
        "relogin_max_attempts": 2,
        "net_retry_delays_s": [15, 30, 60, 120, 300],
    },

    # ------------------------------------------------------------------ 课程
    "course": {
        # 直接粘课程学习页地址最省事，例如
        # https://ucontent.unipus.cn/#/course/xxxx
        "url": "",
        "name_contains": "",        # 不填 url 时，按课程名关键词在"我的课程"里找
        "book_contains": "",        # 教材名关键词，如 "视听说教程1"
        # 自动从门户导航：u.unipus.cn → 学习 → 课程 → 教材 → 教程学习
        # true 时即使填了 url 也会先试导航（导航失败再回落到 url）
        "auto_enter": True,
        "entry_wait_s": 25,         # 等待课程列表渲染
        # 只跑指定单元：书上是 Unit 1~8 就写 "1-3,5"
        "units": "",
        "pick_interactively": False,  # 运行时在控制台勾选单元
        "skip_no_task": True,       # 没有 Tab/Task 的目录项直接跳过
    },

    # -------------------------------------------------------------- 刷时长
    "duration": {
        # total     ：把 total_minutes 平均分给所有 目录×Tab×Task（推荐，等效油猴脚本）
        # per_item  ：每个 Task 固定停 per_item_seconds 秒
        # remaining ：读平台显示的"还需学习 X 分钟"，逐个单元刷到满足（需页面支持）
        "mode": "total",
        "total_minutes": 60,        # mode=total 时的总时长
        "per_item_seconds": 90,     # mode=per_item 时的单点秒数
        "min_per_item_s": 8,        # 均分后单点时长下限，太短会被判无效
        "max_per_item_s": 1800,     # 单点时长上限，防止配置写错卡死
        "probe_samples": 3,         # total 模式下抽样几个目录项来估算 Tab/Task 数（0=全量探测，很慢）
        "tick_s": 1,                # 计时粒度
        # 视频播放期间是否暂停计时（对齐平台规则：视频时长与学习时长分开算）
        "pause_on_video": True,
        # 切页后额外停留，让平台的心跳/上报有机会送达
        "settle_after_switch_s": 4,
        # 计时期间的动作频率（毫秒级占位）
        "activity_move_every_s": 45,   # 每 N 秒动一下鼠标，避免判挂机
        "activity_scroll_every_s": 120,
    },

    # ------------------------------------------------------------------ 视频
    "video": {
        "enabled": True,
        "speed": 1.0,               # 平台若检测倍速会不计数；不确定就保持 1.0
        "wait_end": True,           # 等视频播完再走
        "timeout_per_video_min": 60,
        "poll_interval_s": 2,
        "mute": True,               # 静音可绕过浏览器自动播放限制
        "retry_play": 3,
    },

    # ------------------------------------------------------- 单元测试 / 答题
    "quiz": {
        "enabled": True,
        # sniff   ：试答→嗅探接口返回的正确答案→回填重答（无需题库，命中率高）
        # cache   ：只用本地题库，不试答（最保守，不会占一次作答记录）
        # llm     ：直接调大模型作答
        # hybrid  ：cache → llm → sniff 逐级回退
        "strategy": "hybrid",
        "two_pass": True,           # 第一遍交卷拿答案，第二遍按正确答案重做
        "max_questions": 0,         # 0=不限
        "answer_delay_ms": [400, 1200],
        "submit_confirm": True,     # 点提交后若弹确认框自动确定
        "skip_speaking": False,     # 跳过口语题（交给 speaking 模块）
        "answers_file": "data/answers.json",
        # ---- 大模型（OpenAI 兼容）----
        "llm": {
            "enabled": False,
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
            "timeout_s": 30,
            "temperature": 0,
            "max_calls": 200,
        },
        # ---- 第三方题库（可选）----
        "tiku": {
            "enabled": False,
            "timeout_s": 12,
            "max_calls": 300,
            "providers": [
                # {"type": "icodef", "token": "你的token"},
                # {"type": "tikuadapter", "url": "http://127.0.0.1:8060"},
            ],
        },
    },

    # ------------------------------------------------------------ 口语/录音
    "speaking": {
        "enabled": False,           # 默认关闭：成功率受平台评分逻辑影响大
        "record_seconds": 6,        # 每次录音时长
        "max_items": 20,            # 单次运行最多处理几个口语题
        "submit": True,             # 录完是否自动提交
        "wait_after_submit_s": 3,
    },

    # -------------------------------------------------------------- 拟人化
    "humanize": {
        "enabled": True,
        "click_before_ms": [200, 900],
        "click_after_ms": [300, 1200],
        "mouse_steps": [8, 20],
        "scroll_times": [1, 3],
        "scroll_step_px": [200, 600],
        "long_pause_every": 6,
        "long_pause_s": [15, 45],
    },

    # ---------------------------------------------------------------- 安全
    "safety": {
        "max_runtime_min": 0,       # 0=不限
        "max_items": 0,             # 单次最多处理多少个 Task，0=不限
        "min_task_interval_s": 3,
        "stop_on_risk": False,      # 遇风控/验证页是否直接停
        "retry_rounds": 1,          # 未成功的目录项在末尾重试几轮
        "confirm_before_run": False,
    },

    # ---------------------------------------------------------------- 弹窗
    "popup": {
        "auto_close": True,
        "check_interval_s": 5,
    },

    # ---------------------------------------------------------------- 路径
    "output_dir": "output",
    "state_dir": "state",
    "log_dir": "logs",
    "log_level": "INFO",
}


class Config(dict):
    """支持属性访问的字典：cfg.account.username。"""

    def __getattr__(self, item):
        try:
            value = self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc
        if isinstance(value, dict) and not isinstance(value, Config):
            value = Config(value)
            self[item] = value
        return value

    def __setattr__(self, key, value):
        self[key] = value


def _deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_relative(cfg, base):
    """把相对目录/文件路径解析到配置文件所在目录，保证在任意工作目录下运行都一致。"""
    for key in ("output_dir", "state_dir", "log_dir"):
        p = Path(str(cfg[key]))
        if not p.is_absolute():
            cfg[key] = str((base / p).resolve())
    p = Path(str(cfg["quiz"]["answers_file"]))
    if not p.is_absolute():
        cfg["quiz"]["answers_file"] = str((base / p).resolve())
    wav = str(cfg["browser"].get("fake_audio_wav") or "").strip()
    if wav:
        wp = Path(wav)
        if not wp.is_absolute():
            cfg["browser"]["fake_audio_wav"] = str((base / wp).resolve())


def load_config(path, strict=True):
    """读取配置。strict=False 时允许缺失文件（用全默认值，供 doctor/probe 使用）。"""
    cfg_path = Path(path)
    if not cfg_path.exists():
        if strict:
            sys.exit(f"[配置错误] 配置文件不存在: {cfg_path.resolve()}")
        return Config(_deep_merge(DEFAULTS, {}))

    user_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = Config(_deep_merge(DEFAULTS, user_cfg))
    _resolve_relative(cfg, cfg_path.resolve().parent)

    if not strict:
        return cfg

    # ---- 校验 ----
    method = cfg.account.login_method
    if method not in ("manual", "password"):
        sys.exit(f"[配置错误] 不支持的 login_method: {method}（可选 manual / password）")
    if method == "password" and not cfg.account.username:
        sys.exit("[配置错误] login_method=password 时必须填写 account.username 和 account.password")
    auto = bool(cfg.course.get("auto_enter", True))
    if not cfg.course.url and not cfg.course.name_contains and not auto:
        sys.exit("[配置错误] 请至少填写 course.url（课程地址）或 course.name_contains（课程名关键词），"
                 "或开启 course.auto_enter 让程序自动导航")

    d = cfg.duration
    if d.mode not in ("total", "per_item", "remaining"):
        sys.exit(f"[配置错误] 不支持的 duration.mode: {d.mode}（可选 total / per_item / remaining）")
    if d.mode == "total" and float(d.total_minutes) <= 0:
        sys.exit("[配置错误] duration.mode=total 时 duration.total_minutes 必须 > 0")
    if d.mode == "per_item" and float(d.per_item_seconds) <= 0:
        sys.exit("[配置错误] duration.mode=per_item 时 duration.per_item_seconds 必须 > 0")

    if cfg.quiz.strategy not in ("sniff", "cache", "llm", "hybrid"):
        sys.exit(f"[配置错误] 不支持的 quiz.strategy: {cfg.quiz.strategy}")
    if cfg.browser.mode not in ("launch", "attach"):
        sys.exit(f"[配置错误] 不支持的 browser.mode: {cfg.browser.mode}")
    if cfg.browser.mode == "attach" and not cfg.browser.cdp_url:
        sys.exit("[配置错误] browser.mode=attach 时必须填写 browser.cdp_url")
    return cfg
