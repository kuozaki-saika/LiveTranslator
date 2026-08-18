import os
from PySide6.QtWidgets import (QApplication, QWidget, QMenu,
                               QSystemTrayIcon)
from PySide6.QtCore import Qt
from PySide6.QtGui import (QPainter, QPen, QColor, QPainterPath, QFont,
                           QFontMetrics, QFontDatabase, QPixmap, QIcon)
from config import Config, resource_path

MARGIN = 0    # 文字内边距：不额外留（描边空间由窗口外扩提供）

ICON_PATH = 'assets/icon.png'   # 随包图标（御莉姫）


class SubtitleOverlay(QWidget):
    '''无背景、置顶、可拖动、可调整样式的中日双语字幕悬浮窗。'''

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.blocks = []          # [(jp, zh), ...]，新的在末尾（显示在最下方）
        self.pending = ''         # 正在识别的日文（实时上屏，整段翻译完成后并入 blocks）
        self._drag_pos = None
        self._fonts = {}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.load_fonts()
        self.setWindowIcon(self.load_app_icon())
        self.resize(int(cfg['width']), 100)   # 初始高度，add_block 后自动贴合
        self.move(int(cfg['x']), int(cfg['y']))
        self.apply_click_through()
        self.setup_tray()

    # ---------- 字体 ----------
    JP_FAMILY = 'Source Han Serif JP'
    ZH_FAMILY = 'Source Han Serif SC'

    def load_fonts(self):
        """注册随程序打包的全部字体字重（思源宋体 SC/JP 各 7 档）"""
        import glob
        for p in glob.glob(os.path.join(resource_path(os.path.join('assets', 'fonts')), '*.otf')):
            QFontDatabase.addApplicationFont(p)

    def _margin(self):
        """文字位置固定贴边（0）；描边空间由窗口向外扩提供"""
        return MARGIN

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
        if len(self.blocks) > self.cfg['blocks']:
            self.blocks = self.blocks[-self.cfg['blocks']:]
        self.update()
        self.resize_to_fit()

    def update_pending(self, jp):
        """识别实时上屏：更新正在识别的日文行"""
        if jp != self.pending:
            self.pending = jp
            self.update()
            self.resize_to_fit()

    def complete_pending(self, jp, zh):
        """整段翻译完成：并入 blocks；若翻译期间新段已开始则保留其剩余部分"""
        self.add_block(jp, zh)
        if self.pending.startswith(jp):
            self.pending = self.pending[len(jp):]

    def clear(self):
        self.blocks.clear()
        self.pending = ''
        self.update()

    # ---------- 排版与绘制 ----------
    def _layout(self):
        jp_size = int(self.cfg['jp_size'])
        zh_size = int(self.cfg['zh_size'])
        sw = int(self.cfg['stroke_width'])
        w = self.width() - 2 * (self._margin() + sw)   # 左右给描边留空间
        y = self._margin()
        rows = []   # (y, text, lang, size)：每行精确起始坐标
        n = len(self.blocks)
        for idx, (jp, zh) in enumerate(self.blocks):
            jp_f = self.font_for('jp', jp_size)
            zh_f = self.font_for('zh', zh_size)
            jp_h = QFontMetrics(jp_f).height()
            zh_h = QFontMetrics(zh_f).height()
            for ln in self.wrap_lines(jp, 'jp', jp_size, w):
                rows.append((y, ln, 'jp', jp_size))
                y += jp_h
            for ln in self.wrap_lines(zh, 'zh', zh_size, w):
                rows.append((y, ln, 'zh', zh_size))
                y += zh_h
        # 实时识别行（未翻译的日文，显示在最下方）
        if self.pending:
            jp_f = self.font_for('jp', jp_size)
            jp_h = QFontMetrics(jp_f).height()
            for ln in self.wrap_lines(self.pending, 'jp', jp_size, w):
                rows.append((y, ln, 'jp', jp_size))
                y += jp_h
        # 最后一行高度去掉 leading（字形底部即行底，不留额外空隙）
        if rows:
            lf = self.font_for(rows[-1][2], rows[-1][3])
            fm = QFontMetrics(lf)
            leading = fm.height() - (fm.ascent() + fm.descent())
            total = y - leading + self._margin() + 2 * sw
        else:
            total = self._margin() * 2 + 2 * sw
        return rows, total

    def resize_to_fit(self):
        _, total = self._layout()
        h = max(int(total), 60)   # 空窗口最小高度
        if h != self.height():
            self.setFixedHeight(h)
    def wrap_lines(self, text, lang, size, max_w):
        fm = QFontMetrics(self.font_for(lang, size))
        lines, cur = [], ''
        for ch in text:
            if ch == '\n':
                lines.append(cur); cur = ''
                continue
            if cur and fm.horizontalAdvance(cur + ch) > max_w:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or ['']

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

        # 边框（直角正方形，可开关/调色/调粗）
        if self.cfg['border_enabled'] and int(self.cfg['border_width']) > 0:
            bw = int(self.cfg['border_width'])
            ins = 2 + bw // 2          # 边框内缩量：基础2px + 半粗度（不贴边）
            p.setPen(QPen(QColor(self.cfg['border_color']), bw, Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(ins, ins, self.width() - 2 * ins - 1, self.height() - 2 * ins - 1)

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
            self.resize(int(value), self.height())
        elif key in ('blocks', 'jp_size', 'zh_size', 'font_weight', 'stroke_width', 'border_width'):
            self.resize_to_fit()
        self.update()

    def toggle_border(self):
        self.cfg['border_enabled'] = not self.cfg['border_enabled']
        self.update()

    def toggle_stroke(self):
        self.cfg['stroke_enabled'] = not self.cfg['stroke_enabled']
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

    def _double_submenu(self, menu, title, key, lo, hi, step, invert=False):
        """小数数值项：右侧展开子菜单，内嵌 QDoubleSpinBox；invert 时界面值 = 1 - 存储值"""
        from PySide6.QtWidgets import QWidgetAction, QDoubleSpinBox
        sub = menu.addMenu(title)
        wa = QWidgetAction(sub)
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setDecimals(2)
        sb.setValue(1.0 - float(self.cfg[key]) if invert else float(self.cfg[key]))
        sb.setFixedWidth(self._spin_width_for(999))   # 三位数字宽度（0.50 完整显示）
        if invert:
            sb.valueChanged.connect(lambda v: self.set_cfg(key, round(1.0 - v, 2)))
        else:
            sb.valueChanged.connect(lambda v: self.set_cfg(key, v))
        wa.setDefaultWidget(sb)
        sub.addAction(wa)

    def _bw_color_submenu(self, menu, title, key):
        """颜色：右侧展开白/黑色块 + 自定义▶（内嵌 R/G/B 输入框，不弹框）"""
        from PySide6.QtWidgets import (QWidgetAction, QSpinBox, QLabel,
                                       QHBoxLayout, QWidget)
        sub = menu.addMenu(title)
        for name, hexv, edge in (('白色', '#FFFFFF', '#888888'),
                                 ('黑色', '#000000', '#FFFFFF')):
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
        self._num_submenu(sub, '静音检测（ms）', 'min_silence_ms', 1, 999, 20)   # 下限1ms：0会导致VAD不切段不出字
        self._double_submenu(sub, '语音判定阈值', 'no_speech_threshold', 0.00, 1.00, 0.05, invert=True)   # 界面 1-阈值：0=不判定（最松），1=最严
        a = sub.addAction('更改后重启生效')
        m.addSeparator()
        a = m.addAction('清空字幕')
        a.triggered.connect(self.clear)
        m.exec(e.globalPos())

    # ---------- Windows 命中测试（防透明区域穿透） ----------
    def nativeEvent(self, eventType, message):
        try:
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
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