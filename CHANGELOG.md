# 更新日志

## v1.1.6 — 2026-11-25

修掉两个"被判做不了"的题型：Review & check 自评表、Collocation 下拉选词。

### 修复 1：下拉选词 0/6 全失败（真 bug）

`upc/scoop.py` 的 `JS_PICK_SCOOP` 写成 `(idx, answer) =>`，
而 `page.evaluate()` **只接受一个参数**，于是每个空都抛
`Page.evaluate() takes from 2 to 3 positional arguments but 4`，
被 `except` 吞成 debug 日志 → 整题判"未识别题型"→ 三振出局。

- 改成解构单参数 `([idx, answer]) =>`，调用处传 `[idx, ans]`；
- 补上 antd Dropdown 的 **hover 触发**（原来只 click，弹不出来）；
- 候选池选择器补 `.scoop-select-wrapper p / i`（隐藏池要跳过
  `visibility:hidden`）。

实测：同一页从 **0/6 → 5/6** 个空选上，仍差 1 个空（该空平台侧另有校验），
已单独标 needs_manual，不再每轮白跑。

### 修复 2：自评表 7 行一行都没点（真 bug）

Review & check 是 ant-table：表头 Got it / Review，每行两个圆圈。
**未选中的图标 class 也是蓝色的 `anticon-border primary-color`**，
原判定用 `/primary-color/` 当"已选"，于是 7 行全被跳过、直接标记已看。

- 已选判定改为 `anticon-check && !anticon-border`；
- 逐行点第 2 列（Got it），点完复查选中数。

实测：`自评表：7 行 / 本次点 7 个 / 已选 7 个` → 该叶子 pass
（读写教程已完成 162 → 164）。

### 测试

- 新增 `tests/test_selfcheck_scoop.py`（6 项：单参数解构、调用打包、
  hover 触发、候选池覆盖、primary-color 陷阱、接入顺序）；
  全量 71 项离线测试全绿。

## v1.1.5 — 2026-11-25

讨论区「真发帖」尝试 + 修掉"日志停住不动"的隐性卡死。

### 结论先说（重要，别再重复排查）

**U校园 PC 端 Discussion 页的「发表」按钮是死的**。实测三种点击方式
（JS `dispatchEvent`、Playwright 真实鼠标、先点「发表回复」再点「发表」）
全部 **0 个网络请求**，输入框内容确实在（`.dis-content` 已从
`dis-dis-content` 切过来，101 字符，键盘逐字输入也试过），点完内容不消失、
不弹提示、不发请求。同一页面每次打开都弹「请检查网络」，与之一致。

所以：只做「标记已看」时叶子 `pass=1`，但所属节的圈永远不蓝；
这不是播放方式问题（34 次真播重做 0 成功），也不是程序能力问题，
而是 PC 端功能不可用——只能手机 App 手动发一帖。

### 新增

- `--quiz-discussion`：讨论页真发帖开关（默认关）。实现为
  「键盘逐字重打内容 → JS 派发点击 → 真实点击」的顺序尝试，
  任一成功即以 `code=0` 或「输入框被清空」判定成功。
  平台哪天修好这个按钮，加这个开关就能直接用。
- `UPC_PROBE_DISC=1`：发帖抓包/诊断模式，落盘
  `output/disc_probe_<leaf>.json` 并打印点击前后的按钮属性、输入框状态。

### 修复

- **「日志停住、进程不死也不动」**：点过按钮后页面会进入 JS 忙状态，
  此时 `page.evaluate` 会永久挂起（Playwright 的 evaluate 没有超时参数）。
  `upc/progress.py` 的 `scan()` 改为先 `wait_for_function` 探活（4s 超时）
  再求值，探不通就当"判断不出来"返回空；`_dwell()` 每 8 秒探活，
  卡住就刷新一次自救。之前每处理一个叶子白卡 2~3 分钟，现在最多 4 秒。

## v1.1.4 — 2026-11-25

未完成清单 `--leftover` + 上传作品题（Unit project）全自动。

### 新增

- `tools/study_ai.py --leftover`：只读扫一遍，把 281 个叶子分成
  **已完成 / 待做（程序能做）/ 只能 App / 需上传作品 / 题型不支持**，
  落盘 `output/leftover.md` 并给出建议命令。收尾时照着单子跑就行。
- `upc/upload.py` + `upc/pdfgen.py`：Unit project 这类「拖文件才算作答」
  的页面现在能全自动——大模型按题干写一篇英文作品 → 零依赖打成 PDF
  → `set_input_files` 塞进隐藏 `input[type=file]` → 轮询等列表项 → 再提交。
  `pdfgen` 是手写的 Helvetica/A4 单字体 PDF 生成器，**不新增任何依赖**。

### 修复

- 「需上传作品」以前被判死后永久跳过（`needs_upload` 在 7 天 TTL 内连页面
  都不打开），其实平台只校验有没有文件，程序完全能代劳。
- 实测 Unit 1 Unit project：作品 1382 字符 → PDF → 2s 传完 → 点提交
  → 平台回「成功」→ 该叶子 pass（本轮 162→163）。

