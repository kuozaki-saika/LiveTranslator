import json, os, sys

def app_root():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def resource_path(rel):
    return os.path.join(app_root(), rel)

DEFAULTS = {
    'blocks': 2,            # 同时显示的日文-中文字幕条数
    'jp_size': 20,          # 日文字号
    'zh_size': 20,          # 中文字号
    'font_color': '#FFFFFF',
    'stroke_color': '#000000',
    'stroke_width': 2,
    'font_weight': 600,        # 字重（可选 400/500/600/700/900，思源宋体静态字重）
    'stroke_enabled': True,      # 描边开关
    'border_enabled': True,      # 边框开关
    'border_color': '#000000',   # 边框颜色
    'border_width': 1,           # 边框粗度
    'click_through': False,      # 点击穿透（默认关，右键可操作）
    'min_silence_ms': 100,       # VAD 静音检测（ms）
    'no_speech_threshold': 0.5,  # 语音判定阈值（高于此值判为非语音）
    'x': 0, 'y': 860,   # 贴屏幕底部（任务栏上方）
    'width': 1700,
    'llm_port': 11435,
    'llm_url': None,  # 自动 = http://127.0.0.1:{port}/v1/chat/completions
}

class Config:
    def __init__(self):
        self.data = dict(DEFAULTS)
        self.path = resource_path('config.json')
        self.load()

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            self.data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:
            pass

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()