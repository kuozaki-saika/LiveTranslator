# LiveTranslator — 日语直播实时字幕（全本地）

监听电脑播放的声音（WASAPI 环回），kotoba-whisper-v2.0-faster 日语识别，Sakura GalTransl-v4-4B-2601 (Q6K) 翻译成中文。

## 配置要求

- Windows 10/11
- Python 3.10-3.14
- NVIDIA 显卡（推荐8GB以上显存）

## 安装

克隆仓库

两条命令分开执行。先创建环境并安装依赖（首次约 5-10 分钟）：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
~~~

再下载模型与推理引擎（约 4.6GB，支持断点续传）：

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\download_assets.ps1
~~~

## 使用

- 双击 scripts\cli.bat：监听系统声音，实时输出日语识别 + 中文翻译（终端日志 + 屏幕悬浮窗）
- 托盘图标右键：显示/关闭字幕、穿透、退出
- 悬浮窗右键菜单：大多数设置
- 测试模式：powershell -ExecutionPolicy Bypass -File scripts\run.ps1 --file 某音频.wav

## 技术栈

| 环节 | 组件 |
|------|------|
| 音频捕获 | WASAPI 环回（pyaudiowpatch） |
| VAD | Silero VAD（阈值 0.5/静音 100ms/无补尾） |
| 日语识别 | kotoba-whisper-v2.0-faster（CTranslate2 int8, CUDA） |
| 日译中 | Sakura GalTransl-v4-4B-2601 Q6K（llama.cpp 本地服务） |
| 显示 | 终端日志 + PySide6 悬浮窗（思源宋体 SC/JP） |

## 许可证

- Sakura 模型：CC BY-NC-SA 4.0（个人使用，禁止商用）
- kotoba-whisper：Apache-2.0
- 本仓库代码：MIT