### 测试

- 新增 `tests/test_upload_flow.py`（9 项：PDF 骨架/分页/转义、detect 三态、
  投递成功、已有作品不重复投递、非上传题返回 None、接入点在提交之前）；
  既有 `test_offline` 29/29、`test_selfheal_fix` 10/10、
  `test_voice_score_report` 6/6、`test_retry_burn` 5/5 通过。

## v1.1.3 — 2026-11-25

不再对「真播重做也点不亮」的节白耗时长（`tools/study_ai.py`、`upc/runstate.py`）。

### 问题

用户反馈「界面卡住不动」。实际是主流程的自适应回退：某节叶子全做完、
目录圈仍是白的，就把整节按真实播放（1.0x、不拖动进度条）重做一遍，
一节要 1.5~16 分钟——单是 Reading comprehension 里那段 408s 视频
就要原地播 6 分 48 秒，看起来就像死机。

### 根因

`adaptive` 模式的回退从来没成功过：2026-11-23 ~ 11-25 的日志里
**回退 34 次、点亮 0 次**。圈白与播放方式无关——白圈的这几节末尾是

- `Discussion`（讨论页）：程序只做「标记已看」（不往讨论区发内容），
  平台不把标记已看算进节完成度；
- `Review & check`：自评表页（`tab_type=check`），同理。

### 改动

- 新增闸门：节末叶子是 `discussion` / `check` 时，判定「这节点不亮」，
  直接跳过真播重做，并在收尾提示里写明「只能手动去讨论区发一帖」。
- 新增本地记账：真播重做后圈仍未蓝的节，写进 `state/progress.json`
  的 `micros` 段（TTL 同 `resume.ttl_days`，默认 7 天），后续轮次不再重做。
  新增 `--retry-burn-reset` 可清空这批记录（平台改版后想再试一次）。
- `RunState.mark()` 落盘时保留 `micros` 段，不会被单叶状态覆盖。

### 测试

- 新增 `tests/test_retry_burn.py`（5 项：落盘/重载/复位、enabled=False、
  闸门顺序、收尾文案）；`test_offline.py` 29/29、`test_selfheal_fix.py` 10/10、
  `test_voice_score_report.py` 6/6 通过；`py_compile` OK。

## v1.1.2 — 2026-11-25

让口语题的 SOE 语音评测分数在日志与报告中清晰可见（`tools/study_ai.py`）。

### 改进

- 口语题提交拿到分后，用专门文案打印「★ 口语题评分：NN 分（SOE 语音评测）」，
  不再和客观题的「判分 scorePct=... 标准答案=[...]」混在一行——后者是题库试探分，
  两者含义不同，混排容易被误读成口语题没满分。
- `study_ai_report.json` 新增 `voice_scores` 数组（题名 / 单元 / 百分制分数），
  运行结束日志同步输出「口语题 SOE 评分：N 题 / 平均 XX 分」及各题分数。
- 说明：分数本来就来自提交接口的 `record_grade.scorePct` 并已写进 `info["score"]`，
  只是此前日志未区分来源；本次不改评分逻辑，只提升可见性。

### 测试

- 新增 `tests/test_voice_score_report.py`（6 项：日志分流 + 报告提取行为模拟）；
  `test_offline.py` 29/29、`test_selfheal_fix.py` 10/10 通过；`py_compile` OK。

## v1.1.1 — 2026-11-25

减少运行中「本可自愈却停下来叫人」的环节（`tools/study_ai.py`）。

### 修复

- **课程结构为空（登录态过期）**：原先自动重登 1 次失败就报「请手动执行
  `api_run.py`」，需人工敲命令。现改为最多 2 轮「强制重登 → 重取结构」自愈，
  成功即继续；两轮都失败才给出手动命令（并说明多半是顶号 / 平台临时故障，
  稍后再试通常自愈）。
- **连续叶子打不开**：原先连续 N 个页面打不开就直接弹窗唤起 WorkBuddy + 中止。
  现引入自愈额度（每遍 2 次）：先自动重登 + 重新注入 cookie，并用结构接口
  验证会话确实恢复，成功则清零失败计数继续跑；确属登录态彻底失效、
  自愈耗尽才叫人。
- 两种情况都保留失败时的明确日志与求助包，只是把「先自愈」放在「叫人」之前。

### 测试

- 新增 `tests/test_selfheal_fix.py`（10 项：源码守卫 + 自愈流程行为模拟）；
  `tests/test_offline.py` 29/29 通过；改动文件通过 `py_compile`。

## v1.1.0 — 2026-11-23

安全与正确性审计修复 + 此前功能迭代（AI 版两遍满分答题链、断点续跑、UI 面板等）。

### 安全

- **config.yaml 明文密码清空**（login_method=manual 下该字段完全用不到，纯泄露风险）；
  `output/*.json` 历史调试转储中的手机号批量打码（文件保留供结构参考）。
