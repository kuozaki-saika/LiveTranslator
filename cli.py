# -*- coding: utf-8 -*-
"""命令行日志 + 屏幕悬浮窗字幕 同时运行
日文识别实时上屏（终端原地刷新），整段翻译完成后输出中文
"""
import argparse
import os
import queue
import signal
import sys
import time

# HF 缓存放入程序目录（.cache）：卸载 = 删除文件夹，不在用户目录留残留
_base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) \
    else os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('HF_HOME', os.path.join(_base, '.cache'))


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


def main():
    setup_console()
    parser = argparse.ArgumentParser(description='日文直播实时字幕：命令行日志 + 屏幕悬浮窗')
    parser.add_argument('--file', help='读取音频文件代替系统声音（测试用，仅命令行日志）')
    parser.add_argument('--no-overlay', action='store_true', help='不显示屏幕悬浮窗，仅命令行日志')
    args = parser.parse_args()

    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
    from config import Config
    from engine import LiveEngine

    cfg = Config()

    # ---------- Qt 悬浮窗（与命令行日志并行） ----------
    overlay = None
    app = None
    result_q = queue.Queue()

    def update_overlay():
        try:
            while True:
                item = result_q.get_nowait()
                if overlay is None:
                    continue
                if item[0] == 'pending':
                    overlay.update_pending(item[1])
                elif item[0] == 'block':
                    overlay.complete_pending(item[1], item[2])
        except queue.Empty:
            pass

    if not args.file and not args.no_overlay:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        from overlay import SubtitleOverlay
        app = QApplication(sys.argv)
        app.setApplicationName('LiveTranslator')
        overlay = SubtitleOverlay(cfg)
        overlay.show()
        t = QTimer()
        t.timeout.connect(update_overlay)
        t.start(20)   # 20ms 轮询悬浮窗更新队列
        # 空定时器保证 Python 字节码周期执行，Ctrl+C 才能投递
        keep = QTimer()
        keep.timeout.connect(lambda: None)
        keep.start(100)

    # ---------- 命令行日志 ----------
    import threading
    out_lock = threading.Lock()

    # 终端输出：日文实时更新行（\r 原地刷新），整句翻译完成后输出中文
    class Printer:
        def __init__(self):
            self.live = False      # 当前行是否处于实时更新状态

        def recog(self, jp, meta):
            with out_lock:
                if not self.live:
                    print('', flush=True)              # 组前空行
                print('\r' + jp, end='', flush=True)   # 原地更新识别行
                self.live = True

        def result(self, zh, meta):
            with out_lock:
                print('\n（%.2fs）%s' % (meta.get('tr_s', 0.0), zh), flush=True)   # 翻译本身用时
                self.live = False

    printer = Printer()

    def on_asr(jp, meta=None):
        meta = meta or {}
        printer.recog(jp, meta)
        try:
            result_q.put_nowait(('pending', jp))
        except queue.Full:
            pass

    def on_result(jp, zh, meta=None):
        meta = meta or {}
        printer.result(zh, meta)
        try:
            result_q.put_nowait(('block', jp, zh))
        except queue.Full:
            pass

    def on_status(s):
        print('[状态] ' + s, flush=True)

    eng = LiveEngine(cfg, on_result, on_status, capture=(args.file is None),
                     on_asr=on_asr)

    # ---------- 启动 ----------
    if args.file:
        eng.start()
        import av
        import numpy as np
        container = av.open(args.file)
        frames = []
        for f in container.decode(audio=0):
            frames.append(f.to_ndarray().ravel())
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        for i in range(0, len(audio), 16000):
            eng.audio_q.put((audio[i:i + 16000], 16000, 1))
        idle = 0
        while idle < 12:   # 全部队列空且持续 6s 视为处理完毕
            if eng.audio_q.empty() and eng.asr_q.empty() and eng.tr_q.empty():
                idle += 0.5
            else:
                idle = 0
            time.sleep(0.5)
        eng.stop()
        eng.join(timeout=8)
        print('处理完毕，退出。', flush=True)
        return

    print('正在监听系统声音…（托盘图标右键退出）', flush=True)

    if app is not None:
        def _on_sigint(s, f):
            eng.stop()
            app.quit()
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