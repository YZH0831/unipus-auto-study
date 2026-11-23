# U校园自动化助手（unipus-auto-study）

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-v1.1.0-orange)

> 用 **真实浏览器**（Playwright 驱动本机 Edge）完成 U校园（Unipus）课程的
> **学习时长、视频/音频、单元测试、口语录音**，带 **AI 版两遍满分答题**
> （交卷后解密判题接口拿标准答案）、**多策略选择器回退**、
> **环境自检 / DOM 探针 / 密钥图形设置 / 断点续跑**等工程配套。

当前版本 **v1.1.0**｜平台 Windows 10/11｜许可 MIT
GitHub 仓库：https://github.com/YZH0831/unipus-auto-study

> **EN** · Automate Unipus (U校园) courses with a real browser (Playwright + Edge):
> study time, video/audio, unit quizzes and speaking recordings, with AI answering,
> resume-from-checkpoint and a local control panel. Windows, MIT.

---

## 一、它解决什么问题

U校园最耗人的不是"学不会"，而是这三件事：

1. **学习时长必须真实累积**。平台按页面停留计时，挂着不动的窗口会被判无效，
   一个单元一堆 Micro/Tab/Task，挨个点够时间是纯体力活。
2. **单元测试要一道道点**。选择题、填空题、听力题混着来，还限时。
3. **口语跟读要一段段录**。流程繁琐，且分数依赖语音识别，机器帮不上忙。

本项目的做法：**真实浏览器 + 真实点击 + 真实停留**，把"人已经做过的事"自动化，
不构造平台接口载荷，也不伪造学习记录。

## 二、功能

| 模块 | 能力 |
|---|---|
| 登录 | 手动登录一次后长期复用登录态；掉线自愈；可选账号密码自动填表 |
| 目录解析 | Unit → Section → Micro 三层树自动展开识别；多套选择器回退 |
| 刷时长 | 三种模式：总时长均分 / 单点固定 / 按平台剩余时长补足 |
| 视频音频 | 自动播放、解锁播放器控制、可选倍速、等播完再走、卡顿自愈 |
| 单元测试 | 本地题库 → 大模型 → 试答嗅探 → 兜底，**任何情况不留空** |
| 口语录音 | 伪麦克风走完录音→提交流程（分数看平台，不保证） |
| 反风控 | 贝塞尔鼠标轨迹、随机延时、随机滚动、长暂停、节奏抖动 |
| 可观测 | 逐项日志、Markdown+JSON 报告、进度落盘可续跑、日志自动脱敏 |
| 工程化 | 环境自检 `doctor`、**DOM 探针** `probe`、密钥图形界面、密钥扫描、打包 |

## 三、安装

### 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（64 位） |
| Python | **3.10+**（安装时勾选 `Add python.exe to PATH`） |
| 浏览器 | Microsoft Edge（系统自带）或 Chrome。程序**不下载**额外浏览器，直接用你本机装的 |
| 网络 | 能访问 `u.unipus.cn` / `ucontent.unipus.cn` |

### 步骤

```
1. 把整个 unipus-auto-study 文件夹放到一个不会被误删的位置
2. 双击 setup.bat          ← 自动建 .venv + 装依赖 + 环境自检
3. 双击 start.bat          ← 打开菜单
```

国内镜像已内置三级回退（阿里云 → 腾讯云 → 官方 PyPI），无需手动配镜像。

## 四、第一次使用

```
① 双击 start.bat
② 选 [9] 编辑配置，把课程地址粘到 course.url
       （浏览器打开你的课程 → 复制地址栏 → 形如
        https://ucontent.unipus.cn/#/course/xxxx）
③ 选 [1] 试运行 —— 只扫描目录，验证能不能识别到 Unit/Task
④ 能识别了，再选 [2] 正式运行
```

首次运行会弹出浏览器窗口，**在里面登录一次**，之后靠 `state/storage_state.json` 复用。

## 五、配置要点

全部配置项见 `config.yaml`（每项都有中文注释）。最常改的四个：

```yaml
course:
  url: "https://ucontent.unipus.cn/#/course/你的课程id"   # ★ 必填

duration:
  mode: total          # total / per_item / remaining
  total_minutes: 60    # 本次一共刷多少分钟

quiz:
  strategy: hybrid     # hybrid / sniff / cache / llm

video:
  speed: 1.0           # 平台可能检测倍速，不确定就保持 1.0
```

### 时长三种模式怎么选

