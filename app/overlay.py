import os, re
from PySide6.QtWidgets import (QApplication, QWidget, QMenu,
                               QSystemTrayIcon)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (QPainter, QPen, QColor, QPainterPath, QFont,
                           QFontMetrics, QFontDatabase, QPixmap, QIcon,
                           QTextLayout, QTextOption)
from config import Config, resource_path

MARGIN = 0    # 文字内边距：不额外留（描边空间由窗口外扩提供）
BORDER_GAP = 3
SENTENCE_GAP = '\u2002'

ICON_PATH = 'assets/icon.png'   # 随包图标（御莉姫）


class SubtitleOverlay(QWidget):
    '''无背景、置顶、可拖动、可调整样式的中日双语字幕悬浮窗。'''

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.blocks = []          # [(jp, zh), ...]，新的在末尾（显示在最下方）
        self._live = ''           # 当前仍在识别的日文
        self._waiting = []        # 已确认、正在按顺序等待翻译的日文
        self._drag_pos = None
        self._fonts = {}
        self._layout_cache = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.load_fonts()
        self.setWindowIcon(self.load_app_icon())
        self.resize(self._window_width(), self._minimum_height())
        self.move(int(cfg['x']), int(cfg['y']))
        self.apply_click_through()
        self.setup_tray()

    # ---------- 字体 ----------
    JP_FAMILY = 'Source Han Serif JP'
    ZH_FAMILY = 'Source Han Serif SC'

    def load_fonts(self):
        """注册随程序打包的全部字体字重（思源宋体 SC/JP 各 5 档）"""
        import glob
        for p in glob.glob(os.path.join(resource_path(os.path.join('assets', 'fonts')), '*.otf')):
            QFontDatabase.addApplicationFont(p)

    def font_for(self, lang, size):
        key = (lang, size, self.cfg['font_weight'])
        if key not in self._fonts:
            family = self.JP_FAMILY if lang == 'jp' else self.ZH_FAMILY
            f = QFont(family, int(size))
            f.setWeight(QFont.Weight(int(self.cfg['font_weight'])))   # 字重（思源宋体多字重）
            f.setStyleStrategy(QFont.PreferAntialias)
            self._fonts[key] = f
        return self._fonts[key]

    # ---------- 数据 ----------
    def add_block(self, jp, zh):
        self.blocks.append((jp, zh))
        self._trim_blocks()
        self.resize_to_fit()
        self.update()

    def update_pending(self, jp):
        """保存实时识别；有完整句等待翻译时暂不上屏。"""
        if jp != self._live:
            self._live = jp
            if self._waiting:
                return
            self.resize_to_fit()
            self.update()

    def finalize_pending(self, jp):
        """完整句按顺序等待翻译；屏幕只显示最早的一句。"""
        self._live = ''
        if jp:
            self._waiting.append(jp)
        if len(self._waiting) == 1:
            self.resize_to_fit()
            self.update()

    def complete_pending(self, jp, zh):
        """翻译完成：加入正式字幕，再显示下一句或最新实时识别。"""
        if self._waiting and self._waiting[0] == jp:
            self._waiting.pop(0)
        elif jp in self._waiting:
            self._waiting.remove(jp)
        self.add_block(jp, zh)

    def clear(self):
        self.blocks.clear()
        self._live = ''
        self._waiting.clear()
        self.resize_to_fit()
        self.update()

    # ---------- 排版与绘制 ----------
    def _pending_text(self):
        return self._waiting[0] if self._waiting else self._live

    def _stroke_padding(self):
        if not self.cfg['stroke_enabled']:
            return 0
        return (int(self.cfg['stroke_width']) + 1) // 2 + 1

    def _border_padding(self):
        if not self.cfg['border_enabled'] or int(self.cfg['border_width']) <= 0:
            return 0
        return BORDER_GAP + int(self.cfg['border_width'])

    def _window_width(self):
        return int(self.cfg['width']) + 2 * self._border_padding()

    def _text_width(self):
        return max(1, int(self.cfg['width'])
                   - 2 * (MARGIN + self._stroke_padding()))

    def _packed_blocks(self):
        width = self._text_width()
        jp_fm = QFontMetrics(self.font_for('jp', int(self.cfg['jp_size'])))
        zh_fm = QFontMetrics(self.font_for('zh', int(self.cfg['zh_size'])))
        packed = []
        for jp, zh in self.blocks:
            if packed and packed[-1][1] and zh:
                old_jp, old_zh = packed[-1]
                joined_jp = old_jp + SENTENCE_GAP + jp
                joined_zh = old_zh + SENTENCE_GAP + zh
                if (jp_fm.horizontalAdvance(joined_jp) <= width
                        and zh_fm.horizontalAdvance(joined_zh) <= width):
                    packed[-1] = (joined_jp, joined_zh)
                    continue
            packed.append((jp, zh))
        return packed

    def _trim_blocks(self):
        limit = max(1, int(self.cfg['blocks']))
        while len(self.blocks) > 1 and len(self._packed_blocks()) > limit:
            self.blocks.pop(0)

    def _minimum_height(self):
        jp = QFontMetrics(self.font_for('jp', int(self.cfg['jp_size'])))
        zh = QFontMetrics(self.font_for('zh', int(self.cfg['zh_size'])))
        return int(jp.height() + zh.height() - zh.leading()
                   + 2 * (MARGIN + self._stroke_padding()
                          + self._border_padding()))

    def _layout(self):
        jp_size = int(self.cfg['jp_size'])
        zh_size = int(self.cfg['zh_size'])
        sw = self._stroke_padding()
        border = self._border_padding()
        pending = self._pending_text()
        key = (self.width(), int(self.cfg['width']), tuple(self.blocks), pending,
               jp_size, zh_size, int(self.cfg['font_weight']), sw, border)
        if self._layout_cache and self._layout_cache[0] == key:
            return self._layout_cache[1]
        w = self._text_width()
        y = border + MARGIN
        rows = []   # (y, text, lang, size)：每行精确起始坐标
        jp_h = QFontMetrics(self.font_for('jp', jp_size)).height()
        zh_h = QFontMetrics(self.font_for('zh', zh_size)).height()
        for jp, zh in self._packed_blocks():
            for ln in self.wrap_lines(jp, 'jp', jp_size, w):
                rows.append((y, ln, 'jp', jp_size))
                y += jp_h
            if zh:   # 未译块只有日文行
                for ln in self.wrap_lines(zh, 'zh', zh_size, w):
                    rows.append((y, ln, 'zh', zh_size))
                    y += zh_h
        # 实时识别行（未翻译的日文，显示在最下方）
        if pending:
            for ln in self.wrap_lines(pending, 'jp', jp_size, w):
                rows.append((y, ln, 'jp', jp_size))
                y += jp_h
        # 最后一行高度去掉 leading（字形底部即行底，不留额外空隙）
        if rows:
            lf = self.font_for(rows[-1][2], rows[-1][3])
            fm = QFontMetrics(lf)
            leading = fm.height() - (fm.ascent() + fm.descent())
            total = y - leading + MARGIN + 2 * sw + border
        else:
            total = 2 * (border + MARGIN + sw)
        result = (rows, total)
        self._layout_cache = (key, result)
        return result

    def resize_to_fit(self):
        _, total = self._layout()
        h = max(int(total), self._minimum_height())
        if h != self.height():
            self.setFixedHeight(h)

    def wrap_lines(self, text, lang, size, max_w):
        font = self.font_for(lang, size)
        fm = QFontMetrics(font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAnywhere)
        sentences = []
        for part in text.replace('\r', '').replace('\n', '').split(SENTENCE_GAP):
            sentences.extend(re.findall(
                r'.*?[。！？!?…]+[」』”’）】》〉]*|.+$', part))
        if not sentences:
            return []
        lines, current = [], ''
        for sentence in sentences:
            candidate = current + (SENTENCE_GAP if current else '') + sentence
            if fm.horizontalAdvance(candidate) <= max_w:
                current = candidate
                continue
            if current:
                lines.append(current)
            layout = QTextLayout(sentence, font)
            layout.setTextOption(option)
            wrapped = []
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, max_w))
                start = line.textStart()
                wrapped.append(sentence[start:start + line.textLength()])
            layout.endLayout()
            lines.extend(wrapped[:-1])
            current = wrapped[-1]
        if current:
            lines.append(current)
        return lines

    def draw_line(self, p, text, lang, size, fcolor, scolor, sw, x, y):
        f = self.font_for(lang, size)
        fm = QFontMetrics(f)
        path = QPainterPath()
        path.addText(x, y + fm.ascent(), f, text)
        # 两遍绘制：先描边、后填充（此环境光栅后端先填后描会丢失填充）
        if sw > 0 and self.cfg['stroke_enabled']:
            p.setPen(QPen(scolor, sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(fcolor)
        p.drawPath(path)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # 命中底：整窗铺 alpha=1（肉眼不可见），否则 Windows 分层窗口
        # 对全透明像素区域直接鼠标穿透到下层窗口
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        fcolor = QColor(self.cfg['font_color'])
        scolor = QColor(self.cfg['stroke_color'])
        sw = int(self.cfg['stroke_width'])
        rows, total = self._layout()

        # 垂直居中 + 水平居中：每行文字在窗口宽度内左右居中
        offset = max(0, (self.height() - total) // 2)
        for (ly, text, lang, size) in rows:
            f = self.font_for(lang, size)
            tw = QFontMetrics(f).horizontalAdvance(text)
            x = (self.width() - tw) // 2
            y = offset + ly
            self.draw_line(p, text, lang, size, fcolor, scolor, sw, x, y)

        # 边框内缘距字幕区固定 3px，加粗部分全部向窗口外侧增长
        if self.cfg['border_enabled'] and int(self.cfg['border_width']) > 0:
            bw = int(self.cfg['border_width'])
            p.setPen(QPen(QColor(self.cfg['border_color']), bw, Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            half = bw / 2.0
            p.drawRect(QRectF(half, half,
                              self.width() - bw - 1,
                              self.height() - bw - 1))

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag_pos is not None:
            self._drag_pos = None
            g = self.frameGeometry()
            self.cfg['x'] = g.x()
            self.cfg['y'] = g.y()

    def toggle_click_through(self):
        self.cfg['click_through'] = not self.cfg['click_through']
        self.apply_click_through()
        # 同步托盘菜单勾选状态
        if hasattr(self, 'tray_through'):
            self.tray_through.setChecked(self.cfg['click_through'])

    def apply_click_through(self):
        if self.cfg['click_through']:
            self.setWindowFlag(Qt.WindowTransparentForInput, True)
        else:
            self.setWindowFlag(Qt.WindowTransparentForInput, False)
        self.show()

    def set_cfg(self, key, value):
        self.cfg[key] = value
        if key == 'width':
            self.resize(self._window_width(), self.height())
            self._trim_blocks()
            self.resize_to_fit()
        elif key == 'border_width':
            self._resize_for_border()
        elif key in ('blocks', 'jp_size', 'zh_size', 'font_weight', 'stroke_width'):
            self._trim_blocks()
            self.resize_to_fit()
        self.update()

    def _resize_for_border(self):
        center_x = self.x() + self.width() / 2
        center_y = self.y() + self.height() / 2
        self.resize(self._window_width(), self.height())
        self.resize_to_fit()
        self.move(round(center_x - self.width() / 2),
                  round(center_y - self.height() / 2))

    def toggle_border(self):
        self.cfg['border_enabled'] = not self.cfg['border_enabled']
        self._resize_for_border()
        self.update()

    def toggle_stroke(self):
        self.cfg['stroke_enabled'] = not self.cfg['stroke_enabled']
        self._trim_blocks()
        self.resize_to_fit()
        self.update()

    @staticmethod
    def _spin_width_for(hi):
        """精确宽度：按范围最大值位数 + 60px 固定开销（箭头+边框+余量）"""
        from PySide6.QtWidgets import QSpinBox
        fm = QFontMetrics(QSpinBox().font())
        return fm.horizontalAdvance(str(hi)) + 60

    def _num_submenu(self, menu, title, key, lo, hi, step=1):
        """数值项：右侧展开子菜单，内嵌输入框；宽度按上限位数固定（1/2/3/4位档）"""
        from PySide6.QtWidgets import QWidgetAction, QSpinBox
        sub = menu.addMenu(title)
        wa = QWidgetAction(sub)
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setValue(int(self.cfg[key]))
        sb.setFixedWidth(self._spin_width_for(hi))   # 按上限位数固定宽度
        sb.valueChanged.connect(lambda v: self.set_cfg(key, v))
        wa.setDefaultWidget(sb)
        sub.addAction(wa)

    def _weight_submenu(self, menu):
        """字重：思源宋体静态字重只有 400/500/600/700/900（无 800），下拉列出可用档位"""
        from PySide6.QtWidgets import QWidgetAction, QComboBox
        sub = menu.addMenu('字重')
        wa = QWidgetAction(sub)
        cb = QComboBox()
        for w in (400, 500, 600, 700, 900):
            cb.addItem(str(w), w)
        val = int(self.cfg['font_weight'])
        idx = cb.findData(val)
        if idx < 0:
            idx = cb.findData(700)   # 旧配置存了 800 等无效值：就近落到 700
        cb.setCurrentIndex(idx)
        cb.currentIndexChanged.connect(lambda i: self.set_cfg('font_weight', cb.itemData(i)))
        wa.setDefaultWidget(cb)
        sub.addAction(wa)

    def _bw_color_submenu(self, menu, title, key):
        """颜色：右侧展开白/黑色块 + 自定义▶（内嵌 R/G/B 输入框，不弹框）"""
        sub = menu.addMenu(title)
        for hexv, edge in (('#FFFFFF', '#888888'),
                           ('#000000', '#FFFFFF')):
            pix = QPixmap(16, 16)
            pix.fill(QColor(hexv))
            p = QPainter(pix)
            p.setPen(QColor(edge))
            p.drawRect(0, 0, 15, 15)
            p.end()
            a = sub.addAction(QIcon(pix), '')
            a.triggered.connect(lambda _, h=hexv: self.set_cfg(key, h))
        sub.addSeparator()
        # 自定义 ▶：右侧再展开，内嵌 R/G/B 三个输入框
        sub2 = sub.addMenu('自定义')
        old = QColor(self.cfg[key])
        r = self._rgb_row(sub2, 'R', old.red())
        g = self._rgb_row(sub2, 'G', old.green())
        b = self._rgb_row(sub2, 'B', old.blue())
        def apply():
            self.set_cfg(key, '#%02X%02X%02X' % (r.value(), g.value(), b.value()))
        r.valueChanged.connect(lambda _: apply())
        g.valueChanged.connect(lambda _: apply())
        b.valueChanged.connect(lambda _: apply())

    def _rgb_row(self, menu, letter, val):
        """子菜单里的一行：标签 + 输入框"""
        from PySide6.QtWidgets import (QWidgetAction, QSpinBox, QLabel,
                                       QHBoxLayout, QWidget)
        wa = QWidgetAction(menu)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 0, 4, 0)
        h.addWidget(QLabel(letter))
        sb = QSpinBox()
        sb.setRange(0, 255)
        sb.setValue(val)
        sb.setFixedWidth(self._spin_width_for(255))   # RGB 3 位自动
        h.addWidget(sb)
        wa.setDefaultWidget(w)
        menu.addAction(wa)
        return sb

    def contextMenuEvent(self, e):
        m = QMenu(self)
        # 数值项：右侧展开子菜单（当前值 + 输入…）
        self._num_submenu(m, '字幕条数', 'blocks', 1, 9)
        self._num_submenu(m, '字幕宽度', 'width', 100, 9999, 100)
        self._num_submenu(m, '日文大小', 'jp_size', 1, 99)
        self._num_submenu(m, '中文大小', 'zh_size', 1, 99)
        self._weight_submenu(m)
        self._bw_color_submenu(m, '字体颜色', 'font_color')
        a = m.addAction('描边')
        a.setCheckable(True)
        a.setChecked(self.cfg['stroke_enabled'])
        a.triggered.connect(self.toggle_stroke)
        self._num_submenu(m, '描边宽度', 'stroke_width', 1, 9)
        self._bw_color_submenu(m, '描边颜色', 'stroke_color')
        m.addSeparator()
        a = m.addAction('边框')
        a.setCheckable(True)
        a.setChecked(self.cfg['border_enabled'])
        a.triggered.connect(self.toggle_border)
        self._num_submenu(m, '边框宽度', 'border_width', 1, 99)
        self._bw_color_submenu(m, '边框颜色', 'border_color')
        a = m.addAction('穿透')
        a.setCheckable(True)
        a.setChecked(self.cfg['click_through'])
        a.triggered.connect(self.toggle_click_through)
        m.addSeparator()
        sub = m.addMenu('其他设置')
        self._num_submenu(sub, '句末缓冲（ms）', 'min_silence_ms', 0, 999, 20)   # 说完后再等这么多确认结束
        sub.addAction('（更改后重启生效）')
        m.addSeparator()
        a = m.addAction('清空字幕')
        a.triggered.connect(self.clear)
        m.exec(e.globalPos())

    # ---------- Windows 命中测试（防透明区域穿透） ----------
    def nativeEvent(self, eventType, message):
        try:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:      # WM_NCHITTEST
                return True, 1             # HTCLIENT：整窗可命中
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    # ---------- 图标 ----------
    def load_app_icon(self):
        """随包图标（assets/icon.png，打包保证存在）"""
        return QIcon(resource_path(ICON_PATH))

    # ---------- 托盘 ----------
    def setup_tray(self):
        pix = self.load_app_icon().pixmap(64, 64)
        self.tray = QSystemTrayIcon(pix, self)
        tm = QMenu()
        a = tm.addAction('显示/关闭字幕')
        a.triggered.connect(self.toggle_visible)
        a = tm.addAction('穿透')
        a.setCheckable(True)
        a.setChecked(self.cfg['click_through'])
        a.triggered.connect(self.toggle_click_through)
        self.tray_through = a
        tm.addSeparator()
        a = tm.addAction('退出')
        a.triggered.connect(QApplication.instance().quit)
        self.tray.setContextMenu(tm)
        self.tray.show()

    def toggle_visible(self):
        self.setVisible(not self.isVisible())
