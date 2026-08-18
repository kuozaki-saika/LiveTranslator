# LiveTranslator — 日语直播实时字幕（全本地）

监听电脑播放的声音（WASAPI 环回），kotoba-whisper-v2.0-faster 日语识别，Sakura GalTransl-v4-4B-2601 (Q6K) 翻译成中文。

## 配置要求

- Windows 10/11
- Python 3.10-3.14
- NVIDIA 显卡（推荐8GB以上显存）

## 安装

Code-->Download ZIP

两条命令分开执行。先创建环境并安装依赖（首次约 5-10 分钟）：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
~~~

再下载模型与推理引擎（约 4.6GB，支持断点续传）：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\download_assets.ps1
~~~

## 卸载

删除整个文件夹即可（配置、模型、缓存全部在文件夹内；不写注册表，无系统残留）。

## 使用

- 双击 scripts\cli.bat：监听系统声音，实时输出日语识别 + 中文翻译（终端日志 + 屏幕悬浮窗）
- 字幕显示：识别实时上屏，句末确认后定格，翻译完成整块（日文+中文）上屏
- 托盘图标右键：显示/关闭字幕、穿透、退出
- 悬浮窗右键：条数/字号/字重/颜色/描边/边框/穿透；其他设置：句末确认（ms）、语音判定阈值

## 技术栈

| 环节 | 组件 |
|------|------|
| 音频捕获 | WASAPI 环回（pyaudiowpatch） |
| 语音切分 | 滚动缓冲 + whisper 时间戳判定句末（无 VAD，跳过静音） |
| 日语识别 | kotoba-whisper-v2.0-faster（CTranslate2 int8, CUDA） |
| 日译中 | Sakura GalTransl-v4-4B-2601 Q6K（llama.cpp 本地服务） |
| 显示 | 终端日志 + PySide6 悬浮窗（思源宋体 SC/JP） |

## 许可证

- Sakura 模型：CC BY-NC-SA 4.0（个人使用，禁止商用）
- kotoba-whisper：Apache-2.0
- 本仓库代码：MIT
