import os, queue, threading, time, subprocess, json, urllib.request, urllib.error
import numpy as np

from config import Config, resource_path

SYSTEM_PROMPT = ('你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成简体中文，'
                 '并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，'
                 '不要擅自添加原文中没有的特殊符号，也不要擅自增加或减少换行。')

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
        self.audio_q = queue.Queue(maxsize=512)    # 原始音频块（大容量：识别期间不丢音频）
        self.tr_q = queue.Queue(maxsize=8)         # 待翻译文本
        self.llm_proc = None
        self.asr_model = None
        self.pa = None
        self.stream = None
        self.llm_ok = False

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

    # ============ LLM (llama.cpp + Sakura) ============
    def _llm_url(self):
        port = self.cfg['llm_port']
        return self.cfg['llm_url'] or ('http://127.0.0.1:%d/v1/chat/completions' % port)

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
            kernel32 = ctypes.windll.kernel32

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
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
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # 9 = JobObjectExtendedLimitInformation
            kernel32.AssignProcessToJobObject(job, int(proc._handle))
            self._job_handle = job   # 保持引用，主进程结束时句柄关闭→杀子进程
        except Exception:
            pass

    def _health_ok(self):
        try:
            with urllib.request.urlopen(('http://127.0.0.1:%d/health' % self.cfg['llm_port']), timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def _start_llm(self):
        if self._health_ok():
            self.llm_ok = True
            self.on_status('翻译引擎: 已连接')
            return
        exe = self._find_llama_server()
        gguf = resource_path(os.path.join('models', 'translate', 'Galtransl-v4-4B-2601.gguf'))
        if not exe or not os.path.exists(gguf):
            self.on_status('缺少 llama-server 或 Sakura 模型，翻译不可用')
            return
        self.on_status('正在启动翻译引擎 (Sakura Q6K)…')
        log = open(resource_path('llama-server.log'), 'a', encoding='utf-8', errors='replace')
        self.llm_proc = subprocess.Popen(
            [exe, '-m', gguf, '-c', '4096', '--port', str(self.cfg['llm_port']),
             '-ngl', '999', '--host', '127.0.0.1'],   # 4096 上下文 / 999 全部层入 GPU
            stdout=log, stderr=subprocess.STDOUT)
        self._bind_job(self.llm_proc)   # 主进程死后 llama-server 自动陪葬，不留孤儿
        t0 = time.time()
        while time.time() - t0 < 240 and not self._stop_event.is_set():   # 240s 启动超时
            if self._health_ok():
                self.llm_ok = True
                self.on_status('翻译引擎: 就绪')
                return
            time.sleep(1.5)
        self.on_status('翻译引擎启动失败，查看 llama-server.log')

    def _clean_quotes(self, out):
        out = out.strip()
        for q in ('"', chr(39), '「', '」', '『', '』'):
            if len(out) >= 2 and out[0] == q[0] and out[-1] == q[-1]:
                out = out[1:-1]
        return out or '（空译文）'

    def translate(self, text):
        """Sakura 翻译（一次性整句返回）"""
        if not self.llm_ok:
            return '（翻译引擎未就绪）'
        payload = {
            'model': 'sakura',
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': '将下面的日文文本翻译成中文：' + text},
            ],
            'temperature': 0.3,
            'top_p': 0.8,
            'max_tokens': 512,
        }
        req = urllib.request.Request(
            self._llm_url(),
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
        self.on_status('正在加载 ASR 模型 (kotoba-whisper v2.0)…')
        try:
            self.asr_model = WhisperModel(model_dir, device='cuda', compute_type='int8', cpu_threads=8)
        except Exception:
            self.on_status('CUDA 加载失败，回退 CPU')
            self.asr_model = WhisperModel(model_dir, device='cpu', compute_type='int8', cpu_threads=8)
        self.on_status('ASR: 就绪')

    # 强制过滤：单独成句的"ごめん"直接丢弃
    FORCE_FILTER = {'ごめん'}

    def _transcribe(self, audio):
        """识别整缓冲，返回带时间戳的片段列表（None=模型未就绪/失败）"""
        if self.asr_model is None:
            return None
        try:
            segments, _ = self.asr_model.transcribe(
                audio, language='ja', beam_size=1, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=False,
                vad_filter=True,   # 跳过静音：缓冲内含静音段时避免幻觉
                no_speech_threshold=float(self.cfg['no_speech_threshold']))
            return [s for s in segments if s.text.strip()]
        except Exception as e:
            self.on_status('ASR 错误: %s' % type(e).__name__)
            return None

    # ============ 音频捕获 (WASAPI 环回) ============
    def _start_capture(self):
        import pyaudiowpatch as pyaudio
        self.pa = pyaudio.PyAudio()
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        dev = self.pa.get_device_info_by_index(wasapi['defaultOutputDevice'])
        if not dev.get('isLoopbackDevice', False):
            for loop in self.pa.get_loopback_device_info_generator():
                if dev['name'] in loop['name']:
                    dev = loop
                    break
        rate = int(dev['defaultSampleRate'])
        ch = int(dev['maxInputChannels']) or 2
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

        self.stream = self.pa.open(
            format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
            input_device_index=dev['index'], frames_per_buffer=480, stream_callback=cb)
        self.stream.start_stream()

    def _audio_loop(self):
        try:
            self._start_capture()
        except Exception as e:
            self.on_status('音频设备不可用: %s' % e)
            return
        while not self._stop_event.wait(0.1):
            if self.stream is not None and self.stream.is_active() is False:
                self.on_status('音频流已停止')
                break

    def _to_16k_mono(self, a, rate, ch):
        if ch > 1 and a.ndim > 1:
            a = a.mean(axis=1)
        if rate != SAMPLE_RATE:
            n_out = int(len(a) * SAMPLE_RATE / rate)
            t_old = np.arange(len(a))
            t_new = np.linspace(0, len(a) - 1, n_out)
            a = np.interp(t_new, t_old, a).astype(np.float32)
        return a
    # ============ 滚动识别（模型时间戳切分，无 VAD） ============
    def _asr_loop(self):
        buf = np.zeros(0, dtype=np.float32)   # 16k 单声道缓冲（≤30s）
        margin = float(self.cfg['min_silence_ms']) / 1000.0   # 句末确认：说完后再等这么久确认结束
        need = True                           # 有新音频或切过段 → 立即识别
        last_tail = ''                       # 上次上屏的未完成文本（同句不重复上）
        while not self._stop_event.is_set():
            got = False
            try:
                while True:   # 先排空音频队列（WASAPI 每块仅几 ms，必须一次性全收）
                    item = self.audio_q.get_nowait()
                    a = self._to_16k_mono(*item)
                    if len(a):
                        buf = np.concatenate([buf, a])
                        if len(buf) > MAX_BUF_S * SAMPLE_RATE:
                            buf = buf[-MAX_BUF_S * SAMPLE_RATE:]   # 超限丢最旧
                        got = True
            except queue.Empty:
                pass
            if got:
                need = True
            if not need or len(buf) == 0:
                time.sleep(0.05)   # 无新音频：歇 50ms 再查
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
                        text = s.text.strip()
                        if not text or text in self.FORCE_FILTER:
                            continue          # 强制删除"ごめん"
                        if self.on_asr:
                            self.on_asr(text, {'to_asr': asr_s, 'final': True})
                        try:
                            self.tr_q.put_nowait((text, asr_s))   # 整段立即翻译
                        except queue.Full:
                            pass
                buf = buf[int(cut_s * SAMPLE_RATE):]
                need = True   # 切完立即再识别剩余部分
            # 未完成部分（尾部）替换式实时上屏
            tail = ''.join(s.text for s in segs if s.end > cut_s).strip()
            if tail != last_tail or (cut_s > 0 and last_tail and not tail):
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
        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.pa is not None:
                self.pa.terminate()
        except Exception:
            pass
        if self.llm_proc is not None:
            try:
                self.llm_proc.terminate()
            except Exception:
                pass