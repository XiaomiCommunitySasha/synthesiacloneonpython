import sys
import os
import time
import json
import mido
import pygame
import numpy as np
import bisect
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QListWidget, 
                             QFrame, QStackedWidget, QFileDialog)
from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen

# --- КОНФИГ ---
HISTORY_FILE = "recent_midi.json"
STYLE = """
QMainWindow { background-color: #1a5276; }
#SidePanel { min-width: 250px; }
QPushButton {
    background-color: #2da44e; color: white; font-size: 16px;
    font-weight: bold; border-radius: 4px; padding: 14px; border: none;
}
QPushButton:hover { background-color: #27ae60; }
#BtnSettings { background-color: #0969da; }
#MainContent { background-color: #f6f8fa; border-top-left-radius: 15px; }
QListWidget { background-color: white; border: 1px solid #d0d7de; border-radius: 6px; outline: none; }
QListWidget::item { padding: 15px; border-bottom: 1px solid #ebf0f4; color: #333; }
QListWidget::item:selected { background-color: #ddf4ff; color: #0969da; }
"""

class PianoRoll(QWidget):
    def __init__(self, back_callback):
        super(PianoRoll, self).__init__()
        self.back_callback = back_callback
        self.notes_np = None
        self.start_times = []
        self.is_playing = False
        self.start_time = 0
        self.speed = 350 
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.setFocusPolicy(Qt.StrongFocus)

    def load_midi(self, file_path):
        print("Анализ MIDI...")
        mid = mido.MidiFile(file_path)
        
        note_count = 0
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and msg.velocity > 0:
                    note_count += 1
        
        self.notes_np = np.zeros((note_count, 3), dtype=np.float32)
        active_notes = {}
        idx = 0
        current_time = 0
        
        for msg in mid:
            current_time += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = current_time
            elif (msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)):
                if msg.note in active_notes:
                    st = active_notes.pop(msg.note)
                    if idx < note_count:
                        self.notes_np[idx] = [msg.note, st, current_time - st]
                        idx += 1
        
        self.notes_np = self.notes_np[self.notes_np[:, 1].argsort()]
        self.start_times = self.notes_np[:, 1].tolist()
        self.start_time = time.time()
        self.is_playing = True
        self.timer.start(16)
        print("Загружено {} нот. Запуск!".format(note_count))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            pygame.mixer.music.stop()
            self.timer.stop()
            self.is_playing = False
            self.back_callback()

    def draw_piano(self, painter, w, h, ph):
        kw = w / 88.0
        painter.setPen(QPen(Qt.black, 1))
        # Белые
        for i in range(88):
            x = i * kw
            painter.setBrush(Qt.white)
            painter.drawRect(QRectF(x, h - ph, kw, ph))
        # Черные
        painter.setBrush(QColor(40, 40, 40))
        for i in range(88):
            note_in_octave = (i + 21) % 12
            if note_in_octave in [1, 4, 6, 9, 11]:
                x = i * kw - (kw * 0.35)
                painter.drawRect(QRectF(x, h - ph, kw * 0.7, ph * 0.6))

    def paintEvent(self, event):
        if not self.is_playing: return
        painter = QPainter(self)
        w, h = self.width(), self.height()
        ph = 80 
        painter.fillRect(self.rect(), QColor(10, 10, 10))

        elapsed = time.time() - self.start_time
        kw = w / 88.0
        view_time = (h - ph) / self.speed

        # 1. Поиск видимых нот
        idx_s = bisect.bisect_left(self.start_times, elapsed - 1.0) # Запас снизу
        idx_e = bisect.bisect_right(self.start_times, elapsed + view_time)
        visible = self.notes_np[idx_s:idx_e]

        # 2. ДИНАМИЧЕСКАЯ ОПТИМИЗАЦИЯ
        note_count = len(visible)
        
        # Если нот слишком много, отключаем всё лишнее
        if note_count > 2000:
            painter.setRenderHint(QPainter.Antialiasing, False) # Выкл сглаживание
            # Шаг отрисовки (если нот 500к, рисуем каждую 2-ю или 3-ю на экране)
            step = max(1, note_count // 3000) 
            visible = visible[::step]
        else:
            painter.setRenderHint(QPainter.Antialiasing, True)

        # 3. Отрисовка нот
        painter.setPen(Qt.NoPen)
        for note in visible:
            pitch, start, dur = note
            x = (pitch - 21) * kw
            y_bot = (h - ph) - (start - elapsed) * self.speed
            y_top = (h - ph) - (start + dur - elapsed) * self.speed
            
            # Пропускаем ноты, которые совсем за кадром (на всякий случай)
            if y_bot < 0 or y_top > (h - ph):
                continue

            # Цвет
            color = QColor.fromHsv((pitch * 14) % 360, 180, 255)
            
            # Если нот экстремально много, рисуем плоские прямоугольники без эффектов
            if note_count < 1000 and start <= elapsed <= (start + dur):
                painter.setBrush(QColor(255, 255, 255, 120))
                painter.drawRect(QRectF(x, h - ph - 10, kw-1, 10))

            painter.setBrush(color)
            painter.drawRect(QRectF(x, y_top, kw-1, y_bot-y_top))

        # 4. Клавиатура (всегда поверх)
        self.draw_piano(painter, w, h, ph)
        
        # Полоска прогресса
        if len(self.start_times) > 0:
            prog = min(elapsed / (self.start_times[-1] + 1), 1.0)
            painter.setBrush(QColor(46, 204, 113))
            painter.drawRect(QRectF(0, 0, w * prog, 3))

class SynthesiaApp(QMainWindow):
    def __init__(self):
        super(SynthesiaApp, self).__init__()
        self.setWindowTitle("Synthesia Legacy PRO")
        self.resize(1100, 700)
        
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        
        self.menu_widget = QWidget()
        self.game_widget = PianoRoll(self.go_back)
        self.setup_menu()
        
        self.stack.addWidget(self.menu_widget)
        self.stack.addWidget(self.game_widget)

    def setup_menu(self):
        layout = QHBoxLayout(self.menu_widget)
        layout.setContentsMargins(30, 30, 0, 0)
        
        side = QWidget(); side.setObjectName("SidePanel")
        sl = QVBoxLayout(side)
        logo = QLabel("Synthesia"); logo.setStyleSheet("font-size: 40px; color: white; font-weight: bold; margin-bottom: 30px;")
        
        btn_open = QPushButton("Play a Song")
        btn_open.clicked.connect(self.open_file)
        
        btn_exit = QPushButton("Exit")
        btn_exit.setObjectName("BtnSettings")
        btn_exit.clicked.connect(self.close)
        
        sl.addWidget(logo); sl.addWidget(btn_open); sl.addWidget(btn_exit)
        sl.addStretch()
        
        content = QFrame(); content.setObjectName("MainContent")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(25, 25, 25, 25)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.play_selected)
        cl.addWidget(QLabel("Recently Played"))
        cl.addWidget(self.list_widget)
        self.load_history()

        layout.addWidget(side, 1)
        layout.addWidget(content, 3)

    def load_history(self):
        self.list_widget.clear()
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    paths = json.load(f)
                    for p in paths:
                        self.list_widget.addItem(os.path.basename(p))
                        self.list_widget.item(self.list_widget.count()-1).setData(Qt.UserRole, p)
            except: pass

    def save_history(self, path):
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f: history = json.load(f)
            except: pass
        if path in history: history.remove(path)
        history.insert(0, path)
        with open(HISTORY_FILE, 'w') as f: json.dump(history[:10], f)
        self.load_history()

    def go_back(self):
        self.stack.setCurrentIndex(0)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open MIDI", "", "*.mid *.midi")
        if path: self.start_game(path)

    def play_selected(self, item):
        path = item.data(Qt.UserRole)
        if path: self.start_game(path)

    def start_game(self, path):
        self.save_history(path)
        self.stack.setCurrentIndex(1)
        self.game_widget.load_midi(path)
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    ex = SynthesiaApp()
    ex.show()
    sys.exit(app.exec_())
