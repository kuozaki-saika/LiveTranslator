# LiveTranslator

LiveTranslator 是一款 Windows 日语直播字幕工具。程序读取默认播放设备正在播放的声音，在本机完成日语识别、中文翻译和悬浮字幕显示。

运行期间，音频和字幕只在本机处理。首次安装需要联网下载依赖、字体、识别模型、翻译模型和推理程序。

## 主要功能

- 捕捉 Windows 默认播放设备的系统声音。
- 实时显示仍在识别中的日文。
- 按语音识别模型给出的时间位置确认句末。
- 按顺序翻译已确认的日文，显示一行日文和一行中文。
- 自动保存字幕窗口的位置、宽度和样式。
- 在正常悬浮窗模式下检测默认输出设备变化并重新连接。
- 同时提供终端回显和屏幕悬浮字幕。

## 运行环境

- Windows 10 或 Windows 11。
- Python 3.10 至 3.14；安装脚本会自动寻找可用版本。
- 当前翻译安装方案使用 llama.cpp 的 Windows CUDA 12.4 版本，需要 NVIDIA 显卡。CUDA 是程序使用 NVIDIA 显卡进行计算的接口。
- 日语识别优先使用 NVIDIA 显卡；显卡加载失败时会回退到处理器运行。
- 首次下载需要可访问 GitHub 和 Hugging Face。

## 安装

下载并解压项目后，在项目根目录依次执行下面两条命令。

### 1. 创建 Python 环境并安装依赖

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1
```

完成时会显示：

```text
ENV DONE
IMPORTS OK
```

### 2. 下载字体、模型和翻译程序

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_assets.ps1
```

下载支持断点续传。中途失败时，重新执行同一条命令即可继续。

## 启动

直接双击：

```text
scripts\cli.bat
```

也可以从 PowerShell 启动：

```powershell
.\scripts\run.ps1
```

只使用终端回显，隐藏悬浮字幕：

```powershell
.\scripts\run.ps1 --no-overlay
```

程序启动后会显示模型加载状态和当前监听的播放设备。两个模型并行加载，因此状态的先后顺序可能不同。正常状态包括：

```text
正在加载ASR模型...
ASR模型就绪
正在加载翻译模型...
翻译模型就绪
监听: 播放设备名称
```

## 悬浮窗操作

### 鼠标操作

- 按住左键拖动字幕窗口；松开后自动保存位置。
- 右键打开字幕设置。
- 开启穿透后，通过托盘菜单关闭穿透即可恢复鼠标操作。

### 悬浮窗右键菜单

- 字幕条数
- 字幕宽度
- 日文大小
- 中文大小
- 字重
- 字体颜色
- 描边、描边宽度和描边颜色
- 边框、边框宽度和边框颜色
- 穿透
- 句末缓冲
- 清空字幕

句末缓冲修改后需要重启程序。其他显示设置会立即生效。

### 托盘菜单

- 显示或关闭字幕
- 开启或关闭穿透
- 退出程序

## 默认设置

| 参数 | 默认值 |
|---|---:|
| 字幕条数 | 2 |
| 字幕宽度 | 600 |
| 日文字号 | 20 |
| 中文字号 | 20 |
| 字重 | 900 |
| 字体颜色 | `#FFFFFF` |
| 描边 | 开启 |
| 描边宽度 | 3 |
| 描边颜色 | `#000000` |
| 边框 | 关闭 |
| 边框宽度 | 1 |
| 边框颜色 | `#000000` |
| 穿透 | 关闭 |
| 句末缓冲 | 200 毫秒 |
| 初始位置 | `x=596，y=690` |
| 翻译服务端口 | 11435 |

所有设置保存在项目根目录的 `config.json`。关闭程序后删除这个文件，可以恢复代码中的默认设置。

## 处理流程

```text
Windows 默认播放设备
    ↓
Windows 系统音频接口读取正在播放的声音
    ↓
多声道混合成单声道，并重采样为每秒 16000 个采样点
    ↓
日语语音识别模型滚动识别并确认句末
    ↓
完整日文按顺序进入翻译队列
    ↓
本机 Sakura 翻译模型生成中文
    ↓
终端回显和悬浮字幕
```

这里的“Windows 系统音频接口”对应代码中的 WASAPI 环回。WASAPI 是 Windows 的音频接口；环回表示读取播放设备正在输出的声音，而不是麦克风声音。

## 使用的组件

| 环节 | 组件 |
|---|---|
| 系统声音捕捉 | PyAudioWPatch、Windows 系统音频接口 |
| 音频重采样 | PyAV |
| 日语语音识别 | faster-whisper、kotoba-whisper-v2.0-faster |
| 中文翻译 | llama.cpp、Sakura GalTransl-v4-4B-2601 |
| 字幕窗口 | PySide6、思源宋体简体中文/日文版 |

## 常见问题

### 没有识别到声音

确认声音正在 Windows 默认播放设备中播放。终端中的 `监听:` 后面会显示当前设备。正常悬浮窗模式会在默认输出设备变化时重新连接。

### 提示缺少模型或翻译程序

重新执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_assets.ps1
```

### 翻译模型加载失败

查看项目根目录的 `llama-server.log`。当前下载脚本安装的是 Windows CUDA 12.4 版本，应同时检查 NVIDIA 显卡驱动和显存占用。

### 开启穿透后无法右键字幕

右键任务栏通知区域中的程序图标，取消“穿透”。

### 恢复默认设置

先退出程序，再删除项目根目录的 `config.json`，然后重新启动。

## 卸载

退出程序后删除整个项目文件夹。项目使用的虚拟环境、模型、字体、配置和运行缓存都位于这个文件夹中。

## 许可证

本项目代码采用 [MIT License](LICENSE)。下载的模型、字体和推理程序分别遵循各自的许可证：

- [kotoba-whisper-v2.0-faster](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)
- [Sakura GalTransl-v4-4B-2601](https://huggingface.co/SakuraLLM/GalTransl-v4-4B-2601)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
- [思源宋体](https://github.com/adobe-fonts/source-han-serif)
