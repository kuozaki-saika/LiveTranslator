import json
import io
import os
import sys
import threading
from contextlib import redirect_stdout
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFontMetrics, QImage
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from cli import Printer
from engine import LiveEngine, SYSTEM_PROMPT, USER_PROMPT
from overlay import SubtitleOverlay


class MemoryConfig:
    def __init__(self, **updates):
        self.data = dict(DEFAULTS)
        self.data.update(updates)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


class TestOverlay(SubtitleOverlay):
    def apply_click_through(self):
        pass

    def setup_tray(self):
        pass


def main():
    printer = Printer(threading.Lock())
    terminal = io.StringIO()
    with redirect_stdout(terminal):
        printer.recog('ごめ')
        printer.recog('')
        printer.recog('临时')
        printer.final('最终')
    assert '\r' + ' ' * 4 + '\r' in terminal.getvalue()
    assert terminal.getvalue().endswith('\r' + ' ' * 4 + '\r最终\n')
    assert not printer.live and printer.text == ''

    engine = LiveEngine(MemoryConfig(), lambda *args: None, capture=False)
    assert engine.tr_q.maxsize == 0
    assert 'llm_url' not in DEFAULTS
    assert engine._llm_url('health') == 'http://127.0.0.1:11435/health'
    assert LiveEngine.FORCE_FILTER == {'ごめん', 'はい'}
    assert engine._filter_asr_text(' ごめん ') == ''
    assert engine._filter_asr_text(' はい ') == ''
    assert engine._filter_asr_text('「はい！」') == ''
    assert engine._filter_asr_text('ごめん！') == ''
    assert engine._filter_asr_text('はいはい') == 'はいはい'
    assert engine._filter_asr_text('はい。') == ''
    assert engine._filter_asr_text('はい、わかりました') == 'はい、わかりました'
    assert engine._filter_asr_text('ごめんなさい') == 'ごめんなさい'
    assert engine._filter_asr_text('本当にごめん') == '本当にごめん'
    assert engine._clean_quotes(' 「译文」 ') == '译文'
    assert engine._clean_quotes('『译文』') == '译文'
    assert engine._clean_quotes('译文') == '译文'
    assert engine._clean_quotes(USER_PROMPT + 'お') == ''
    assert engine._clean_quotes('「' + USER_PROMPT + '译文」') == ''
    assert engine._clean_quotes('前言' + USER_PROMPT + '译文') == ''

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"choices":[{"message":{"content":"translated"}}]}'

    engine.llm_ok = True
    with patch('engine.urllib.request.urlopen', return_value=Response()) as urlopen:
        assert engine.translate('原文') == 'translated'
    assert urlopen.call_args.args[0].full_url == (
        'http://127.0.0.1:11435/v1/chat/completions')
    payload = json.loads(urlopen.call_args.args[0].data)
    assert payload['messages'] == [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': USER_PROMPT + '原文'},
    ]

    app = QApplication.instance() or QApplication([])
    overlay = TestOverlay(MemoryConfig(border_enabled=True))
    assert overlay._border_padding() == 4
    assert overlay.width() == overlay.cfg['width'] + 8
    old_center = (2 * overlay.x() + overlay.width(),
                  2 * overlay.y() + overlay.height())
    overlay.set_cfg('border_width', 4)
    assert overlay._border_padding() == 7
    assert overlay.width() == overlay.cfg['width'] + 14
    assert (2 * overlay.x() + overlay.width(),
            2 * overlay.y() + overlay.height()) == old_center
    overlay.toggle_border()
    assert overlay._border_padding() == 0
    assert overlay.width() == overlay.cfg['width']
    overlay.toggle_border()
    assert overlay._border_padding() == 7
    assert overlay.width() == overlay.cfg['width'] + 14
    overlay.cfg['stroke_width'] = 4
    assert overlay._stroke_padding() == 3
    overlay.cfg['stroke_enabled'] = False
    assert overlay._stroke_padding() == 0
    overlay.cfg['stroke_enabled'] = True
    overlay.resize_to_fit()
    assert overlay.height() == overlay._minimum_height()
    overlay.add_block('一行日文', '一行中文')
    rows, total = overlay._layout()
    assert len(rows) == 2
    assert int(total) == overlay._minimum_height()
    overlay.clear()

    overlay.update_pending('第一句识别中')
    overlay.finalize_pending('第一句')
    overlay.update_pending('第二句识别中')
    overlay.finalize_pending('第二句')
    overlay.update_pending('第三句识别中')
    assert overlay._pending_text() == '第一句'

    overlay.complete_pending('第一句', '第一句翻译')
    assert overlay._pending_text() == '第二句'
    overlay.complete_pending('第二句', '第二句翻译')
    assert overlay._pending_text() == '第三句识别中'

    overlay.set_cfg('width', 100)
    overlay.update_pending('これは幅に合わせて複数行へ折り返される長い字幕です')
    lines = overlay.wrap_lines(overlay._pending_text(), 'jp',
                               overlay.cfg['jp_size'], 80)
    assert len(lines) > 1
    assert ''.join(lines) == overlay._pending_text()
    fm = QFontMetrics(overlay.font_for('jp', overlay.cfg['jp_size']))
    for current, following in zip(lines, lines[1:]):
        assert fm.horizontalAdvance(current + following[0]) > 80
    assert overlay.wrap_lines('短句\n继续', 'zh',
                              overlay.cfg['zh_size'], 1000) == ['短句继续']
    zh_fm = QFontMetrics(overlay.font_for('zh', overlay.cfg['zh_size']))
    half_gap = '\u2002'
    assert abs(zh_fm.horizontalAdvance(half_gap) * 2
               - zh_fm.horizontalAdvance('汉')) <= 1
    first, second = '第一句。', '第二句。'
    separate_width = zh_fm.horizontalAdvance(first + half_gap + second) - 1
    assert overlay.wrap_lines(first + second, 'zh',
                              overlay.cfg['zh_size'], separate_width) == [first, second]
    together_width = zh_fm.horizontalAdvance(first + half_gap + second)
    assert overlay.wrap_lines(first + second, 'zh',
                              overlay.cfg['zh_size'], together_width) == [first + half_gap + second]
    assert overlay.height() > overlay._minimum_height()

    combined = TestOverlay(MemoryConfig(
        blocks=1, width=1000, border_enabled=False))
    combined.complete_pending(first, first)
    combined.complete_pending(second, second)
    rows = [(text, lang) for _, text, lang, _ in combined._layout()[0]]
    assert rows == [(first + half_gap + second, 'jp'),
                    (first + half_gap + second, 'zh')]

    empty_first = TestOverlay(MemoryConfig(
        blocks=1, width=1000, border_enabled=False))
    empty_first.complete_pending('お', '')
    empty_first.complete_pending('次。', '下一句。')
    rows = [(text, lang) for _, text, lang, _ in empty_first._layout()[0]]
    assert rows == [('お' + half_gap + '次。', 'jp'),
                    ('下一句。', 'zh')]

    empty_second = TestOverlay(MemoryConfig(
        blocks=1, width=1000, border_enabled=False))
    empty_second.complete_pending('前。', '前句。')
    empty_second.complete_pending('お', '')
    rows = [(text, lang) for _, text, lang, _ in empty_second._layout()[0]]
    assert rows == [('前。' + half_gap + 'お', 'jp'),
                    ('前句。', 'zh')]

    both_empty = TestOverlay(MemoryConfig(
        blocks=1, width=1000, border_enabled=False))
    both_empty.complete_pending('一', '')
    both_empty.complete_pending('二', '')
    rows = [(text, lang) for _, text, lang, _ in both_empty._layout()[0]]
    assert rows == [('一' + half_gap + '二', 'jp')]

    either_full = TestOverlay(MemoryConfig(
        blocks=2, width=1000, border_enabled=False))
    jp1, jp2 = '一。', '二。'
    zh1, zh2 = '中文第一句。', '中文第二句。'
    jp_fm = QFontMetrics(either_full.font_for('jp', either_full.cfg['jp_size']))
    zh_fm = QFontMetrics(either_full.font_for('zh', either_full.cfg['zh_size']))
    available = max(
        jp_fm.horizontalAdvance(jp1 + half_gap + jp2),
        zh_fm.horizontalAdvance(zh1),
        zh_fm.horizontalAdvance(zh2),
    )
    assert zh_fm.horizontalAdvance(zh1 + half_gap + zh2) > available
    either_full.set_cfg(
        'width', available + 2 * either_full._stroke_padding())
    either_full.complete_pending(jp1, zh1)
    either_full.complete_pending(jp2, zh2)
    rows = [(text, lang) for _, text, lang, _ in either_full._layout()[0]]
    assert rows == [(jp1, 'jp'), (zh1, 'zh'),
                    (jp2, 'jp'), (zh2, 'zh')]

    jp_fm = QFontMetrics(combined.font_for('jp', combined.cfg['jp_size']))
    zh_fm = QFontMetrics(combined.font_for('zh', combined.cfg['zh_size']))
    narrow = max(jp_fm.horizontalAdvance(first),
                 zh_fm.horizontalAdvance(first)) + 1
    combined.set_cfg('width', narrow + 2 * combined._stroke_padding())
    assert combined.blocks == [(second, second)]

    two_lines = TestOverlay(MemoryConfig(
        blocks=2,
        width=narrow + 2 * combined._stroke_padding(),
        border_enabled=False))
    two_lines.complete_pending(first, first)
    two_lines.complete_pending(second, second)
    rows = [(text, lang) for _, text, lang, _ in two_lines._layout()[0]]
    assert rows == [(first, 'jp'), (first, 'zh'),
                    (second, 'jp'), (second, 'zh')]

    image = QImage(overlay.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    overlay.cfg['border_color'] = '#FFFFFF'
    overlay.render(image)
    assert image.pixelColor(0, overlay.height() // 2).alpha() > 1
    overlay.close()
    app.processEvents()
    print('正式代码检查通过')


if __name__ == '__main__':
    main()
