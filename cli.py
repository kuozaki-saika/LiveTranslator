# -*- coding: utf-8 -*-
"""命令行日志 + 屏幕悬浮窗字幕 同时运行
日文识别实时上屏（终端原地刷新），整段翻译完成后输出中文
"""
import argparse
import os
import signal
import sys
import threading
import time

# HF 缓存放入程序目录（.cache）：卸载 = 删除文件夹，不在用户目录留残留
_base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
    else os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('HF_HOME', os.path.join(_base, '.cache'))
_qt_rules = os.environ.get('QT_LOGGING_RULES', '')
os.environ['QT_LOGGING_RULES'] = ';'.join(
    rule for rule in (_qt_rules, 'qt.multimedia.ffmpeg=false') if rule)


def setup_console():
    """让 Windows 控制台正确显示 UTF-8 中文"""
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass


class Printer:
    def __init__(self, lock):
        self.lock = lock
        self.live = False
        self.text = ''

    def _replace(self, text, end=''):
        print('\r' + ' ' * (len(self.text) * 2) + '\r' + text,
              end=end, flush=True)
        self.text = '' if end else text

    def recog(self, jp):
        with self.lock:
            if not self.live and jp:
                print('', flush=True)
            self._replace(jp)
            self.live = bool(jp)

    def final(self, jp):
        with self.lock:
            if not self.live:
                print('', flush=True)
            self._replace(jp, '\n')
            self.live = False

    def result(self, zh, meta):
        with self.lock:
            print('（%.2fs）%s' % (meta.get('tr_s', 0.0), zh), flush=True)


def main():
    setup_console()
    parser = argparse.ArgumentParser(description='日文直播实时字幕：命令行日志 + 屏幕悬浮窗')
    parser.add_argument('--no-overlay', action='store_true', help='不显示屏幕悬浮窗，仅命令行日志')
    args = parser.parse_args()

    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
    from config import Config
    from engine import LiveEngine

    cfg = Config()

    # ---------- Qt 悬浮窗（与命令行日志并行） ----------
    overlay = None
    bridge = None
    app = None

    if not args.no_overlay:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QObject, Signal, QTimer
        from overlay import SubtitleOverlay

        # 跨线程桥：引擎回调线程 emit 信号 → Qt 自动排队到 GUI 线程执行（免轮询）
        class OverlayBridge(QObject):
            pending = Signal(str)
            final = Signal(str)
            block = Signal(str, str)

        app = QApplication(sys.argv)
        app.setApplicationName('LiveTranslator')
        overlay = SubtitleOverlay(cfg)
        overlay.show()
        bridge = OverlayBridge()
        bridge.pending.connect(overlay.update_pending)
        bridge.final.connect(overlay.finalize_pending)
        bridge.block.connect(overlay.complete_pending)
        # 空定时器保证 Python 字节码周期执行，Ctrl+C 才能投递
        keep = QTimer()
        keep.timeout.connect(lambda: None)
        keep.start(100)

    # ---------- 命令行日志 ----------
    out_lock = threading.Lock()

    # 终端输出：日文实时更新行（\r 原地刷新），整句翻译完成后输出中文
    printer = Printer(out_lock)

    def on_asr(jp, meta=None):
        meta = meta or {}
        if meta.get('final'):
            if jp:
                printer.final(jp)     # 完成句：定格当前行
        else:
            printer.recog(jp)         # 尾部：实时更新或清空
        if bridge is not None:
            (bridge.final if meta.get('final') else bridge.pending).emit(jp)

    def on_result(jp, zh, meta=None):
        meta = meta or {}
        printer.result(zh, meta)
        if bridge is not None:
            bridge.block.emit(jp, zh)

    def on_status(s):
        print('[状态] ' + s, flush=True)

    eng = LiveEngine(cfg, on_result, on_status, capture=True, on_asr=on_asr)

    # ---------- 启动 ----------
    print('正在监听系统声音…（托盘图标右键退出）', flush=True)

    if app is not None:
        from PySide6.QtMultimedia import QMediaDevices

        def _on_sigint(s, f):
            eng.stop()
            app.quit()
        media_devices = QMediaDevices()
        media_devices.audioOutputsChanged.connect(eng.request_capture_restart)
        signal.signal(signal.SIGINT, _on_sigint)
        app.aboutToQuit.connect(lambda: eng.stop())
        eng.start()
        app.exec()
        eng.join(timeout=8)
    else:
        eng.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            eng.stop()
            eng.join(timeout=8)


if __name__ == '__main__':
    main()