- **start.bat 编码损坏修复**：11-21 的某次编辑把全文件中文写成 UTF-8 替换符乱码
  （"锟斤拷"，菜单不可读）。以 git 初始提交的健康版为基线重建，保留损坏前唯一
  有效改动（aiauto 直跑加 `--book all`），损坏版留档 `start.bat.bak_broken_20261123`。

### 修复

- **upc/quiz.py 题库防污染**：停用多题页的 DOM 顺序对齐回填——旧实现把反馈文本
  按序号硬对到题目上，一题缺反馈后续全错位入库，且 cache 命中即 0.95 置信直接使用；
  现只保留单题页回填，多题页答案沉淀走 study_ai 链路的解密判题（天然按题分组）。
- **upc/sniff.py 答案键强弱分级**：correctAnswer 类强键优先采信；answer/key 类弱键
  （可能是用户本次提交的答案）仅在强键全层未命中时采用。
- **upc/llm.py**：`_same_answer` 修复——"cat"/"act" 这类英文文本不再被误按字母
  集合判一致（复核假阳）；HTTP 401/402/403 熔断（Key 无效/余额不足时不再每题
  白撞到 max_calls）。
- **essay/填空兜底去雷同**：主观题兜底从"全平台同一句"改为随机句池 + 随机尾句，
  消除最典型的机器作答文本特征。
- **死配置对齐现实**：strategy/two_pass/tiku 在 upc 链路本就未实现差异化，config
  注释此前描述的能力（试答嗅探、第二遍重做、第三方题库）并不存在，已改为如实说明；
  真正的两遍满分刷由 AI 版链路 `study_ai.py --passes 2` 完成。
- **ui_server「停止」对齐行为**：Windows 上 terminate() 是硬杀且杀不到浏览器子进程，
  改 `taskkill /T` 连进程树清理，文案不再谎称"会先保存登录态"。

### 测试

- `tests/test_offline.py` 27/27 通过；本次修改的函数均通过独立行为验证。

## v1.0.0 — 2026-11-04

首个版本。参照 `chaoxing-auto-study` 的工程骨架，针对 U校园（Unipus）
重新设计——U校园 的机制与超星不同（没有视频任务点强校验，核心是
**页面停留时长 + Tab/Task 层级导航 + 无题库的即时判题反馈**），
所以业务模块是重写的，不是简单改选择器。

### 新增

- **登录**：手动登录 + 登录态持久化 + 掉线自愈 + 可选账号密码
- **浏览器**：Edge/Chrome 自动回退；`launch` / `attach`（CDP 接管已登录浏览器）双模式；
  伪麦克风参数（口语题必需）
- **目录解析**：Unit → Section → Micro 三层树，自动展开折叠节点，
  `data-upc-id` 锚定避免 ElementHandle 失效；三套策略（pc-slider-menu / AI 版 u3menu / 通用树）
- **刷时长**：`total` / `per_item` / `remaining` 三种分配模式，
  时长 ±15% 抖动避免机器节奏，计时期间鼠标移动 + 随机滚动保活
- **视频音频**：跨 frame 查找、解锁 `vjs-controls-disabled`、静音自动播放、
  倍速、卡顿自愈、禁止倍速检测
- **单元测试**：从 `input` 反推题目结构（不依赖题目容器 class）；
  本地题库 → 大模型 → 试答嗅探 → 兜底四级链路；**保证不留空**
- **答案嗅探**：监听 ajax/api 响应，递归提取 `correctAnswer` / `answer` 等字段，
  或从 HTML 正则匹配"正确答案：X"；自动沉淀到本地题库
- **口语录音**：录音 → 停止 → 提交流程自动化
- **拟人化**：二次贝塞尔鼠标轨迹、随机延时、随机滚动、长暂停、节奏抖动
- **可观测**：Markdown + JSON 报告、进度落盘、日志脱敏（Key/手机号）

### 工程配套

- `tools/doctor.py` 环境自检（Python/依赖/浏览器/配置/网络/登录态）
- `tools/probe.py` **DOM 探针**：打印每组选择器的命中数、dump 真实结构，
  用于平台改版后重新校准
- `tools/set_key_gui.py` 密钥图形界面（含连通性测试、状态行）
- `tools/check_secrets.py` 提交前密钥扫描
- `tools/package.py` 白名单打包
- `tests/test_offline.py` 18 项离线单测
- `tests/smoke_browser.py` 浏览器连通性冒烟测试
- `setup.bat` 三级镜像回退（阿里云 → 腾讯云 → 官方 PyPI）

### 实测记录

- Edge 启动 + U校园 首页可达：通过
- 会话探测在未登录时正确返回非 `ok`（避免误判跳过登录）
- 离线单测 18/18 通过
- 密钥写入只改动 `quiz.llm` 段，不影响同级其它配置

### 待校准

以下需要在真实课程页面（`ucontent.unipus.cn`）上用 `probe.py` 复核：

- [ ] `MENU_CONTAINER` / `NODE_*` 在你就读课程上的实际命中
- [ ] `.pc-task` 的完成状态类名
- [ ] 单元测试提交接口的返回结构（决定 `sniff` 命中率）
- [ ] 口语题的录音按钮选择器