| 模式 | 含义 | 适合 |
|---|---|---|
| `total` | 总分钟数平均分给所有 目录×Tab×Task | 常规，可预测（推荐） |
| `per_item` | 每个 Task 固定停 N 秒 | 想精确控制单点节奏 |
| `remaining` | 读平台"还需学习 X 分钟"，刷到满足为止 | 只想把差的那点补齐 |

### 答题四种策略怎么选

| 策略 | 做法 | 风险 |
|---|---|---|
| `cache` | 只查本地题库 | 最保守，题库空时全走兜底 |
| `llm` | 直接问大模型 | 需 API Key，准确率取决于模型 |
| `sniff` | 先随便答→交卷→从返回数据里捞正确答案→重做 | **会消耗一次作答记录** |
| `hybrid` | cache → llm → 兜底 | 最实用，不消耗作答次数（默认） |

想用大模型：菜单按 `K`，图形界面里填 Key，点「测试连通性」再保存。
支持 DeepSeek / Kimi / 智谱 / 通义 / 本地 Ollama / 任意 OpenAI 兼容网关。

## 六、识别不到东西怎么办（重要）

U校园不同课程、不同学期的 DOM 结构会变。程序的应对是**多套选择器回退链**，
但总有踩不到的情况。这时候用 **DOM 探针**：

```
start.bat → 按 [P]
```

它会：
1. 打开浏览器，你手动进到课程学习页
2. 打印每组选择器命中了几个元素（一眼看出哪条链断了）
3. 列出所有 iframe、目录节点、Tab、Task 的真实 class 名
4. 结果存到 `output/probe_*.json` 和 `output/probe_*.html`

拿到这两个文件，改 `upc/selectors.py` 里对应的选择器列表即可——
**所有选择器都集中在那一个文件里，业务逻辑不用动**。

## 七、工程结构

```
unipus-auto-study/
├── main.py                 入口 / CLI
├── config.yaml             配置
├── upc/
│   ├── selectors.py        ★ 所有 DOM 选择器（校准只改这里）
│   ├── config.py           YAML 配置 + 默认值 + 校验
│   ├── browser.py          浏览器会话 / 登录态 / 健康探测
│   ├── login.py            登录 / 重登
│   ├── menu.py             目录树解析（Unit/Section/Micro）
│   ├── tabs.py             Tab / Task 枚举与点击
│   ├── dwell.py            ★ 刷时长计时引擎
│   ├── video.py            视频音频播放
│   ├── quiz.py             ★ 单元测试答题
│   ├── sniff.py            ★ 响应嗅探（试答提取答案）
│   ├── llm.py              大模型客户端
│   ├── speaking.py         口语录音
│   ├── humanize.py         拟人化
│   ├── popups.py           弹窗处理
│   ├── report.py           报告
│   └── runner.py           总编排
├── tools/
│   ├── doctor.py           环境自检
│   ├── probe.py            ★ DOM 探针
│   ├── set_key_gui.py      密钥图形界面
│   ├── check_secrets.py    提交前密钥扫描
│   └── package.py          打包
├── tests/
│   ├── test_offline.py     18 项离线单测（不联网不开浏览器）
│   └── smoke_browser.py    浏览器连通性冒烟测试
├── setup.bat / start.bat / run.bat
└── data/ state/ logs/ output/
```

## 八、自测

```bat
.venv\Scripts\python.exe tests\test_offline.py     :: 离线单测
.venv\Scripts\python.exe tests\smoke_browser.py    :: 浏览器冒烟
.venv\Scripts\python.exe tools\doctor.py           :: 环境自检
```

## 九、已知限制（诚实版）

1. **口语题拿不到分**。平台会把录音做语音识别跟原文比对，伪麦克风只能把流程走完。
2. **倍速可能被检测**。平台若判定非真实观看，学习时长可能不计。默认 1.0 最稳。
3. **`sniff` 策略会消耗一次作答**。第二次用正确答案重做，但痕迹留在平台记录里。
4. **选择器依赖页面结构**。平台改版就会失效，用 `probe` 重校准。这是这类工具的通病。
5. **不并发多账号**，无服务端调度。
6. **必须在可见窗口里跑**。最小化可能导致平台不计学习时长。

## 十、免责声明

本工具仅供**个人学习效率辅助**使用。使用前请确认不违反你所在学校的
课程考核规定与 U校园 用户协议。因使用本工具产生的一切后果由使用者自负。
作者不对账号状态、学习成绩、学术诚信认定承担任何责任。
