import os, queue, threading, time, subprocess, json, urllib.request
import numpy as np

from config import Config, resource_path

SYSTEM_PROMPT = ('你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成简体中文，'
                 '并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，'
                 '不要擅自添加原文中没有的特殊符号，也不要擅自增加或减少换行。')
USER_PROMPT = '将下面的日文文本翻译成中文：'

SAMPLE_RATE = 16000
MAX_BUF_S = 30           # 识别缓冲上限（秒，whisper 单次窗口上限）


class LiveEngine(threading.Thread):
    '''系统音频 → faster-whisper(kotoba) 滚动识别（模型时间戳切分） → Sakura 翻译 → 回调。'''

    def __init__(self, cfg: Config, on_result, on_status=None, capture=True, on_asr=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.on_result = on_result          # (jp, zh[, meta]) -> None
        self.on_status = on_status or (lambda s: None)
        self.on_asr = on_asr
        self.capture = capture
        self._stop_event = threading.Event()
        self._restart_capture_event = threading.Event()
        self._capture_lock = threading.Lock()
        self.audio_q = queue.Queue(maxsize=2048)   # 实时捕捉约 20.48s（10ms/块）
        self.tr_q = queue.Queue()                  # 待翻译文本：完整保留，按顺序翻译
        self.llm_proc = None
        self.asr_model = None
        self.pa = None
        self.stream = None
        self.llm_ok = False
        self._job_handle = None

    # ============ 主循环 ============
    def run(self):
        # 翻译引擎与 ASR 并行加载（串行约 16s → 并行约 10s）
        t_llm = threading.Thread(target=self._start_llm, daemon=True)
        t_asr = threading.Thread(target=self._start_asr, daemon=True)
        t_llm.start()
        t_asr.start()
        t_llm.join()
        t_asr.join()
        threads = [
            threading.Thread(target=self._asr_loop, daemon=True),
            threading.Thread(target=self._translate_loop, daemon=True),
        ]
        if self.capture:
            threads.append(threading.Thread(target=self._audio_loop, daemon=True))
        for t in threads:
            t.start()
        while not self._stop_event.wait(0.2):
            pass
        self._cleanup()

    def stop(self):
        self._stop_event.set()
        self._restart_capture_event.set()

    def request_capture_restart(self):
        self._restart_capture_event.set()

    # ============ LLM (llama.cpp + Sakura) ============
    def _llm_url(self, path):
        return 'http://127.0.0.1:%d/%s' % (self.cfg['llm_port'], path)

    def _find_llama_server(self):
        base = resource_path('llama')
        if not os.path.isdir(base):
            return None
        for root, _, files in os.walk(base):
            if 'llama-server.exe' in files:
                return os.path.join(root, 'llama-server.exe')
        return None

    def _bind_job(self, proc):
        """把子进程绑进 Job Object（KILL_ON_JOB_CLOSE）：
        主进程无论怎么退出（托盘/关窗口/强杀），llama-server 都会被系统终止"""
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            create_job = kernel32.CreateJobObjectW
            create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            create_job.restype = wintypes.HANDLE
            set_info = kernel32.SetInformationJobObject
            set_info.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                 ctypes.c_void_p, wintypes.DWORD)
            set_info.restype = wintypes.BOOL
            assign = kernel32.AssignProcessToJobObject
            assign.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            assign.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL

            job = create_job(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            try:
                if not set_info(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not assign(job, wintypes.HANDLE(proc._handle)):
                    raise ctypes.WinError(ctypes.get_last_error())
            except Exception:
                close_handle(job)
                raise
            self._job_handle = job   # 保持引用，主进程结束时句柄关闭→杀子进程
        except Exception as exc:
            self.on_status('翻译服务退出保护失败: %s' % type(exc).__name__)

    def _health_ok(self, timeout=0.1):
        try:
            with urllib.request.urlopen(self._llm_url('health'), timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def _start_llm(self):
        self.on_status('正在加载翻译模型...')
        if self._health_ok():
            self.llm_ok = True
            self.on_status('翻译模型就绪')
            return
        exe = self._find_llama_server()
        gguf = resource_path(os.path.join('models', 'translate', 'Galtransl-v4-4B-2601.gguf'))
        if not exe or not os.path.exists(gguf):
            self.on_status('缺少 llama-server 或 Sakura 模型，翻译不可用')
            return
        log = open(resource_path('llama-server.log'), 'a', encoding='utf-8', errors='replace')
        self.llm_proc = subprocess.Popen(
            [exe, '-m', gguf, '-c', '4096', '--port', str(self.cfg['llm_port']),
             '-ngl', '999', '--host', '127.0.0.1'],   # 4096 上下文 / 999 全部层入 GPU
            stdout=log, stderr=subprocess.STDOUT)
        self._bind_job(self.llm_proc)   # 主进程死后 llama-server 自动陪葬，不留孤儿
        deadline = time.monotonic() + 10
        next_check = time.monotonic()
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._health_ok(min(0.1, remaining)):
                self.llm_ok = True
                self.on_status('翻译模型就绪')
                return
            next_check += 0.1
            wait_s = min(deadline - time.monotonic(), next_check - time.monotonic())
            if wait_s > 0:
                self._stop_event.wait(wait_s)
        self.on_status('翻译模型加载失败，查看 llama-server.log')

    def _clean_quotes(self, out):
        out = out.strip()
        for left, right in ((chr(34), chr(34)), (chr(39), chr(39)),
                            ('「', '」'), ('『', '』')):
            if len(out) >= 2 and out.startswith(left) and out.endswith(right):
                out = out[1:-1]
                break
        if USER_PROMPT in out:
            return ''
        return out or '（空译文）'

    def translate(self, text):
        """Sakura 翻译（一次性整句返回）"""
        if not self.llm_ok:
            return '（翻译引擎未就绪）'
        payload = {
            'model': 'sakura',
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': USER_PROMPT + text},
            ],
            'temperature': 0.3,
            'top_p': 0.8,
            'max_tokens': 512,
        }
        req = urllib.request.Request(
            self._llm_url('v1/chat/completions'),
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode('utf-8'))
                out = data['choices'][0]['message']['content']
                return self._clean_quotes(out)
        except Exception as exc:
            return '（翻译失败: %s）' % type(exc).__name__

    # ============ ASR (kotoba-whisper-v2.0-faster) ============
    def _start_asr(self):
        from faster_whisper import WhisperModel
        model_dir = resource_path(os.path.join('models', 'asr'))
        if not os.path.exists(os.path.join(model_dir, 'model.bin')):
            self.on_status('缺少 ASR 模型，无法识别')
            return
        self.on_status('正在加载ASR模型...')
        try:
            self.asr_model = WhisperModel(model_dir, device='cuda', compute_type='int8', cpu_threads=8)
        except Exception:
            self.on_status('CUDA 加载失败，回退 CPU')
            self.asr_model = WhisperModel(model_dir, device='cpu', compute_type='int8', cpu_threads=8)
        self.on_status('ASR模型就绪')

    FORCE_FILTER = {'ごめん', 'はい'}

    def _filter_asr_text(self, text):
        text = text.strip()
        bare = text.strip('。、，,.！？!?…「」『』"\'（）()【】：:；;')
        return '' if bare in self.FORCE_FILTER else text

    def _transcribe(self, audio):
        """识别整缓冲，返回带时间戳的片段列表（None=模型未就绪/失败）"""
        if self.asr_model is None:
            return None
        try:
            segments, _ = self.asr_model.transcribe(
                audio, language='ja', beam_size=1, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=False,
                vad_filter=True)   # 跳过静音：缓冲内含静音段时避免幻觉
            return [s for s in segments if s.text.strip()]
        except Exception as e:
            self.on_status('ASR 错误: %s' % type(e).__name__)
            return None

    # ============ 音频捕获 (WASAPI 环回) ============
    def _start_capture(self):
        import pyaudiowpatch as pyaudio
        with self._capture_lock:
            pa = pyaudio.PyAudio()
            try:
                dev = pa.get_default_wasapi_loopback()
                rate = int(dev['defaultSampleRate'])
                ch = int(dev['maxInputChannels']) or 2
                block_frames = max(1, round(rate * 0.01))
                self.on_status('监听: %s' % dev['name'])

                def cb(in_data, frame_count, time_info, status_flags):
                    if in_data is None:
                        return (None, pyaudio.paContinue)
                    raw = np.frombuffer(in_data, dtype=np.int16)
                    a = raw.astype(np.float32) / 32768.0
                    if ch > 1:
                        # 交错立体声 → 单声道混音（否则重采样后信号扭曲，VAD 不触发）
                        a = a.reshape(-1, ch).mean(axis=1)
                    try:
                        self.audio_q.put_nowait((a, rate, 1))
                    except queue.Full:
                        try:
                            self.audio_q.get_nowait()
                            self.audio_q.put_nowait((a, rate, 1))
                        except Exception:
                            pass
                    return (None, pyaudio.paContinue)

                stream = pa.open(
                    format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                    input_device_index=dev['index'], frames_per_buffer=block_frames, stream_callback=cb)
            except Exception:
                pa.terminate()
                raise
            self.pa = pa
            self.stream = stream

    def _close_capture(self):
        with self._capture_lock:
            stream, self.stream = self.stream, None
            pa, self.pa = self.pa, None
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if pa is not None:
                pa.terminate()
        except Exception:
            pass

    def _audio_loop(self):
        while not self._stop_event.is_set():
            try:
                self._start_capture()
                while not self._stop_event.is_set():
                    if self._restart_capture_event.wait(0.1):
                        self._restart_capture_event.clear()
                        if not self._stop_event.is_set():
                            self.on_status('输出设备已变化，正在重新连接')
                        break
                    if self.stream is not None and self.stream.is_active() is False:
                        self.on_status('音频流已停止，正在重新连接')
                        break
            except Exception as e:
                self.on_status('音频设备不可用: %s' % e)
                self._restart_capture_event.wait(1.0)
                self._restart_capture_event.clear()
            finally:
                self._close_capture()

    # ============ 滚动识别（模型时间戳切分） ============
    def _asr_loop(self):
        import av

        buf = np.zeros(0, dtype=np.float32)   # 16k 单声道缓冲（≤30s）
        resampler = None
        source_rate = None
        margin = float(self.cfg['min_silence_ms']) / 1000.0   # 句末确认：说完后再等这么久确认结束
        need = True                           # 有新音频或切过段 → 立即识别
        last_tail = ''                       # 上次上屏的未完成文本（同句不重复上）
        while not self._stop_event.is_set():
            new_audio = []
            try:
                while True:   # 先排空音频队列（每块约 10ms，必须一次性全收）
                    a, rate, _ = self.audio_q.get_nowait()
                    a = np.asarray(a, dtype=np.float32).reshape(-1)
                    if len(a) == 0:
                        continue
                    if rate != source_rate:
                        if resampler is not None:
                            new_audio.extend(
                                frame.to_ndarray().reshape(-1)
                                for frame in resampler.resample(None))
                        source_rate = rate
                        resampler = None if rate == SAMPLE_RATE else av.AudioResampler(
                            format='fltp', layout='mono', rate=SAMPLE_RATE)
                    if resampler is None:
                        new_audio.append(a)
                    else:
                        frame = av.AudioFrame.from_ndarray(
                            np.ascontiguousarray(a.reshape(1, -1)),
                            format='fltp', layout='mono')
                        frame.sample_rate = rate
                        new_audio.extend(
                            out.to_ndarray().reshape(-1)
                            for out in resampler.resample(frame))
            except queue.Empty:
                pass
            if new_audio:
                buf = np.concatenate([buf, *new_audio])
                if len(buf) > MAX_BUF_S * SAMPLE_RATE:
                    buf = buf[-MAX_BUF_S * SAMPLE_RATE:]   # 超限丢最旧
                need = True
            if not need or len(buf) == 0:
                time.sleep(0.01)   # 当前没有新的识别任务：等 10ms 再查
                continue
            need = False
            t0 = time.monotonic()
            segs = self._transcribe(buf)
            asr_s = time.monotonic() - t0
            if not segs:
                continue
            buf_s = len(buf) / SAMPLE_RATE
            cut_s = 0.0
            for s in segs:
                if s.end < buf_s - margin:
                    cut_s = max(cut_s, s.end)   # 最晚的完成句结束点
            # 完成句：日文立即上屏（final，不等翻译），随后进翻译队列
            if cut_s > 0:
                for s in segs:
                    if s.end <= cut_s:
                        text = self._filter_asr_text(s.text)
                        if not text:
                            continue
                        if self.on_asr:
                            self.on_asr(text, {'to_asr': asr_s, 'final': True})
                        self.tr_q.put_nowait((text, asr_s))   # 整段立即翻译
                buf = buf[int(cut_s * SAMPLE_RATE):]
                need = True   # 切完立即再识别剩余部分
            # 未完成部分（尾部）替换式实时上屏
            tail = self._filter_asr_text(''.join(s.text for s in segs if s.end > cut_s))
            if tail != last_tail:
                last_tail = tail
                if self.on_asr:
                    self.on_asr(tail, {'to_asr': asr_s})   # 识别实时上屏

    def _translate_loop(self):
        while not self._stop_event.is_set():
            try:
                jp, asr_s = self.tr_q.get(timeout=0.5)
            except queue.Empty:
                continue
            t0 = time.monotonic()
            zh = self.translate(jp)
            tr_s = time.monotonic() - t0
            self.on_result(jp, zh, {'to_asr': asr_s, 'tr_s': tr_s})   # tr_s=翻译本身用时

    # ============ 清理 ============
    def _cleanup(self):
        self._close_capture()
        if self.llm_proc is not None:
            try:
                self.llm_proc.terminate()
            except Exception:
                pass
        if self._job_handle is not None:
            import ctypes
            from ctypes import wintypes
            close_handle = ctypes.windll.kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(self._job_handle)
            self._job_handle = None
