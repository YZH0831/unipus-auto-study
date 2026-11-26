# mobile/ — App 端专属题的手机自动化

PC 端有一类题**在代码层面就做不了**：`ai-spoken`（Talk with AI 1~4）。
PC 包里它是个空壳组件，`render()` 直接返回「该题型暂不支持pc端，请在app端查看」。

本目录用 **ADB 直接操作手机上的 U校园 App** 完成这类题。

## 为什么 PC 端绕不过去（别再重复试）

| 尝试 | 结果 |
|---|---|
| 换移动 UA | 页面照旧显示「暂不支持 pc 端」 |
| 改 `appid` 参数 | 无效 |
| 换移动皮肤包 `_explorationmobile_*` / `_explorationwap_*` / `_explorationapp_*` | 全部 404，皮肤只有 `_explorationpc_default` / `_explorationpc__` |
| 分析 `question-pc-556fc926.js` | 找到根因：该类题型是**硬编码空壳组件**，没有交互实现 |

后端接口倒是 PC/移动完全一致（`/studio/assistant/{ebcp/auth, conversation/create,
conversation/message/history, message/save, ...}`），但目前缺 App 端的鉴权参数，
且 Android 16 默认不信任用户证书、未 root 抓不到 HTTPS，故走真机操作。

## 原理

```
英文文本 --edge-tts--> mp3 --ffmpeg--> wav --电脑扬声器--> 手机麦克风 --App 录音--> 识别 --> 提交
```

关键点：用**电脑扬声器**发声，而不是手机自己放。手机的回声消除（AEC）只消除
它自己扬声器的声音，对外部声源不做处理，所以能被正常录进去。

要求：手机放在电脑旁、电脑音量开大、手机不锁屏、题目页面停在对话界面。

## 依赖

| 项 | 说明 |
|---|---|
| adb | 优先 PATH，其次 `adbenv.py` 里的已知路径；也可用 `ADB_PATH` 环境变量指定 |
| ffmpeg / ffprobe | 同上（`FFMPEG_PATH` / `FFPROBE_PATH`） |
| edge-tts | 已在本项目 venv 里，英文语音用 `en-US-AriaNeural` |
| 手机 | 开「USB 调试」，首次需在手机上点「允许」 |

检查依赖：`python mobile/adbenv.py`

## 用法

```bash
# 0. 看手机当前界面有哪些文字/坐标（定位按钮用）
python mobile/mobile_uia.py texts
python mobile/mobile_uia.py dump

# 1. 常用动作
python mobile/mobile_uia.py tap "学习"        # 按文字点
python mobile/mobile_uia.py xy 607 2200       # 按坐标点
python mobile/mobile_uia.py shot 页面名        # 截图到 mobile/shots/
python mobile/mobile_uia.py back / home / unlock

# 2. 对话页里连续发送多段英文（界面已停在「点击录音」）
python mobile/talk.py "句1|句2|句3|句4" 12

# 3. 从题目详情页一键做完一整题（选角色→对话→结束→查看评价→提交→返回目录）
python mobile/auto_twai.py --content "句1|句2|句3|句4" --role A --wait 12

# 4. 只验证「能不能录进去」（单句实验）
python mobile/try_voice.py "Hello, this is a test." 8
```

## 实测结论（2026-11-26）

- 单位：Unit 1 / Unit 2 的 Talk with AI 1、2 共 4 题，全部提交成功，
  目录里状态变为「已完成」。
- 答题小结明确：**是否计入教程学习成绩 = 不计入**，**是否必修 = 非必修**。
  所以语音识别质量（实测 59 分）不影响任何成绩，**只要「主观题已作答 1/1」即算完成**。
- 识别文本会有失真（声音经空气传播），表现为「You say that one just got.」这类碎片。
  如要更高分，只能缩短手机与音箱距离或改用有线音频注入（未 root 不可行）。

## 交互要点（踩过的坑）

1. **必须先点选 Role，再点「开始对话」**。只点「开始对话」不会有反应。
2. 「重录」= **立即重新开始录音**，不是回到待录状态。
3. 录音失败会弹「录音失败，请重试！」，需点「我知道了」后重来；`talk.py` 已自动处理。
4. 发送后要等 2~4 秒才回到「点击录音」，过早点击会导致下一轮失败。
5. 教材目录是 ViewPager：`input swipe` 慢速滑动**无效**，必须用 `duration <= 100ms`
   的快滑（`input swipe 900 1800 250 1800 80`）；但快滑带惯性会跳 2 个单元，
   往回滑一次只退 1 个，可据此微调。
6. Talk with AI 页面的章节条目有时 `bounds` 全为 `[0,0]`（无障碍缓存问题），
   此时改用截图坐标，各单元布局相同可直接复用。
