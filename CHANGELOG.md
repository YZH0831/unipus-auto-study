# 更新日志

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
