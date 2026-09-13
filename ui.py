# sorry for the horrible formatting (ive been told)
# thank you neuralnine my goat for the 45 minute pyside6 video 🥺
import io
import json
import hashlib
import os
import sys
import threading
import subprocess
import ctypes
import time
import webbrowser
import re
from difflib import SequenceMatcher
from ctypes import wintypes
from datetime import datetime
from urllib.parse import urlparse, urljoin
import requests
from PIL import Image, ImageStat, ImageOps
from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject, QRectF, QAbstractNativeEventFilter, QEvent, QSize
from PySide6.QtGui import QColor, QPixmap, QPainter, QPainterPath, QPen, QKeySequence, QMovie
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFrame, QVBoxLayout, QHBoxLayout, QSlider, QComboBox, QStackedWidget, QListWidget, QScrollArea, QDialog, QAbstractButton, QLineEdit, QKeySequenceEdit, QTextEdit, QFileDialog, QGridLayout
from config import load_settings, save_settings, data_dir, image_cache_dir, supabase_url, lastfm_setup_url, app_version
from database import StatsDB
from media import MediaPoller, play_pause as media_play_pause, next_track as media_next_track, previous_track as media_previous_track, volume_up as media_volume_up, volume_down as media_volume_down
from account import CloudClient, AuthDialog, MFASetupDialog, MFADisableDialog, DeleteAccountDialog, ChangePasswordDialog, SuspensionDialog, DeveloperDeleteNoticeDialog, DeveloperAccessViolation
brand = "Amethyst"
version = app_version
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CTRL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN=0x0008
MOD_NOREPEAT = 0x4000; HOTKEY_ID = 2119

def safe_image_url(url):
    try:
        parsed=urlparse(str(url or ""))
        host=(parsed.hostname or "").lower()
        supabase_host=(urlparse(supabase_url).hostname or "").lower()
        image_hosts={"e-cdns-images.dzcdn.net","cdns-images.dzcdn.net","cdn-images.dzcdn.net"}
        return parsed.scheme=="https" and (host==supabase_host or host=="i.scdn.co" or host.endswith(".scdn.co") or host in image_hosts)
    except Exception:
        return False

def _media_text(value):
    return re.sub(r"[^a-z0-9]+"," ",str(value or "").casefold()).strip()

def _match_score(a,b):
    a=_media_text(a); b=_media_text(b)
    if not a or not b:return 0.0
    if a==b:return 1.0
    if a in b or b in a:return 0.9
    return SequenceMatcher(None,a,b).ratio()

def _deezer_search(kind,query,limit=10):
    try:
        response=requests.get(f"https://api.deezer.com/search/{kind}",params={"q":query,"limit":limit},timeout=(4,7))
        response.raise_for_status(); body=response.json()
        return body.get("data") or [] if isinstance(body,dict) else []
    except Exception:
        return []

def lookup_artist_image(name):
    rows=_deezer_search("artist",name,8); best=None; best_score=0.0
    for row in rows:
        score=_match_score(name,row.get("name"))
        if score>best_score:
            best=row; best_score=score
    if not best or best_score<0.72:return ""
    return str(best.get("picture_big") or best.get("picture_xl") or best.get("picture_medium") or "")

def lookup_track_art(artist,title,album=""):
    query=f'{artist} {title}'.strip(); rows=_deezer_search("track",query,12); best=None; best_score=0.0
    for row in rows:
        row_artist=(row.get("artist") or {}).get("name") or ""; row_album=(row.get("album") or {}).get("title") or ""
        title_score=_match_score(title,row.get("title_short") or row.get("title")); artist_score=_match_score(artist,row_artist)
        if title_score<0.66 or artist_score<0.58:continue
        album_score=_match_score(album,row_album) if album else 0.75
        score=(title_score*.55)+(artist_score*.35)+(album_score*.10)
        if score>best_score:
            best=row; best_score=score
    if not best:return ""
    album_data=best.get("album") or {}
    return str(album_data.get("cover_big") or album_data.get("cover_xl") or album_data.get("cover_medium") or "")

def valid_image_bytes(data,max_pixels=25000000):
    try:
        with Image.open(io.BytesIO(data)) as img:return img.width>0 and img.height>0 and img.width*img.height<=max_pixels
    except Exception:return False

def download_image_bytes(url,max_bytes=2*1024*1024):
    current=str(url or "")
    try:
        for _ in range(5):
            if not safe_image_url(current):return b""
            response=requests.get(current,timeout=(4,8),stream=True,allow_redirects=False)
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get("location") or ""; response.close()
                current=urljoin(current,location)
                if not safe_image_url(current):return b""
                continue
            try:
                response.raise_for_status()
                content_type=str(response.headers.get("content-type") or "").lower()
                if content_type and not content_type.startswith("image/"):return b""
                try:
                    size=int(response.headers.get("content-length") or 0)
                    if size>max_bytes:return b""
                except Exception:pass
                data=bytearray()
                for chunk in response.iter_content(65536):
                    if not chunk:continue
                    data.extend(chunk)
                    if len(data)>max_bytes:return b""
                data=bytes(data)
                return data if valid_image_bytes(data) else b""
            finally:response.close()
        return b""
    except Exception:return b""

class Signals(QObject):
    art = Signal(bytes, str)
    status = Signal(str)
    artist_images=Signal(object)
    track_art=Signal(str, str)
    stat_image = Signal(object, bytes)
    leaderboard = Signal(object, object, bool, bool)
    developer = Signal(str, object)
    active_users = Signal(int)
    profile = Signal(str, object)
    public_profile = Signal(object)
    history_synced = Signal(int)
    security_check = Signal(bool)
    local_playback = Signal(object)
    lastfm_event = Signal(str, object)

class ToggleSwitch(QAbstractButton):
    def __init__(self, checked=False, accent="#b64fff"):
        super().__init__(); self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.accent = QColor(accent)

    def set_accent(self, colour):
        self.accent = QColor(colour)
        self.update()

    def paintEvent(self, event):
        painter= QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.accent if self.isChecked() else QColor("#3a3a40"))
        painter.drawRoundedRect(QRectF(0, 2, 44, 20), 10, 10); painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(23 if self.isChecked() else 3, 3, 18, 18))

class MediaButton(QAbstractButton):
    def __init__(self, icon, accent="#b64fff", size=38, filled=False):
        super().__init__()
        self.icon = icon
        self.accent=QColor(accent)
        self.active = False
        self.repeat_mode = "off"
        self.filled = filled
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)

    def set_icon(self, icon):
        self.icon = icon; self.update()

    def set_active(self, active):
        self.active = bool(active)
        self.update()

    def set_repeat_mode(self, mode):
        self.repeat_mode = str(mode or "off")
        if self.icon == "repeat":
            self.active = self.repeat_mode != "off"
        self.update()

    def set_accent(self, colour):
        self.accent = QColor(colour)
        self.update()

    def set_button_size(self, size):
        self.setFixedSize(size, size)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        s=min(w, h)

        if self.filled:
            p.setPen(Qt.NoPen); p.setBrush(self.accent)
            p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
            colour = QColor("#090a0d")
        else:
            if self.underMouse():
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#29292f"))
                p.drawEllipse(QRectF(1, 1, w - 2, h - 2))
            colour = self.accent if self.active else QColor("#ddddE4")

        pen = QPen(colour, max(1.7, s * .055), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(colour)

        cx, cy = w / 2, h / 2
        u = s / 38

        if self.icon == "play":
            path = QPainterPath()
            path.moveTo(cx - 5*u, cy - 8*u); path.lineTo(cx + 8*u, cy)
            path.lineTo(cx - 5*u, cy + 8*u)
            path.closeSubpath()
            p.drawPath(path)

        elif self.icon == "pause":
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(cx - 7*u, cy - 8*u, 4*u, 16*u), 1*u, 1*u)
            p.drawRoundedRect(QRectF(cx + 3*u, cy - 8*u, 4*u, 16*u), 1*u, 1*u)

        elif self.icon in ("next", "previous"):
            flip = -1 if self.icon == "previous" else 1
            p.setPen(Qt.NoPen)
            path=QPainterPath()
            path.moveTo(cx - 7*u*flip, cy - 8*u)
            path.lineTo(cx + 4*u*flip, cy); path.lineTo(cx - 7*u*flip, cy + 8*u)
            path.closeSubpath()
            p.drawPath(path)
            x = cx + 7*u*flip
            p.drawRoundedRect(QRectF(x - 1.5*u, cy - 8*u, 3*u, 16*u), 1*u, 1*u)

        elif self.icon == "shuffle":
            p.setBrush(Qt.NoBrush)
            left, right = cx - 10*u, cx + 9*u
            top, bottom = cy - 6*u, cy + 6*u
            path=QPainterPath(); path.moveTo(left,top); path.cubicTo(cx-2*u,top,cx+1*u,bottom,right,bottom); p.drawPath(path)
            path=QPainterPath(); path.moveTo(left,bottom); path.cubicTo(cx-2*u,bottom,cx+1*u,top,right,top); p.drawPath(path)
            p.drawLine(QPoint(int(right-3*u),int(bottom-3*u)),QPoint(int(right),int(bottom)))
            p.drawLine(QPoint(int(right-3*u),int(bottom+3*u)),QPoint(int(right),int(bottom)))
            p.drawLine(QPoint(int(right-3*u),int(top-3*u)),QPoint(int(right),int(top)))
            p.drawLine(QPoint(int(right-3*u),int(top+3*u)),QPoint(int(right),int(top)))

        elif self.icon == "repeat":
            p.setBrush(Qt.NoBrush)
            left, right = cx - 10*u, cx + 10*u
            top, bottom = cy - 6*u, cy + 6*u
            p.drawLine(QPoint(int(left + 3*u), int(top)), QPoint(int(right - 2*u), int(top)))
            p.drawLine(QPoint(int(right - 2*u), int(top)), QPoint(int(right), int(top + 3*u)))
            p.drawLine(QPoint(int(right), int(top + 3*u)), QPoint(int(right - 3*u), int(top + 6*u)))
            p.drawLine(QPoint(int(right - 3*u), int(bottom)), QPoint(int(left + 2*u), int(bottom)))
            p.drawLine(QPoint(int(left + 2*u), int(bottom)), QPoint(int(left), int(bottom - 3*u)))
            p.drawLine(QPoint(int(left), int(bottom - 3*u)), QPoint(int(left + 3*u), int(bottom - 6*u)))
            if self.repeat_mode == "track":
                font=p.font(); font.setPixelSize(max(7,int(8*u))); font.setBold(True); p.setFont(font)
                p.drawText(QRectF(cx+4*u,cy-9*u,8*u,9*u),Qt.AlignCenter,"1")

        elif self.icon in ("volume_up", "volume_down"):
            p.setPen(Qt.NoPen)
            path = QPainterPath(); path.moveTo(cx - 8*u, cy - 4*u)
            path.lineTo(cx - 4*u, cy - 4*u)
            path.lineTo(cx + 1*u, cy - 8*u)
            path.lineTo(cx + 1*u, cy + 8*u)
            path.lineTo(cx - 4*u, cy + 4*u)
            path.lineTo(cx - 8*u, cy + 4*u)
            path.closeSubpath()
            p.drawPath(path); p.setPen(pen)
            if self.icon == "volume_up":
                p.drawArc(QRectF(cx - 1*u, cy - 7*u, 14*u, 14*u), -55*16, 110*16)
            else:
                p.drawLine(QPoint(int(cx + 6*u), int(cy - 3*u)), QPoint(int(cx + 6*u), int(cy + 3*u)))

class CleanDialog(QDialog):
    def __init__(self, parent, title, text, accent, confirm="Continue", cancel=True, cancel_text="Cancel"):
        super().__init__(parent)
        self.ok= False
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10); card = QFrame()
        card.setObjectName("dialogCard")
        root.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        name=QLabel(title)
        name.setObjectName("dialogTitle")
        body = QLabel(text)
        body.setObjectName("dialogText"); body.setWordWrap(True)
        layout.addWidget(name)
        layout.addWidget(body)
        layout.addSpacing(4)

        row = QHBoxLayout()
        row.addStretch()
        if cancel:
            no = QPushButton(cancel_text)
            no.clicked.connect(self.reject)
            row.addWidget(no)
        yes = QPushButton(confirm)
        yes.setObjectName("primaryButton"); yes.clicked.connect(self.accept_ok)
        row.addWidget(yes)
        layout.addLayout(row)

        self.setStyleSheet(dialog_style(accent))

    def accept_ok(self):
        self.ok = True
        self.accept()

class LastFMWaitingDialog(QDialog):
    def __init__(self,parent,accent):
        super().__init__(parent); self.connected=False
        self.setModal(True); self.setWindowFlags(Qt.Dialog|Qt.FramelessWindowHint); self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedWidth(470)
        root=QVBoxLayout(self); root.setContentsMargins(10,10,10,10); card=QFrame(); card.setObjectName("dialogCard"); root.addWidget(card)
        layout=QVBoxLayout(card); layout.setContentsMargins(22,20,22,18); layout.setSpacing(12)
        title=QLabel("Finish connecting Last.fm"); title.setObjectName("dialogTitle")
        self.body=QLabel("Finish the Last.fm page in your browser. Amethyst will pick it up when it is done.")
        self.body.setObjectName("dialogText"); self.body.setWordWrap(True)
        self.status=QLabel("Waiting for Last.fm"); self.status.setObjectName("muted")
        row=QHBoxLayout(); row.addStretch(); cancel=QPushButton("Cancel"); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        layout.addWidget(title); layout.addWidget(self.body); layout.addWidget(self.status); layout.addSpacing(2); layout.addLayout(row)
        self.setStyleSheet(dialog_style(accent))

    def success(self,username):
        self.connected=True; self.status.setText(f"Connected as {username}"); self.accept()

class ColourDialog(QDialog):
    colours = ["#b64fff", "#4f9cff", "#1ed760", "#ff4f81", "#ff7a45", "#ffd84f", "#47d7d7", "#ffffff"]

    def __init__(self, parent, current, accent, title="Choose colour"):
        super().__init__(parent)
        self.colour = None
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("dialogCard")
        root.addWidget(card)
        layout=QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18); layout.setSpacing(12)

        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        text = QLabel("Pick one or type any hex colour.")
        text.setObjectName("dialogText")
        self.hex = QLineEdit(current)
        self.preview = QFrame()
        self.preview.setFixedHeight(34); self.preview.setObjectName("colourPreview")
        self.hex.textChanged.connect(self.update_preview)

        swatches = QHBoxLayout()
        for colour in self.colours:
            b = QPushButton("")
            b.setFixedSize(34, 34)
            b.setStyleSheet(f"background:{colour}; border:1px solid #44444b; border-radius:17px;")
            b.clicked.connect(lambda checked=False, c=colour: self.hex.setText(c))
            swatches.addWidget(b)
        swatches.addStretch()

        buttons=QHBoxLayout()
        cancel = QPushButton("Cancel")
        save = QPushButton("Use colour")
        save.setObjectName("primaryButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save_colour); buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addLayout(swatches)
        layout.addWidget(self.hex)
        layout.addWidget(self.preview)
        layout.addLayout(buttons); self.setStyleSheet(dialog_style(accent))
        self.update_preview(current)

    def update_preview(self, text):
        c = QColor(text.strip())
        if c.isValid():
            self.preview.setStyleSheet(f"background:{c.name()}; border-radius:8px;")
        else:
            self.preview.setStyleSheet("background:#242429; border:1px solid #d94a4a; border-radius:8px;")

    def save_colour(self):
        c = QColor(self.hex.text().strip())
        if not c.isValid():
            return
        self.colour = c.name()
        self.accept()

def leaderboard_rank_colour(rank):
    try: rank=int(rank)
    except Exception:return "#ffffff"
    if 0<rank<=10:return "#ffd54a"
    return "#ffffff"

def set_rank_text(label,text,rank,size=12):
    colour=leaderboard_rank_colour(rank)
    label.setTextFormat(Qt.PlainText); label.setText(str(text)); label.setStyleSheet(f"color:{colour};font-size:{size}px;font-weight:850;")

def dialog_style(accent):
    c=QColor(accent); border=f"rgba({c.red()},{c.green()},{c.blue()},105)"; faint=f"rgba({c.red()},{c.green()},{c.blue()},60)"
    return f"""
        QDialog {{ background:transparent; }}
        #dialogCard {{ background:#17171b; border:1px solid {border}; border-radius:16px; }}
        #dialogTitle {{ color:white; font-size:19px; font-weight:750; }}
        #dialogText {{ color:#a1a1aa; font-size:12px; }}
        QLineEdit {{ background:#202025; color:white; border:1px solid {faint}; border-radius:8px; padding:9px; }}
        QPushButton {{ background:#242429; color:#eeeeF2; border:1px solid {faint}; border-radius:8px; padding:8px 13px; }}
        QPushButton:hover {{ background:#2e2e34; border-color:{border}; }}
        #primaryButton {{ background:{accent}; color:#090a0d; border:none; font-weight:750; }}
        #primaryButton:hover {{ background:white; }}
        #profileShowcaseCard {{ background:#141418; border:1px solid {border}; border-radius:12px; }}
        #profileShowcaseRow {{ background:#1c1c21; border:1px solid {faint}; border-radius:9px; }}
    """

class SeekSlider(QSlider):
    clicked= Signal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.width():
            value=round(self.minimum() + (self.maximum() - self.minimum()) * event.position().x() / self.width())
            self.setValue(value)
            self.clicked.emit(value)
        super().mousePressEvent(event)

class TitleBar(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.dragging = False
        self.offset = QPoint()
        self.setObjectName("titlebar"); self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(5)
        title = QLabel(brand)
        title.setObjectName("windowTitle"); self.min_btn=QPushButton("−")
        self.max_btn = QPushButton("□")
        self.close_btn = QPushButton("×")
        for button in (self.min_btn, self.max_btn, self.close_btn):
            button.setFixedSize(42, 34)
            button.setObjectName("windowButton")
        self.close_btn.setObjectName("closeButton")
        self.min_btn.clicked.connect(window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_max)
        self.close_btn.clicked.connect(window.close)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    def toggle_max(self):
        if self.window.isMaximized():
            self.window.showNormal()
            self.max_btn.setText("□")
        else:
            self.window.showMaximized()
            self.max_btn.setText("❐")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_max()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.window.isMaximized():
            self.dragging = True
            self.offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.dragging and event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self.offset)

    def mouseReleaseEvent(self, event):
        self.dragging = False

class SwitchRow(QFrame):
    def __init__(self, title, text="", checked=False, accent="#b64fff"):
        super().__init__(); self.setObjectName("settingRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(12)
        words=QVBoxLayout()
        words.setSpacing(3)
        name = QLabel(title)
        name.setObjectName("settingName"); words.addWidget(name)
        if text:
            desc = QLabel(text)
            desc.setWordWrap(True)
            desc.setObjectName("muted")
            words.addWidget(desc)
        self.check = ToggleSwitch(checked, accent)
        layout.addLayout(words, 1)
        layout.addWidget(self.check, 0, Qt.AlignVCenter)


class AvatarLabel(QLabel):
    def __init__(self,size=46,accent="#b64fff",name="?"):
        super().__init__(); self.setFixedSize(size,size); self.setAlignment(Qt.AlignCenter); self.accent=accent; self.name=name or "?"; self.setCursor(Qt.ArrowCursor); self.set_fallback()
    def set_accent(self,accent):
        self.accent=accent; pix=self.pixmap()
        if pix is None or pix.isNull():self.set_fallback()
    def set_fallback(self):
        self.setPixmap(QPixmap()); self.setText((self.name or "?")[0].upper()); r=self.width()//2; self.setStyleSheet(f"background:#202025;border:2px solid {self.accent};border-radius:{r}px;font-size:{max(13,int(self.width()*.34))}px;font-weight:800;color:#eeeeF2;")
    def set_image(self,data):
        pix=QPixmap()
        if not data or not pix.loadFromData(data): self.set_fallback(); return
        size=self.size(); scaled=pix.scaled(size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); out=QPixmap(size); out.fill(Qt.transparent)
        p=QPainter(out); p.setRenderHint(QPainter.Antialiasing); path=QPainterPath(); path.addEllipse(2,2,size.width()-4,size.height()-4); p.setClipPath(path); p.drawPixmap(0,0,scaled); p.setClipping(False); p.setPen(QPen(QColor(self.accent),2)); p.setBrush(Qt.NoBrush); p.drawEllipse(QRectF(1.5,1.5,size.width()-3,size.height()-3)); p.end()
        self.setText(""); self.setStyleSheet("background:transparent;border:none;"); self.setPixmap(out)

class ShowcaseThumb(QLabel):
    def __init__(self,size=36,accent="#b64fff",name="?",round_image=False):
        super().__init__(); self.setFixedSize(size,size); self.setAlignment(Qt.AlignCenter); self.accent=accent; self.name=name or "?"; self.round_image=round_image; self.set_fallback()
    def set_fallback(self):
        self.setPixmap(QPixmap()); self.setText((self.name or "?")[0].upper()); radius=self.width()//2 if self.round_image else 8
        self.setStyleSheet(f"background:#202025;border:1px solid #34343b;border-radius:{radius}px;color:#d8d8df;font-size:{max(11,int(self.width()*.3))}px;font-weight:800;")
    def set_image(self,data):
        pix=QPixmap()
        if not data or not pix.loadFromData(data):self.set_fallback(); return
        size=self.size(); scaled=pix.scaled(size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); out=QPixmap(size); out.fill(Qt.transparent)
        p=QPainter(out); p.setRenderHint(QPainter.Antialiasing); path=QPainterPath()
        if self.round_image:path.addEllipse(1,1,size.width()-2,size.height()-2)
        else:path.addRoundedRect(QRectF(1,1,size.width()-2,size.height()-2),7,7)
        p.setClipPath(path); p.drawPixmap(0,0,scaled); p.end(); self.setText(""); self.setStyleSheet("background:transparent;border:none;"); self.setPixmap(out)

def normalise_profile_showcase(raw):
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except Exception:raw={}
    if not isinstance(raw,dict):raw={}
    periods=("Week","Month","Year","All time"); result={}
    for key in ("artists","songs","albums"):
        src=raw.get(key) or {}; result[key]={}
        # 0.10.0 and older only allowed one period per category
        if isinstance(src,dict) and ("period" in src or "items" in src or "visible" in src):
            old_period=src.get("period","All time") if src.get("period") in periods else "All time"
            for period in periods:result[key][period]={"visible":False,"items":[]}
            result[key][old_period]={"visible":bool(src.get("visible")),"items":src.get("items") or []}
            continue
        for period in periods:
            sec=src.get(period) if isinstance(src,dict) else None
            if not isinstance(sec,dict):sec={}
            result[key][period]={"visible":bool(sec.get("visible")),"items":sec.get("items") or []}
    return result

def profile_showcase_is_visible(raw):
    data=normalise_profile_showcase(raw)
    return any(bool(((data.get(key) or {}).get(period) or {}).get("visible")) for key in ("artists","songs","albums") for period in ("Week","Month","Year","All time"))

class ClickableFrame(QFrame):
    clicked=Signal(object)
    def __init__(self,payload=None): super().__init__(); self.payload=payload; self.setCursor(Qt.PointingHandCursor)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:self.clicked.emit(self.payload)
        super().mouseReleaseEvent(event)

class HotkeyEdit(QKeySequenceEdit):
    captureStarted=Signal()

    def mousePressEvent(self,event):
        self.captureStarted.emit()
        super().mousePressEvent(event)

    def focusInEvent(self,event):
        self.captureStarted.emit()
        super().focusInEvent(event)

class StatCard(QFrame):
    def __init__(self, rank, kind, name, extra, plays, listening_ms, duration_ms, accent):
        super().__init__(); self.setObjectName("statCard"); self.accent=accent; self.kind=kind; self.rank=rank; self.item_key=None
        plays=int(plays or 0); listening_ms=int(listening_ms or 0); duration_ms=int(duration_ms or 0)
        self.medal={1:"#ffd54a",2:"#c8c8d0",3:"#cd7f32"}.get(rank)
        layout=QHBoxLayout(self); layout.setContentsMargins(12,10,14,10); layout.setSpacing(12)
        number=QLabel(str(rank)); number.setObjectName("rank"); number.setFixedWidth(30); number.setAlignment(Qt.AlignCenter)
        if self.medal:number.setStyleSheet(f"color:{self.medal};font-size:14px;font-weight:850;")
        self.pic=QLabel(); self.pic.setObjectName("statPic"); self.pic.setFixedSize(56,56); self.pic.setAlignment(Qt.AlignCenter); self.set_placeholder(name)
        words=QVBoxLayout(); self.title=QLabel(name); self.title.setObjectName("statName"); self.title.setWordWrap(True); self.title.setMinimumWidth(0)
        if self.medal:self.title.setStyleSheet(f"color:{self.medal};font-size:14px;font-weight:750;")
        sub_text=extra if extra else " "
        if kind=="song" and duration_ms:
            mins,secs=divmod(int(duration_ms/1000),60); sub_text=f"{sub_text}  •  {mins}:{secs:02d}" if extra else f"{mins}:{secs:02d}"
        self.sub=QLabel(sub_text); self.sub.setObjectName("muted"); self.sub.setWordWrap(True); self.sub.setMinimumWidth(0); words.addWidget(self.title); words.addWidget(self.sub)
        right=QVBoxLayout(); right.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        self.listened=None
        if kind=="artist":
            self.listened=QLabel(format_artist_time(listening_ms)); self.listened.setObjectName("playCount"); self.count=QLabel(f"{plays:,} plays"); self.count.setObjectName("mutedSmall")
            right.addWidget(self.listened,0,Qt.AlignRight); right.addWidget(self.count,0,Qt.AlignRight)
        else:
            self.count=QLabel(f"{plays:,} plays"); self.count.setObjectName("playCount"); right.addWidget(self.count,0,Qt.AlignRight)
        layout.addWidget(number); layout.addWidget(self.pic); layout.addLayout(words,1); layout.addLayout(right)

    def update_values(self,plays,listening_ms):
        count_text=f"{int(plays or 0):,} plays"
        if self.count.text()!=count_text:self.count.setText(count_text)
        if self.listened is not None:
            listened_text=format_artist_time(listening_ms)
            if self.listened.text()!=listened_text:self.listened.setText(listened_text)

    def set_placeholder(self,name):
        letter=(name or "?")[0].upper(); self.pic.clear(); self.pic.setText(letter)
        radius=28 if self.kind=="artist" else 10; border=f"3px solid {self.medal}" if self.medal else "1px solid #34343b"
        self.pic.setStyleSheet(f"background:{self.accent};color:#0b0c10;border:{border};border-radius:{radius}px;font-size:18px;font-weight:800;")

    def set_image(self,data):
        pix=QPixmap()
        if not pix.loadFromData(data):return
        size=self.pic.size(); scaled=pix.scaled(size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); out=QPixmap(size); out.fill(Qt.transparent)
        painter=QPainter(out); painter.setRenderHint(QPainter.Antialiasing); path=QPainterPath()
        if self.kind=="artist": path.addEllipse(2,2,size.width()-4,size.height()-4)
        else: path.addRoundedRect(QRectF(2,2,size.width()-4,size.height()-4),9,9)
        painter.setClipPath(path); painter.drawPixmap(0,0,scaled); painter.setClipping(False)
        if self.medal:
            painter.setPen(QPen(QColor(self.medal),3)); painter.setBrush(Qt.NoBrush)
            if self.kind=="artist": painter.drawEllipse(QRectF(1.5,1.5,size.width()-3,size.height()-3))
            else: painter.drawRoundedRect(QRectF(1.5,1.5,size.width()-3,size.height()-3),10,10)
        painter.end(); self.pic.setText(""); self.pic.setStyleSheet("background:transparent;border:none;"); self.pic.setPixmap(out)

def format_artist_time(ms):
    minutes=max(0,int((ms or 0)/60000))
    if minutes>=60:
        hours,minutes=divmod(minutes,60); return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else ""

class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, app):
        super().__init__()
        self.app = app

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    QTimer.singleShot(0, self.app.toggle_overlay)
            except Exception:
                pass
        return False, 0

class Overlay(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app; self.track = None
        self.progress_key=None; self.progress_anchor_ms=0; self.progress_anchor_at=time.monotonic(); self.progress_playing=False; self.progress_duration_ms=0
        self.album_colour=QColor(app.accent)
        self.dragging = False
        self.drag_offset = QPoint()
        self.saving_resize = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        self.card = QFrame(); self.card.setObjectName("overlayCard")
        outer.addWidget(self.card)

        self.layout = QVBoxLayout(self.card)
        self.layout.setContentsMargins(16, 13, 16, 14)
        self.layout.setSpacing(9)

        top=QHBoxLayout()
        self.brand_label = QLabel("SPOTIFY")
        self.brand_label.setObjectName("source")
        self.open_btn = QPushButton("Open")
        self.open_btn.setObjectName("tinyButton"); self.hide_btn = QPushButton("×")
        self.hide_btn.setObjectName("tinyButton"); self.clock_label=QLabel(""); self.clock_label.setObjectName("overlayClock")
        self.open_btn.clicked.connect(self.open_main)
        self.hide_btn.clicked.connect(app.hide_overlay)
        top.addWidget(self.brand_label)
        top.addStretch()
        top.addWidget(self.clock_label)
        top.addWidget(self.open_btn)
        top.addWidget(self.hide_btn); self.layout.addLayout(top)

        self.track_row = QHBoxLayout()
        self.cover = QLabel("♪")
        self.cover.setObjectName("cover")
        self.cover.setAlignment(Qt.AlignCenter)
        self.info = QVBoxLayout()
        self.title=QLabel("Nothing playing")
        self.title.setObjectName("overlayTitle"); self.artist = QLabel("")
        self.artist.setObjectName("overlayArtist")
        self.info.addStretch()
        self.info.addWidget(self.title)
        self.info.addWidget(self.artist)
        self.info.addStretch()
        self.track_row.addWidget(self.cover)
        self.track_row.addLayout(self.info, 1); self.layout.addLayout(self.track_row)

        self.seek = SeekSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.clicked.connect(self.seek_to)
        self.layout.addWidget(self.seek)

        self.time_row = QFrame()
        time_layout = QHBoxLayout(self.time_row)
        time_layout.setContentsMargins(0, 0, 0, 0)
        self.now = QLabel("0:00"); self.end = QLabel("0:00")
        time_layout.addWidget(self.now)
        time_layout.addStretch()
        time_layout.addWidget(self.end)
        self.layout.addWidget(self.time_row)

        controls=QHBoxLayout()
        controls.setSpacing(7)
        self.shuffle_btn = MediaButton("shuffle", app.accent, 38)
        self.prev_btn = MediaButton("previous", app.accent, 40)
        self.play_btn = MediaButton("play", app.accent, 50, True)
        self.next_btn = MediaButton("next", app.accent, 40)
        self.repeat_btn = MediaButton("repeat", app.accent, 38)
        self.prev_btn.clicked.connect(app.previous)
        self.play_btn.clicked.connect(app.play_pause)
        self.next_btn.clicked.connect(app.next)
        self.shuffle_btn.clicked.connect(app.toggle_shuffle); self.repeat_btn.clicked.connect(app.toggle_repeat)
        controls.addStretch()
        controls.addWidget(self.shuffle_btn)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.repeat_btn)
        controls.addStretch(); self.layout.addLayout(controls)

        self.volume_row = QFrame()
        volume_layout=QHBoxLayout(self.volume_row)
        volume_layout.setContentsMargins(2, 0, 2, 0)
        down = MediaButton("volume_down", app.accent, 32)
        up = MediaButton("volume_up", app.accent, 32)
        down.clicked.connect(app.volume_down)
        up.clicked.connect(app.volume_up); volume_layout.addWidget(QLabel("Volume"))
        volume_layout.addStretch()
        volume_layout.addWidget(down)
        volume_layout.addWidget(up)
        self.layout.addWidget(self.volume_row)

        self.resize_timer=QTimer(self); self.resize_timer.setSingleShot(True); self.resize_timer.timeout.connect(self.save_custom_size)
        self.move_timer=QTimer(self); self.move_timer.setSingleShot(True); self.move_timer.timeout.connect(self.save_custom_position)
        self.clock_timer=QTimer(self); self.clock_timer.setInterval(1000); self.clock_timer.timeout.connect(self.update_clock); self.clock_timer.start(); self.update_clock()
        for thing in self.findChildren(QWidget):
            thing.setMouseTracking(True)
            thing.installEventFilter(self)
        self.apply_settings(first=True)

    def update_clock(self):
        if hasattr(self,"clock_label"):
            if self.app.settings.get("overlay_time_format","12 hour")=="24 hour":
                text=datetime.now().strftime("%H:%M")
            else:
                text=datetime.now().strftime("%I:%M %p").lstrip("0")
            self.clock_label.setText(text)
        self.update_progress_display()

    def update_progress_display(self):
        if not self.track:return
        duration=max(0,int(self.progress_duration_ms or 0))
        progress=max(0,int(self.progress_anchor_ms or 0))
        if self.progress_playing:
            progress+=max(0,int((time.monotonic()-self.progress_anchor_at)*1000))
        if duration:progress=min(progress,duration)
        if duration:self.seek.setValue(int(progress/duration*1000))
        self.now.setText(self.app.fmt(progress)); self.end.setText(self.app.fmt(duration))

    def open_main(self):
        self.app.window.show()
        self.app.window.raise_()
        self.app.window.activateWindow()

    def colour(self):
        mode = self.app.settings.get("overlay_colour_mode", "Theme")
        if mode == "Custom":
            return QColor(self.app.settings.get("overlay_colour", self.app.accent))
        if mode == "Album artwork":
            return self.album_colour
        return QColor(self.app.accent)

    def apply_settings(self, first=False):
        s=self.app.settings
        self.cover.setVisible(bool(s.get("show_art", True)))
        self.seek.setVisible(bool(s.get("show_progress", True)))
        self.time_row.setVisible(bool(s.get("show_times", True)))
        self.shuffle_btn.setVisible(bool(s.get("show_shuffle", True)))
        self.repeat_btn.setVisible(bool(s.get("show_repeat", True)))
        playback_visible=not bool(s.get("hide_playback_buttons", False))
        self.prev_btn.setVisible(playback_visible)
        self.play_btn.setVisible(playback_visible)
        self.next_btn.setVisible(playback_visible)
        self.volume_row.setVisible(bool(s.get("show_volume", False)))
        self.clock_label.setVisible(bool(s.get("show_local_time", False)))
        self.open_btn.setVisible(bool(s.get("show_open_button", False)))
        self.setWindowOpacity(float(s.get("overlay_opacity", .96)))

        mode = s.get("overlay_size_mode", "Default")
        sizes = {"Compact": 360, "Default": 430, "Large": 520}; self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)

        if mode == "Custom":
            self.setMinimumSize(320, 220)
            self.setMaximumSize(900, 700)
            if first or not self.isVisible():
                self.resize(int(s.get("custom_width", 430)), int(s.get("custom_height", 310)))
        else:
            width = sizes.get(mode, 430)
            self.setFixedWidth(width)
            self.adjustSize()

        self.scale_contents()
        self.restyle()

    def scale_contents(self):
        factor = max(.78, min(1.55, self.width() / 430 if self.width() else 1))
        art = int(78 * factor); self.cover.setFixedSize(art, art)
        self.shuffle_btn.set_button_size(int(38 * factor))
        self.prev_btn.set_button_size(int(40 * factor))
        self.play_btn.set_button_size(int(50 * factor))
        self.next_btn.set_button_size(int(40 * factor))
        self.repeat_btn.set_button_size(int(38 * factor))
        title_size = max(13, int(18 * factor))
        artist_size=max(10, int(12 * factor))
        self.title.setStyleSheet(f"font-size:{title_size}px; font-weight:700;")
        self.artist.setStyleSheet(f"font-size:{artist_size}px; color:#a0a0a8;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.app.settings.get("overlay_size_mode") == "Custom":
            self.scale_contents(); self.resize_timer.start(250)

    def save_custom_size(self):
        if self.app.settings.get("overlay_size_mode") != "Custom":
            return
        self.app.settings["custom_width"] = self.width()
        self.app.settings["custom_height"] = self.height()
        save_settings(self.app.settings)

    def seek_to(self, value):
        if not self.track or not self.track.get("duration_ms"):
            return
        self.app.seek(int(value / 1000 * self.track["duration_ms"]))

    def set_track(self, track):
        old_key=self.progress_key
        self.track = track
        if not track:
            self.progress_key=None; self.progress_anchor_ms=0; self.progress_duration_ms=0; self.progress_playing=False
            self.title.setText("Nothing playing"); self.artist.setText(""); self.set_art(b"",self.app.accent); return
        self.title.setText(track.get("title", "Unknown song")); self.artist.setText(track.get("artist", "")); self.play_btn.set_icon("pause" if track.get("is_playing") else "play")
        key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
        duration=max(0,int(track.get("duration_ms") or 0)); incoming=max(0,int(track.get("progress_ms") or 0)); playing=bool(track.get("is_playing"))
        predicted=self.progress_anchor_ms + (int((time.monotonic()-self.progress_anchor_at)*1000) if self.progress_playing else 0)
        if key!=old_key or playing!=self.progress_playing or abs(incoming-predicted)>2500:
            self.progress_anchor_ms=incoming; self.progress_anchor_at=time.monotonic()
        self.progress_key=key; self.progress_duration_ms=duration; self.progress_playing=playing
        self.update_progress_display(); self.shuffle_btn.set_active(bool(track.get("shuffle"))); self.repeat_btn.set_repeat_mode(track.get("repeat","off"))

    def set_art(self, data, colour):
        pix=QPixmap()
        if data and pix.loadFromData(data):
            self.cover.setText(""); self.cover.setPixmap(pix.scaled(self.cover.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
        else:
            self.cover.clear(); self.cover.setText("♫\nLOCAL" if self.track and self.track.get("is_local") else "♪")
        self.album_colour=QColor(colour)
        if self.app.settings.get("overlay_colour_mode")=="Album artwork": self.restyle()

    def restyle(self):
        colour = self.colour().name()
        for button in (self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn):
            button.set_accent(colour)
        self.setStyleSheet(f"""
            QWidget {{ color:#f5f5f6; font-family:'Segoe UI'; background:transparent; }}
            #overlayCard {{ background:rgba(18,18,21,247); border:2px solid {colour}; border-radius:20px; }}
            #source {{ color:{colour}; font-size:9px; font-weight:750; letter-spacing:1px; }}
            #overlayClock {{ color:#b9b9c2; font-size:10px; font-weight:700; padding:0 4px; }}
            #cover {{ background:#242429; border-radius:11px; font-size:28px; }}
            #tinyButton {{ background:#232328; border:none; border-radius:7px; padding:5px 9px; color:#b9b9c2; }}
            #tinyButton:hover {{ color:white; background:#303036; }}
            QSlider::groove:horizontal {{ height:4px; background:#35353b; border-radius:2px; }}
            QSlider::sub-page:horizontal {{ background:{colour}; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:12px; height:12px; margin:-4px 0; background:white; border-radius:6px; }}
            #sizeGrip {{ width:13px; height:13px; }}
        """)

    def resize_edges(self,global_pos):
        if self.app.settings.get("overlay_size_mode")!="Custom":return None
        p=self.mapFromGlobal(global_pos); edge=14; edges=None
        if p.x()<=edge:edges=Qt.LeftEdge
        elif p.x()>=self.width()-edge:edges=Qt.RightEdge
        if p.y()<=edge:edges=Qt.TopEdge if edges is None else edges|Qt.TopEdge
        elif p.y()>=self.height()-edge:edges=Qt.BottomEdge if edges is None else edges|Qt.BottomEdge
        return edges

    def begin_resize(self,global_pos):
        edges=self.resize_edges(global_pos)
        if edges is None:return False
        self.resizing=True
        self.resize_direction=edges
        self.resize_start_pos=global_pos
        self.resize_start_geometry=self.geometry()
        return True

    def resize_from_mouse(self,global_pos):
        if not getattr(self,"resizing",False):return False
        delta=global_pos-self.resize_start_pos; g=self.resize_start_geometry
        left,top,right,bottom=g.left(),g.top(),g.right(),g.bottom()
        if self.resize_direction & Qt.LeftEdge:left+=delta.x()
        if self.resize_direction & Qt.RightEdge:right+=delta.x()
        if self.resize_direction & Qt.TopEdge:top+=delta.y()
        if self.resize_direction & Qt.BottomEdge:bottom+=delta.y()

        min_w,max_w=self.minimumWidth(),self.maximumWidth()
        min_h,max_h=self.minimumHeight(),self.maximumHeight()
        width=right-left+1; height=bottom-top+1
        if width<min_w:
            if self.resize_direction & Qt.LeftEdge:left=right-min_w+1
            else:right=left+min_w-1
        elif width>max_w:
            if self.resize_direction & Qt.LeftEdge:left=right-max_w+1
            else:right=left+max_w-1
        if height<min_h:
            if self.resize_direction & Qt.TopEdge:top=bottom-min_h+1
            else:bottom=top+min_h-1
        elif height>max_h:
            if self.resize_direction & Qt.TopEdge:top=bottom-max_h+1
            else:bottom=top+max_h-1

        self.setGeometry(left,top,right-left+1,bottom-top+1)
        return True

    def finish_resize(self):
        if not getattr(self,"resizing",False):return False
        self.resizing=False
        self.save_custom_size()
        if self.app.settings.get("overlay_position")=="Custom":self.save_custom_position()
        else:self.app.position_overlay()
        return True

    def eventFilter(self,obj,event):
        if event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
            if self.begin_resize(event.globalPosition().toPoint()):event.accept(); return True
            if self.app.settings.get("overlay_position")=="Custom" and not isinstance(obj,(QAbstractButton,QSlider)):
                handle=self.windowHandle()
                if handle and handle.startSystemMove():event.accept(); return True
                self.dragging=True; self.drag_offset=event.globalPosition().toPoint()-self.frameGeometry().topLeft(); return True
        if event.type()==QEvent.MouseMove:
            if self.resize_from_mouse(event.globalPosition().toPoint()):event.accept(); return True
            if self.dragging and event.buttons()&Qt.LeftButton:
                self.move(event.globalPosition().toPoint()-self.drag_offset); return True
        if event.type()==QEvent.MouseButtonRelease:
            if self.finish_resize():event.accept(); return True
            if self.dragging:
                self.dragging=False; self.save_custom_position(); return True
        return super().eventFilter(obj,event)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self.begin_resize(event.globalPosition().toPoint()):event.accept(); return
        if self.app.settings.get("overlay_position")=="Custom" and event.button()==Qt.LeftButton:
            handle=self.windowHandle()
            if handle and handle.startSystemMove():event.accept(); return
            self.dragging=True; self.drag_offset=event.globalPosition().toPoint()-self.frameGeometry().topLeft(); event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.resize_from_mouse(event.globalPosition().toPoint()):event.accept(); return
        if self.dragging and event.buttons()&Qt.LeftButton:self.move(event.globalPosition().toPoint()-self.drag_offset); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.finish_resize():event.accept(); return
        if self.dragging:self.dragging=False; self.save_custom_position()
        super().mouseReleaseEvent(event)

    def moveEvent(self,event):
        super().moveEvent(event)
        if self.app.settings.get("overlay_position")=="Custom" and self.isVisible(): self.move_timer.start(250)

    def save_custom_position(self):
        if self.app.settings.get("overlay_position")!="Custom": return
        self.app.settings["custom_x"]=self.x(); self.app.settings["custom_y"]=self.y(); save_settings(self.app.settings)

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self.app,"window"): QTimer.singleShot(0,self.app.window.update_overlay_button)

    def hideEvent(self,event):
        super().hideEvent(event)
        if hasattr(self.app,"window"): QTimer.singleShot(0,self.app.window.update_overlay_button)

class PublicProfileDialog(QDialog):
    def __init__(self,parent,app,data):
        super().__init__(parent); self.app=app; self.data=data or {}; self.setModal(True); self.setWindowFlags(Qt.Dialog|Qt.FramelessWindowHint); self.setAttribute(Qt.WA_TranslucentBackground); self.resize(760,680); self.setMinimumSize(660,520)
        outer=QVBoxLayout(self); outer.setContentsMargins(10,10,10,10); card=QFrame(); card.setObjectName("dialogCard"); outer.addWidget(card); root=QVBoxLayout(card); root.setContentsMargins(22,20,22,18); root.setSpacing(12)
        profile=self.data; display=profile.get("display_name") or profile.get("username") or "Unknown user"; top=QHBoxLayout(); avatar=AvatarLabel(78,app.accent,display); path=profile.get("avatar_path") or ""
        if path:app.load_avatar_widget(avatar,app.cloud.avatar_url(path))
        names=QVBoxLayout(); title=QLabel(display); title.setObjectName("dialogTitle"); username=QLabel("@"+(profile.get("username") or "unknown")); username.setObjectName("dialogText"); names.addWidget(title); names.addWidget(username)
        rank=profile.get("rank"); plays=int(profile.get("total_plays") or 0)
        if profile.get("is_developer"):
            dev=QLabel("DEVELOPER"); dev.setStyleSheet(f"color:{app.accent};background:#242429;border:1px solid {app.accent};border-radius:7px;padding:2px 7px;font-size:10px;font-weight:850;"); dev.setFixedWidth(dev.sizeHint().width()+4); names.addWidget(dev,0,Qt.AlignLeft)
        if rank:
            badge=QLabel(); set_rank_text(badge,f"Leaderboard #{int(rank):,}  •  {plays:,} songs played",rank,12); names.addWidget(badge)
        created=profile.get("created_at")
        if created:
            try:
                joined=datetime.fromisoformat(str(created).replace("Z","+00:00")).astimezone().strftime("%d %b %Y")
                joined_label=QLabel("Joined "+joined); joined_label.setObjectName("mutedSmall"); names.addWidget(joined_label)
            except Exception:pass
        top.addWidget(avatar); top.addLayout(names,1); root.addLayout(top)
        desc=QLabel(profile.get("description") or "No description yet."); desc.setObjectName("dialogText"); desc.setWordWrap(True); root.addWidget(desc)

        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea>QWidget>QWidget{background:transparent;}"); scroll.viewport().setStyleSheet("background:transparent;")
        content=QWidget(); content.setStyleSheet("background:transparent;"); grid=QGridLayout(content); grid.setContentsMargins(0,4,4,4); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)
        showcase=normalise_profile_showcase(profile.get("profile_showcase") or {})
        sections=[]
        for key,label in [("artists","Top artists"),("songs","Top songs"),("albums","Top albums")]:
            for period in ("Week","Month","Year","All time"):
                sec=(showcase.get(key) or {}).get(period) or {}
                if sec.get("visible"):sections.append((key,label,period,sec))
        if not sections:
            empty=QLabel("This user isn't showing any listening stats yet."); empty.setObjectName("dialogText"); empty.setAlignment(Qt.AlignCenter); grid.addWidget(empty,0,0,1,2)
        for index,(key,label,period,sec) in enumerate(sections):
            box=QFrame(); box.setObjectName("profileShowcaseCard"); b=QVBoxLayout(box); b.setContentsMargins(11,10,11,10); b.setSpacing(6)
            h=QHBoxLayout(); heading=QLabel(label); heading.setStyleSheet("font-weight:800;font-size:13px;"); period_badge=QLabel(period); period_badge.setStyleSheet(f"color:{app.accent};font-size:11px;font-weight:700;")
            h.addWidget(heading); h.addStretch(); h.addWidget(period_badge); b.addLayout(h)
            items=[x for x in (sec.get("items") or []) if isinstance(x,dict) and x.get("name")]
            if not items:
                empty=QLabel("Nothing here yet"); empty.setObjectName("dialogText"); b.addWidget(empty)
            else:
                medals={1:"#ffd54a",2:"#c8c8d0",3:"#cd7f32"}
                for i,item in enumerate(items[:5],1):
                    row=QFrame(); row.setObjectName("profileShowcaseRow"); r=QHBoxLayout(row); r.setContentsMargins(7,6,8,6); r.setSpacing(7)
                    num=QLabel(str(i)); num.setFixedWidth(16); num.setAlignment(Qt.AlignCenter); colour=medals.get(i,"#777781"); num.setStyleSheet(f"color:{colour};font-weight:850;")
                    thumb=ShowcaseThumb(34,app.accent,item.get("name","?"),key=="artists"); image=item.get("image") or ""
                    if image:app.load_avatar_widget(thumb,image)
                    words=QVBoxLayout(); words.setSpacing(0); item_name=QLabel(item.get("name","") or "Unknown"); item_name.setStyleSheet("font-weight:700;font-size:12px;"); item_name.setWordWrap(True); item_name.setMinimumWidth(0)
                    extra=QLabel(item.get("extra","") or ""); extra.setObjectName("mutedSmall"); extra.setWordWrap(True); extra.setMinimumWidth(0); words.addWidget(item_name)
                    if item.get("extra"):words.addWidget(extra)
                    play_count=QLabel(f"{int(item.get('plays') or 0):,} plays"); play_count.setStyleSheet("color:#c7c7cf;font-size:11px;font-weight:700;")
                    r.addWidget(num); r.addWidget(thumb); r.addLayout(words,1); r.addWidget(play_count,0,Qt.AlignRight|Qt.AlignVCenter); b.addWidget(row)
            grid.addWidget(box,index//2,index%2)
        grid.setColumnStretch(0,1); grid.setColumnStretch(1,1); scroll.setWidget(content); root.addWidget(scroll,1)
        close=QPushButton("Close"); close.clicked.connect(self.accept); root.addWidget(close,0,Qt.AlignRight); self.setStyleSheet(dialog_style(app.accent))

class MainWindow(QMainWindow):
    friends_gif_ready=Signal(str)
    def __init__(self, app):
        super().__init__()
        self.app= app
        self.friends_gif_ready.connect(self.start_friends_gif)
        self.stat_cards = []; self.missing_artist_fetch = False
        self.lastfm_notices=[]
        self.debug_page_index = None

        self.setWindowTitle(brand)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setMinimumSize(900, 620)
        self.resize(1040, 700)

        base=QWidget()
        base.setObjectName("base")
        self.setCentralWidget(base)
        outer = QVBoxLayout(base); outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)
        self.nav = QListWidget()
        self.nav.setObjectName("nav"); self.nav.setFixedWidth(185)
        self.nav.addItems(["Home", "Stats", "Leaderboard", "Profile", "Friends", "Overlay", "Settings", "Account"])
        self.nav.currentRowChanged.connect(self.change_page)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        self.pages.addWidget(self.home_page())
        self.pages.addWidget(self.guest_locked_page("Stats","Sign up to access listening stats and save listening data.") if self.app.guest else self.stats_page())
        self.pages.addWidget(self.guest_locked_page("Leaderboard","Sign up to access global leaderboards and public profiles.") if self.app.guest else self.leaderboard_page())
        self.pages.addWidget(self.guest_locked_page("Profile","Sign up to customise your profile.") if self.app.guest else self.profile_page())
        self.pages.addWidget(self.guest_locked_page("Friends","Sign up to connect with friends.") if self.app.guest else self.friends_page())
        self.pages.addWidget(self.overlay_page())
        self.pages.addWidget(self.guest_locked_page("Settings","Sign up to customise settings.") if self.app.guest else self.settings_page())
        self.pages.addWidget(self.guest_account_page() if self.app.guest else self.account_page())
        if self.app.is_developer:
            self.add_debug_page(select=False)
        self.nav.setCurrentRow(0)
        self.apply_style()
        self.toast=QLabel(self); self.toast.setObjectName("toast"); self.toast.setWordWrap(False); self.toast.hide(); self.toast_timer=QTimer(self); self.toast_timer.setSingleShot(True); self.toast_timer.timeout.connect(self.toast.hide)
        self.stats_live_timer=QTimer(self); self.stats_live_timer.setInterval(1000); self.stats_live_timer.timeout.connect(self.live_stats_refresh); self.stats_live_timer.start()

    def page_header(self, title, subtitle):
        box = QVBoxLayout()
        big=QLabel(title)
        big.setObjectName("big")
        small = QLabel(subtitle)
        small.setObjectName("muted"); small.setWordWrap(True)
        box.addWidget(big)
        box.addWidget(small)
        return box

    def section_title(self, text):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def lastfm_note(self,text):
        frame=QFrame(); frame.setObjectName("lastfmNotice"); row=QHBoxLayout(frame); row.setContentsMargins(12,8,10,8); row.setSpacing(10)
        body=QLabel(text); body.setObjectName("muted"); body.setWordWrap(True)
        button=QPushButton("Connect"); button.clicked.connect(self.app.lastfm_button_clicked)
        row.addWidget(body,1); row.addWidget(button); frame.hide(); self.lastfm_notices.append(frame); return frame

    def guest_locked_page(self,title,text):
        page=QWidget(); page.setObjectName("page"); layout=QVBoxLayout(page); layout.setContentsMargins(22,18,22,22); layout.addLayout(self.page_header(title,"Account feature")); layout.addStretch()
        card=QFrame(); card.setObjectName("section"); box=QVBoxLayout(card); box.setContentsMargins(22,20,22,20); heading=QLabel("Sign in required"); heading.setObjectName("sectionTitle"); body=QLabel(text); body.setObjectName("muted"); body.setWordWrap(True); button=QPushButton("Sign in or create account"); button.setObjectName("primaryAction"); button.clicked.connect(self.app.sign_in_from_guest); box.addWidget(heading); box.addWidget(body); box.addSpacing(6); box.addWidget(button,0,Qt.AlignLeft); layout.addWidget(card); layout.addStretch(); return page

    def guest_account_page(self):
        return self.guest_locked_page("Account","Guest mode is overlay only. Sign in to unlock stats, saved data, settings and social features.")

    def home_page(self):
        page=QWidget(); page.setObjectName("page")
        layout=QVBoxLayout(page); layout.setContentsMargins(22,18,22,22); layout.setSpacing(14)
        hour=datetime.now().hour; greeting="Good morning" if hour<12 else "Good afternoon" if hour<18 else "Good evening"
        display=(self.app.profile or {}).get("display_name") or (self.app.profile or {}).get("username") or ("guest" if self.app.guest else "there")
        home_top=QHBoxLayout(); self.home_greeting=QLabel(f"{greeting}, {display}"); self.home_greeting.setObjectName("big"); self.home_version=QLabel(f"v{str(version).lstrip('v')}"); self.home_version.setObjectName("mutedSmall"); home_top.addWidget(self.home_greeting); home_top.addStretch(); home_top.addWidget(self.home_version,0,Qt.AlignBottom); layout.addLayout(home_top)

        hero=QFrame(); hero.setObjectName("hero"); h=QHBoxLayout(hero); h.setContentsMargins(20,18,20,18); h.setSpacing(18)
        self.home_cover=QLabel("♪"); self.home_cover.setObjectName("homeCover"); self.home_cover.setAlignment(Qt.AlignCenter); self.home_cover.setFixedSize(96,96)
        words=QVBoxLayout(); words.setSpacing(5)
        eyebrow=QLabel("Now playing"); eyebrow.setObjectName("mutedSmall")
        self.home_track=QLabel("Play something on Spotify"); self.home_track.setObjectName("homeTrack"); self.home_track.setWordWrap(True)
        self.home_artist=QLabel(""); self.home_artist.setObjectName("muted"); self.home_state=QLabel("Waiting for Spotify on this PC"); self.home_state.setObjectName("mutedSmall")
        words.addWidget(eyebrow); words.addSpacing(2); words.addWidget(self.home_track); words.addWidget(self.home_artist); words.addWidget(self.home_state); words.addStretch()
        buttons=QVBoxLayout(); buttons.setSpacing(8)
        self.overlay_toggle_btn=QPushButton(); self.overlay_toggle_btn.clicked.connect(self.app.toggle_overlay)
        buttons.addWidget(self.overlay_toggle_btn); buttons.addStretch()
        h.addWidget(self.home_cover); h.addLayout(words,1); h.addLayout(buttons); layout.addWidget(hero)

        cards=QHBoxLayout(); cards.setSpacing(10)
        shortcut=QFrame(); shortcut.setObjectName("section"); sh=QVBoxLayout(shortcut); sh.setContentsMargins(16,14,16,14)
        sh.addWidget(self.section_title("Overlay shortcut"))
        self.home_hotkey_edit=HotkeyEdit(QKeySequence(self.app.settings.get("overlay_hotkey","F1")))
        self.home_hotkey_edit.setMaximumSequenceLength(1); self.home_hotkey_edit.captureStarted.connect(self.hotkey_capture_started); self.home_hotkey_edit.editingFinished.connect(self.home_hotkey_changed)
        self.home_hotkey_note=QLabel("Click the box to replace the overlay shortcut."); self.home_hotkey_note.setObjectName("muted"); self.home_hotkey_note.setWordWrap(True)
        sh.addWidget(self.home_hotkey_edit); sh.addWidget(self.home_hotkey_note)
        connection=QFrame(); connection.setObjectName("section"); ch=QVBoxLayout(connection); ch.setContentsMargins(16,14,16,14)
        self.spotify_status_card=connection; self.spotify_status_card.setObjectName("historyStatusChecking" if not self.app.guest else "historyStatusOff")
        ch.addWidget(self.section_title("Last.fm status")); self.home_status=QLabel("Checking..." if not self.app.guest else "Sign in required"); self.home_status.setStyleSheet("font-size:16px;font-weight:700;color:#b7b7c0;")
        self.home_history_note=QLabel("Checking your Last.fm connection." if not self.app.guest else "Sign in to use Last.fm."); self.home_history_note.setObjectName("muted"); self.home_history_note.setWordWrap(True)
        self.home_lastfm_extra=QLabel("Checking listening history sync..." if not self.app.guest else ""); self.home_lastfm_extra.setObjectName("mutedSmall"); self.home_lastfm_extra.setWordWrap(True)
        self.lastfm_btn=QPushButton("Connect Last.fm" if not self.app.guest else "Sign in first"); self.lastfm_btn.clicked.connect(self.app.lastfm_button_clicked); self.lastfm_btn.setEnabled(not self.app.guest)
        if self.app.guest:self.lastfm_btn.hide()
        ch.addWidget(self.home_status); ch.addWidget(self.home_history_note); ch.addSpacing(2); ch.addWidget(self.home_lastfm_extra); ch.addWidget(self.lastfm_btn,0,Qt.AlignLeft)
        online=QFrame(); online.setObjectName("section"); ol=QVBoxLayout(online); ol.setContentsMargins(16,14,16,14)
        ol.addWidget(self.section_title("Active users")); self.active_users_label=QLabel("Sign in" if self.app.guest else "--"); self.active_users_label.setStyleSheet(f"color:{self.app.accent};font-size:24px;font-weight:800;")
        active_note=QLabel("How many people currently have the app open."); active_note.setObjectName("muted"); active_note.setWordWrap(True)
        ol.addWidget(self.active_users_label); ol.addWidget(active_note); ol.addStretch()
        cards.addWidget(shortcut,1); cards.addWidget(connection,1); cards.addWidget(online,1); layout.addLayout(cards); layout.addStretch(); self.update_overlay_button(); return page

    def stats_page(self):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(12)
        head = QHBoxLayout()
        stats_header=self.page_header("Your stats", "")
        self.stats_subtitle=stats_header.itemAt(1).widget(); head.addLayout(stats_header,1)
        filters = QHBoxLayout()
        self.kind= QComboBox(); self.kind.addItems(["Artists", "Albums", "Songs"])
        self.period=QComboBox()
        self.period.addItems(["Week", "Month", "Year", "All time"])
        self.stats_filter_timer=QTimer(self); self.stats_filter_timer.setSingleShot(True); self.stats_filter_timer.setInterval(120); self.stats_filter_timer.timeout.connect(self.reload_stats)
        self.kind.currentTextChanged.connect(self.schedule_stats_reload)
        self.period.currentTextChanged.connect(self.schedule_stats_reload)
        self.update_stats_subtitle()
        filters.addWidget(self.kind)
        filters.addWidget(self.period)
        head.addLayout(filters); layout.addLayout(head)

        self.stats_history_banner=self.lastfm_note("Last.fm is off, so these stats can miss anything played while Amethyst was closed.")
        layout.addWidget(self.stats_history_banner)

        self.stats_scroll = QScrollArea()
        self.stats_scroll.setWidgetResizable(True)
        self.stats_scroll.setFrameShape(QFrame.NoFrame)
        self.stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stats_body = QWidget()
        self.stats_body.setObjectName("scrollBody")
        self.stats_layout = QVBoxLayout(self.stats_body)
        self.stats_layout.setContentsMargins(0, 4, 4, 4); self.stats_layout.setSpacing(8)
        self.stats_layout.addStretch()
        self.stats_scroll.setWidget(self.stats_body)
        layout.addWidget(self.stats_scroll, 1)
        return page

    def leaderboard_page(self):
        page=QWidget(); page.setObjectName("page")
        layout=QVBoxLayout(page); layout.setContentsMargins(22,18,22,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Leaderboard", "Click any user to view their profile."))
        layout.addWidget(self.lastfm_note("Last.fm is off. Your total can be lower if you listen while Amethyst is closed."))
        search=QHBoxLayout(); self.board_search=QLineEdit(); self.board_search.setPlaceholderText("Search username or display name")
        find=QPushButton("Search"); clear=QPushButton("Clear"); find.setObjectName("primaryAction")
        find.clicked.connect(self.reload_leaderboard); self.board_search.returnPressed.connect(self.reload_leaderboard)
        clear.clicked.connect(self.clear_leaderboard_search); search.addWidget(self.board_search,1); search.addWidget(find); search.addWidget(clear); layout.addLayout(search)
        self.board_search_note=QLabel(""); self.board_search_note.setObjectName("muted"); layout.addWidget(self.board_search_note)
        self.board_scroll=QScrollArea(); self.board_scroll.setWidgetResizable(True); self.board_scroll.setFrameShape(QFrame.NoFrame); self.board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body=QWidget(); body.setObjectName("scrollBody"); self.board_layout=QVBoxLayout(body); self.board_layout.setContentsMargins(0,4,4,4); self.board_layout.setSpacing(8); self.board_layout.addStretch()
        self.board_scroll.setWidget(body); self.board_scroll.verticalScrollBar().valueChanged.connect(self.leaderboard_scroll)
        self.board_loading=False; self.board_has_more=True; self.board_cursor=None; self.board_query=""; layout.addWidget(self.board_scroll,1); return page

    def clear_leaderboard_search(self):
        self.board_search.clear(); self.reload_leaderboard()

    def clear_leaderboard_rows(self):
        while self.board_layout.count()>1:
            item=self.board_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()

    def reload_leaderboard(self):
        if not hasattr(self,"board_layout"):return
        self.clear_leaderboard_rows(); self.board_query=self.board_search.text().strip() if hasattr(self,"board_search") else ""; self.board_cursor=None; self.board_has_more=True; self.board_loading=False
        self.board_search_note.setText(f'Searching all users for "{self.board_query}"' if self.board_query else "")
        self.load_more_leaderboard()

    def leaderboard_scroll(self,value):
        if not hasattr(self,"board_scroll") or self.board_loading or not self.board_has_more:return
        bar=self.board_scroll.verticalScrollBar()
        if bar.maximum()-value<220:self.load_more_leaderboard()

    def load_more_leaderboard(self):
        if self.board_loading or not self.board_has_more:return
        self.board_loading=True; loading=QLabel("Loading more..." if self.board_cursor else ("Searching..." if self.board_query else "Loading leaderboard...")); loading.setObjectName("muted"); loading.setProperty("boardLoading",True)
        self.board_layout.insertWidget(self.board_layout.count()-1,loading); self.app.load_leaderboard(self.board_query,self.board_cursor,bool(self.board_cursor))

    def show_leaderboard(self,rows,cursor,append,has_more):
        self.board_loading=False; self.board_has_more=bool(has_more); self.board_cursor=cursor
        for i in range(self.board_layout.count()-1,-1,-1):
            item=self.board_layout.itemAt(i); w=item.widget()
            if w and w.property("boardLoading"):
                self.board_layout.takeAt(i); w.deleteLater()
        if not append:
            for i in range(self.board_layout.count()-2,-1,-1):
                item=self.board_layout.takeAt(i)
                if item.widget():item.widget().deleteLater()
        if not rows and not append:
            empty=QLabel("No users found." if self.board_query else "Nobody is on this leaderboard yet."); empty.setObjectName("muted"); self.board_layout.insertWidget(0,empty); return
        me=str(getattr(self.app.cloud.user,"id",""))
        for row in rows:
            profile=row.get("profiles") or {}; rank=int(row.get("rank") or 0); uid=row.get("user_id") or profile.get("id"); card=ClickableFrame(uid); card.setObjectName("statCard"); card.clicked.connect(self.app.open_public_profile)
            line=QHBoxLayout(card); line.setContentsMargins(12,10,14,10); line.setSpacing(12)
            num=QLabel(); num.setObjectName("rank"); num.setFixedWidth(56); set_rank_text(num,f"#{rank:,}" if rank else "—",rank,14)
            display=profile.get("display_name") or profile.get("username") or "Unknown user"; avatar=AvatarLabel(48,self.app.accent,display); path=profile.get("avatar_path") or ""
            if path:self.app.load_avatar_widget(avatar,self.app.cloud.avatar_url(path))
            names=QVBoxLayout(); name=QLabel(display+("  (you)" if uid==me else "")); name.setObjectName("statName"); name.setWordWrap(True); name.setMinimumWidth(0); username=QLabel("@"+(profile.get("username") or "unknown")); username.setObjectName("muted"); username.setWordWrap(True); username.setMinimumWidth(0)
            names.addWidget(name); names.addWidget(username); value_label=QLabel(f"{int(row.get('total_plays') or 0):,} songs"); value_label.setObjectName("playCount")
            line.addWidget(num); line.addWidget(avatar); line.addLayout(names,1); line.addWidget(value_label); self.board_layout.insertWidget(self.board_layout.count()-1,card)
        if not self.board_has_more:
            end=QLabel("End of leaderboard"); end.setObjectName("mutedSmall"); end.setAlignment(Qt.AlignCenter); self.board_layout.insertWidget(self.board_layout.count()-1,end)

    def show_public_profile(self,data):
        if not data:
            d=CleanDialog(self,"Profile","Couldn't load that profile.",self.app.accent,"Close",False); d.exec(); return
        PublicProfileDialog(self,self.app,data).exec()

    def profile_page(self):
        page=QWidget(); page.setObjectName("page")
        outer=QVBoxLayout(page); outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        content=QWidget(); content.setObjectName("scrollBody"); layout=QVBoxLayout(content); layout.setContentsMargins(22,18,26,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Profile", "Your name, picture and listening highlights."))
        layout.addWidget(self.lastfm_note("Last.fm is off, so your public stats can miss some plays."))

        top=QFrame(); top.setObjectName("section"); t=QHBoxLayout(top); t.setContentsMargins(16,16,16,16); t.setSpacing(16)
        self.profile_avatar=QLabel(); self.profile_avatar.setFixedSize(92,92); self.profile_avatar.setAlignment(Qt.AlignCenter); self.profile_avatar.setObjectName("profileAvatar")
        self.set_profile_avatar(b"")
        avatar_words=QVBoxLayout(); avatar_words.setSpacing(5); avatar_words.addWidget(self.section_title("Profile picture"))
        avatar_note=QLabel("Your profile picture is visible on leaderboards and your public profile."); avatar_note.setObjectName("muted"); avatar_note.setWordWrap(True); avatar_words.addWidget(avatar_note)
        avatar_buttons=QHBoxLayout(); choose=QPushButton("Choose picture"); remove=QPushButton("Remove"); choose.clicked.connect(self.choose_profile_picture); remove.clicked.connect(self.remove_profile_picture); avatar_buttons.addWidget(choose); avatar_buttons.addWidget(remove); avatar_buttons.addStretch(); avatar_words.addLayout(avatar_buttons)
        t.addWidget(self.profile_avatar,0,Qt.AlignTop); t.addLayout(avatar_words,1); layout.addWidget(top)

        about=QFrame(); about.setObjectName("section"); a=QVBoxLayout(about); a.addWidget(self.section_title("Tell us about yourself."))
        self.profile_display=QLineEdit(); self.profile_display.setMaxLength(32); self.profile_display.setPlaceholderText("Display name"); self.profile_display.textChanged.connect(self.profile_preview_update)
        self.profile_description=QTextEdit(); self.profile_description.setPlaceholderText("Description (max 160 characters)"); self.profile_description.setFixedHeight(86)
        self.profile_description.textChanged.connect(self.profile_description_changed)
        self.profile_desc_count=QLabel("0 / 160"); self.profile_desc_count.setObjectName("mutedSmall")
        save=QPushButton("Save profile"); save.setObjectName("primaryAction"); save.clicked.connect(self.save_profile)
        a.addWidget(QLabel("Display name")); a.addWidget(self.profile_display); a.addWidget(QLabel("Description")); a.addWidget(self.profile_description); a.addWidget(self.profile_desc_count,0,Qt.AlignRight); a.addWidget(save,0,Qt.AlignLeft); layout.addWidget(about)

        username_box=QFrame(); username_box.setObjectName("section"); u=QVBoxLayout(username_box); u.addWidget(self.section_title("Username"))
        note=QLabel("Usernames are unique. Changing yours requires your current password."); note.setObjectName("muted"); note.setWordWrap(True); u.addWidget(note)
        userrow=QHBoxLayout(); self.profile_username=QLineEdit(); self.profile_username.setMaxLength(24); self.profile_username.setPlaceholderText("Username"); self.profile_username.textChanged.connect(self.profile_preview_update)
        self.profile_username_password=QLineEdit(); self.profile_username_password.setEchoMode(QLineEdit.Password); self.profile_username_password.setPlaceholderText("Current password")
        change=QPushButton("Change username"); change.clicked.connect(self.change_profile_username); userrow.addWidget(self.profile_username,1); userrow.addWidget(self.profile_username_password,1); userrow.addWidget(change); u.addLayout(userrow); layout.addWidget(username_box)

        showcase=QFrame(); showcase.setObjectName("section"); sh=QVBoxLayout(showcase); sh.addWidget(self.section_title("What shows on your profile"))
        showcase_note=QLabel("Select what you want to display on your profile."); showcase_note.setObjectName("muted"); showcase_note.setWordWrap(True); sh.addWidget(showcase_note)
        self.profile_showcase_controls={}; config=self.profile_showcase_data()
        grid=QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(8); grid.setContentsMargins(0,5,0,3)
        grid.addWidget(QLabel(""),0,0)
        for col,period in enumerate(("Week","Month","Year","All time"),1):
            label=QLabel(period); label.setAlignment(Qt.AlignCenter); label.setObjectName("mutedSmall"); grid.addWidget(label,0,col)
        for row,(key,title) in enumerate((("artists","Top artists"),("songs","Top songs"),("albums","Top albums")),1):
            name=QLabel(title); name.setStyleSheet("font-weight:650;font-size:13px;"); grid.addWidget(name,row,0)
            for col,period in enumerate(("Week","Month","Year","All time"),1):
                checked=bool(((config.get(key) or {}).get(period) or {}).get("visible")); toggle=ToggleSwitch(checked,self.app.accent); toggle.toggled.connect(self.profile_preview_update)
                wrap=QWidget(); wl=QHBoxLayout(wrap); wl.setContentsMargins(0,0,0,0); wl.addStretch(); wl.addWidget(toggle); wl.addStretch(); grid.addWidget(wrap,row,col); self.profile_showcase_controls[(key,period)]=toggle
        grid.setColumnStretch(0,1); sh.addLayout(grid)
        save_showcase=QPushButton("Save profile visibility"); save_showcase.clicked.connect(self.save_profile); sh.addWidget(save_showcase,0,Qt.AlignLeft); layout.addWidget(showcase)

        preview=QFrame(); preview.setObjectName("section"); pv=QVBoxLayout(preview); pv.addWidget(self.section_title("Profile preview"))
        preview_row=QHBoxLayout(); preview_row.setSpacing(14); self.profile_preview_avatar=QLabel(); self.profile_preview_avatar.setFixedSize(72,72); self.profile_preview_avatar.setAlignment(Qt.AlignCenter)
        self.profile_preview=QLabel(); self.profile_preview.setWordWrap(True); self.profile_preview.setTextInteractionFlags(Qt.TextSelectableByMouse); preview_row.addWidget(self.profile_preview_avatar,0,Qt.AlignTop); preview_row.addWidget(self.profile_preview,1); pv.addLayout(preview_row); layout.addWidget(preview)
        self.profile_status=QLabel(""); self.profile_status.setObjectName("muted"); self.profile_status.setWordWrap(True); layout.addWidget(self.profile_status)
        layout.addStretch(); scroll.setWidget(content); outer.addWidget(scroll); QTimer.singleShot(0,self.refresh_profile_page); return page

    def friends_page(self):
        page=QWidget(); page.setObjectName("page"); layout=QVBoxLayout(page); layout.setContentsMargins(22,18,22,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Friends", "Like and subscribe for the friends tab guys"))
        self.friends_meme=QLabel(); self.friends_meme.setAlignment(Qt.AlignCenter)
        layout.addStretch(); layout.addWidget(self.friends_meme,0,Qt.AlignCenter); layout.addStretch()
        QTimer.singleShot(0,self.load_friends_gif)
        return page

    def load_friends_gif(self):
        if not hasattr(self,"friends_meme"):return
        path=image_cache_dir/"friends.gif"
        if path.exists():
            try:
                with Image.open(path) as gif:
                    if gif.format=="GIF" and gif.width*gif.height<=4000000:self.start_friends_gif(path); return
            except Exception:pass
            try:path.unlink()
            except Exception:pass
        def worker():
            try:
                with requests.get("https://media1.tenor.com/m/oVSZ7PKhDqsAAAAd/hello-neighbor-like-and-subscribe.gif",headers={"User-Agent":"Amethyst"},timeout=(5,15),stream=True,allow_redirects=False) as r:
                    r.raise_for_status()
                    if "gif" not in str(r.headers.get("content-type") or "").lower():return
                    data=bytearray()
                    for chunk in r.iter_content(65536):
                        if chunk:data.extend(chunk)
                        if len(data)>8*1024*1024:return
                if not bytes(data[:6]) in (b"GIF87a",b"GIF89a"):return
                path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data); self.friends_gif_ready.emit(str(path))
            except Exception as e:print("Friends GIF failed:",e)
        threading.Thread(target=worker,daemon=True).start()

    def start_friends_gif(self,path):
        if not hasattr(self,"friends_meme") or not os.path.exists(str(path)):return
        self.friends_movie=QMovie(str(path)); self.friends_movie.setCacheMode(QMovie.CacheAll); self.friends_movie.setScaledSize(QSize(498,374)); self.friends_meme.setMovie(self.friends_movie); self.friends_movie.start()

    def profile_showcase_data(self):
        return normalise_profile_showcase((self.app.profile or {}).get("profile_showcase") or {})

    def profile_description_changed(self):
        if not hasattr(self,"profile_description"):return
        text=self.profile_description.toPlainText()
        if len(text)>160:
            cursor=self.profile_description.textCursor(); pos=cursor.position(); self.profile_description.blockSignals(True); self.profile_description.setPlainText(text[:160]); cursor=self.profile_description.textCursor(); cursor.setPosition(min(pos,160)); self.profile_description.setTextCursor(cursor); self.profile_description.blockSignals(False); text=text[:160]
        self.profile_desc_count.setText(f"{len(text)} / 160")
        self.profile_preview_update()

    def refresh_profile_page(self):
        if not hasattr(self,"profile_display"):return
        p=self.app.profile or {}; self.profile_display.setText(p.get("display_name","") or ""); self.profile_username.setText(p.get("username","") or ""); self.profile_description.setPlainText(p.get("description","") or ""); self.profile_description_changed()
        config=self.profile_showcase_data()
        for (key,period),toggle in getattr(self,"profile_showcase_controls",{}).items():toggle.setChecked(bool(((config.get(key) or {}).get(period) or {}).get("visible")))
        self.profile_preview_update(); self.load_profile_avatar(); self.app.load_own_rank()

    def current_showcase_payload(self):
        payload={"artists":{},"songs":{},"albums":{}}; kinds={"artists":"artist","songs":"song","albums":"album"}
        for (key,period),toggle in self.profile_showcase_controls.items():
            visible=toggle.isChecked(); items=[]
            if visible:
                for row in self.app.db.top(kinds[key],period,5):
                    item={"name":str(row["name"] or "")[:120],"plays":max(0,int(row["plays"] or 0))}
                    if row["extra"]:item["extra"]=str(row["extra"] or "")[:120]
                    if row["image"]:item["image"]=str(row["image"] or "")[:500]
                    items.append(item)
            payload[key][period]={"visible":visible,"items":items}
        return payload

    def profile_preview_update(self,*args):
        if not hasattr(self,"profile_preview"):return
        lines=[]; display=self.profile_display.text().strip() or (self.app.profile or {}).get("display_name") or "Your display name"; username=self.profile_username.text().strip() or (self.app.profile or {}).get("username") or "username"
        desc=self.profile_description.toPlainText().strip(); lines.append(f"{display}  •  @{username}")
        created=(self.app.profile or {}).get("created_at")
        if created:
            try:lines.append("Joined "+datetime.fromisoformat(str(created).replace("Z","+00:00")).astimezone().strftime("%d %b %Y"))
            except Exception:pass
        rank_info=getattr(self,"profile_rank_info",{}) or {}; rank=rank_info.get("rank")
        if rank and int(rank)<=100:lines.append(f"Leaderboard #{int(rank)}  •  {int(rank_info.get('total_plays') or 0):,} songs played")
        if desc:lines.append(desc)
        data=self.current_showcase_payload() if hasattr(self,"profile_showcase_controls") else {}
        labels={"artists":"Top artists","songs":"Top songs","albums":"Top albums"}
        for key in ("artists","songs","albums"):
            for period in ("Week","Month","Year","All time"):
                sec=((data.get(key) or {}).get(period) or {})
                if not sec.get("visible"):continue
                items=[x for x in sec.get("items",[]) if x.get("name")]
                lines.append(f"\n{period} {labels[key]}")
                lines.append("  ".join(f"{i+1}. {x['name']} ({int(x.get('plays') or 0):,})" for i,x in enumerate(items)) if items else "Nothing here yet")
        self.profile_preview.setText("\n".join(lines))

    def save_profile(self):
        display=self.profile_display.text().strip(); desc=self.profile_description.toPlainText().strip(); showcase=self.current_showcase_payload()
        if not display:self.profile_status.setText("Display name can't be empty."); return
        self.profile_status.setText("Saving profile...")
        def worker():
            try:self.app.signals.profile.emit("saved",self.app.cloud.update_profile(display,desc,showcase))
            except Exception as e:self.app.signals.profile.emit("error",str(e))
        threading.Thread(target=worker,daemon=True).start()

    def change_profile_username(self):
        username=self.profile_username.text().strip().lstrip("@"); password=self.profile_username_password.text()
        if not username or not password:self.profile_status.setText("Enter the new username and your current password."); return
        self.profile_status.setText("Changing username...")
        def worker():
            try:self.app.signals.profile.emit("username",self.app.cloud.change_username(username,password))
            except Exception as e:self.app.signals.profile.emit("error",str(e))
        threading.Thread(target=worker,daemon=True).start()

    def prepare_profile_picture(self,path):
        if os.path.getsize(path)>12*1024*1024:raise ValueError("Pick an image smaller than 12 MB.")
        raw=Image.open(path)
        if raw.width*raw.height>40000000:raw.close(); raise ValueError("That image is too large. Pick one under 40 megapixels.")
        img=ImageOps.exif_transpose(raw).convert("RGB"); raw.close(); side=min(img.size); left=(img.width-side)//2; top=(img.height-side)//2
        img=img.crop((left,top,left+side,top+side)).resize((160,160),Image.Resampling.LANCZOS)
        data=b""
        for quality in (78,72,66,60,54,48,42,36):
            out=io.BytesIO(); img.save(out,"JPEG",quality=quality,optimize=True,progressive=True); data=out.getvalue()
            if len(data)<=20*1024:break
        if len(data)>20*1024:raise ValueError("Failed to compress image. Try a simpler image.")
        return data

    def choose_profile_picture(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose profile picture","","Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:return
        try:data=self.prepare_profile_picture(path)
        except Exception as e:self.profile_status.setText(str(e)); return
        self.profile_status.setText("Uploading picture...")
        def worker():
            try:self.app.signals.profile.emit("avatar",{"url":self.app.cloud.upload_avatar(data),"bytes":data})
            except Exception as e:self.app.signals.profile.emit("error","Profile picture upload failed: "+str(e))
        threading.Thread(target=worker,daemon=True).start()

    def remove_profile_picture(self):
        self.profile_status.setText("Removing picture...")
        def worker():
            try:self.app.cloud.remove_avatar(); self.app.signals.profile.emit("avatar_removed",True)
            except Exception as e:self.app.signals.profile.emit("error",str(e))
        threading.Thread(target=worker,daemon=True).start()

    def load_profile_avatar(self):
        path=(self.app.profile or {}).get("avatar_path","") or ""
        if not path:self.set_profile_avatar(b""); return
        url=self.app.cloud.avatar_url(path)
        def worker():
            data=self.app.image_bytes(url)
            self.app.signals.profile.emit("avatar_data",data)
        threading.Thread(target=worker,daemon=True).start()

    def set_profile_avatar(self,data):
        targets=[]
        if hasattr(self,"profile_avatar"): targets.append(self.profile_avatar)
        if hasattr(self,"profile_preview_avatar"): targets.append(self.profile_preview_avatar)
        if not targets:return
        letter=((self.app.profile or {}).get("display_name") or (self.app.profile or {}).get("username") or "?")[0].upper()
        for label in targets:
            pix=QPixmap()
            if data and pix.loadFromData(data):
                size=label.size(); scaled=pix.scaled(size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); out=QPixmap(size); out.fill(Qt.transparent)
                painter=QPainter(out); painter.setRenderHint(QPainter.Antialiasing); path=QPainterPath(); path.addEllipse(1,1,size.width()-2,size.height()-2); painter.setClipPath(path); painter.drawPixmap(0,0,scaled); painter.setClipping(False); painter.setPen(QPen(QColor(self.app.accent),2)); painter.setBrush(Qt.NoBrush); painter.drawEllipse(QRectF(1.5,1.5,size.width()-3,size.height()-3)); painter.end()
                label.setText(""); label.setPixmap(out); label.setStyleSheet("background:transparent;border:none;")
            else:
                label.setPixmap(QPixmap()); label.setText(letter); label.setStyleSheet(f"background:#202025;border:2px solid {self.app.accent};border-radius:{label.width()//2}px;font-size:{max(18,int(label.width()*.3))}px;font-weight:800;color:#eeeeF2;")

    def profile_result(self,kind,result):
        if kind=="rank": self.profile_rank_info=result or {}; self.profile_preview_update(); return
        if kind=="error":self.profile_status.setText(str(result)); self.load_profile_avatar(); return
        if kind in ("saved","username"):
            self.app.profile=self.app.cloud.profile or self.app.profile; self.profile_status.setText("Profile saved." if kind=="saved" else "Username changed."); self.profile_username_password.clear(); self.refresh_profile_labels(); self.refresh_profile_page(); return
        if kind=="avatar":
            self.app.profile=self.app.cloud.profile or self.app.profile; self.profile_status.setText("Profile picture updated."); self.set_profile_avatar((result or {}).get("bytes",b"")); return
        if kind=="avatar_removed":
            self.app.profile=self.app.cloud.profile or self.app.profile; self.profile_status.setText("Profile picture removed."); self.set_profile_avatar(b""); return
        if kind=="avatar_data":self.set_profile_avatar(result or b"")

    def refresh_profile_labels(self):
        p=self.app.profile or {}; display=p.get("display_name") or p.get("username") or "there"; hour=datetime.now().hour; greeting="Good morning" if hour<12 else "Good afternoon" if hour<18 else "Good evening"
        if hasattr(self,"home_greeting"):self.home_greeting.setText(f"{greeting}, {display}")
        if hasattr(self,"account_name"):self.account_name.setText(p.get("display_name","") or "")
        if hasattr(self,"account_username"):self.account_username.setText("@"+(p.get("username","") or ""))

    def overlay_page(self):
        page = QWidget()
        page.setObjectName("page"); layout=QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(12)
        layout.addLayout(self.page_header("Overlay", "Customise the spotify overlay."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("scrollBody")
        form = QVBoxLayout(content); form.setContentsMargins(0, 2, 4, 2)
        form.setSpacing(10)

        position_box = QFrame()
        position_box.setObjectName("section")
        p = QVBoxLayout(position_box)
        p.addWidget(self.section_title("Position"))
        self.position = QComboBox()
        self.position.addItems([
            "Top left", "Top centre", "Top right", "Left centre", "Centre", "Right centre",
            "Bottom left", "Bottom centre", "Bottom right", "Custom"
        ])
        self.position.setCurrentText(self.app.settings.get("overlay_position", "Bottom centre"))
        self.position.currentTextChanged.connect(self.position_changed)
        p.addWidget(self.position)
        drag_note=QLabel("Custom lets you drag the overlay anywhere. All other positions stay locked in place.")
        drag_note.setObjectName("muted")
        drag_note.setWordWrap(True); p.addWidget(drag_note)

        form.addWidget(position_box)

        look = QFrame()
        look.setObjectName("section")
        l = QVBoxLayout(look)
        l.addWidget(self.section_title("Appearance"))

        opacity=QHBoxLayout()
        opacity.addWidget(QLabel("Opacity"))
        self.opacity = QSlider(Qt.Horizontal); self.opacity.setRange(55, 100)
        self.opacity.setValue(round(float(self.app.settings.get("overlay_opacity", .96)) * 100))
        self.opacity.valueChanged.connect(self.opacity_changed)
        reset_opacity = QPushButton("Reset")
        reset_opacity.clicked.connect(lambda: self.opacity.setValue(96))
        opacity.addWidget(self.opacity, 1)
        opacity.addWidget(reset_opacity)
        l.addLayout(opacity)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size")); self.size_mode = QComboBox()
        self.size_mode.addItems(["Compact", "Default", "Large", "Custom"])
        self.size_mode.setCurrentText(self.app.settings.get("overlay_size_mode", "Default"))
        self.size_mode.currentTextChanged.connect(self.size_changed)
        size_row.addWidget(self.size_mode, 1)
        l.addLayout(size_row)
        self.size_note = QLabel("Custom size lets you drag any edge or corner of the overlay to resize it.")
        self.size_note.setObjectName("muted")
        self.size_note.setWordWrap(True)
        l.addWidget(self.size_note)

        colour = QHBoxLayout()
        colour.addWidget(QLabel("Overlay colour"))
        self.colour_mode=QComboBox(); self.colour_mode.addItems(["Theme", "Album artwork", "Custom"])
        self.colour_mode.setCurrentText(self.app.settings.get("overlay_colour_mode", "Theme"))
        self.colour_mode.currentTextChanged.connect(self.colour_mode_changed)
        pick = QPushButton("Pick colour")
        pick.clicked.connect(self.choose_overlay_colour)
        reset_colour = QPushButton("Reset")
        reset_colour.clicked.connect(self.reset_overlay_colour)
        colour.addWidget(self.colour_mode)
        colour.addWidget(pick)
        colour.addWidget(reset_colour); l.addLayout(colour)
        form.addWidget(look)

        visible = QFrame()
        visible.setObjectName("section")
        v = QVBoxLayout(visible)
        v.addWidget(self.section_title("Show on overlay"))
        self.overlay_switches = {}
        for key, title, desc, default in [
            ("show_art", "Album artwork", "Show the cover beside the song.", True),
            ("show_progress", "Progress bar", "Show where you are in the song.", True),
            ("show_times", "Song times", "Show the current time and song length.", True),
            ("show_shuffle", "Shuffle", "Show the shuffle control.", True),
            ("show_repeat", "Repeat", "Show the repeat control.", True),
            ("show_volume", "Volume buttons", "Show volume down and up controls.", False),
            ("hide_playback_buttons", "Hide playback buttons", "Hide play/pause, skip and rewind from the overlay.", False),
            ("show_local_time", "Local time", "Show your computer's local time at the top of the overlay.", False),
            ("show_open_button", "Open app button", "Show an Open button on the overlay.", False),
        ]:
            row = SwitchRow(title, desc, bool(self.app.settings.get(key, default)), self.app.accent)
            row.check.toggled.connect(lambda value, name=key: self.overlay_toggle(name, value))
            self.overlay_switches[key] = row
            v.addWidget(row)
        clockrow=QHBoxLayout(); clockrow.addWidget(QLabel("Time format"))
        self.clock_format=QComboBox(); self.clock_format.addItems(["12 hour","24 hour"]); self.clock_format.setCurrentText(self.app.settings.get("overlay_time_format","12 hour"))
        self.clock_format.currentTextChanged.connect(self.local_time_format_changed); clockrow.addStretch(); clockrow.addWidget(self.clock_format); v.addLayout(clockrow)
        form.addWidget(visible)
        form.addStretch(); scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def settings_page(self):
        page=QWidget(); page.setObjectName("page")
        outer=QVBoxLayout(page); outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        content=QWidget(); content.setObjectName("scrollBody"); layout=QVBoxLayout(content); layout.setContentsMargins(22,18,26,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Settings", "Appearance and app settings."))
        appearance=QFrame(); appearance.setObjectName("section"); a=QVBoxLayout(appearance); a.addWidget(self.section_title("Appearance"))
        row=QHBoxLayout(); row.addWidget(QLabel("App colour")); row.addStretch(); colour=QPushButton("Choose colour"); colour.clicked.connect(self.choose_theme_colour); reset=QPushButton("Reset"); reset.clicked.connect(self.reset_theme_colour); row.addWidget(colour); row.addWidget(reset); a.addLayout(row); layout.addWidget(appearance)
        updates=SwitchRow("Auto update", "Check GitHub Releases when Amethyst starts and install newer versions automatically.", bool(self.app.settings.get("auto_update",False)), self.app.accent); updates.check.toggled.connect(self.auto_update_changed); layout.addWidget(updates)
        layout.addStretch(); scroll.setWidget(content); outer.addWidget(scroll); return page

    def account_page(self):
        page=QWidget(); page.setObjectName("page")
        outer=QVBoxLayout(page); outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        content=QWidget(); content.setObjectName("scrollBody"); layout=QVBoxLayout(content); layout.setContentsMargins(22,18,26,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Account", "Your profile, sign-in security and account controls."))
        profile_box=QFrame(); profile_box.setObjectName("section"); profile_layout=QVBoxLayout(profile_box); profile_layout.addWidget(self.section_title("Profile"))
        profile=self.app.profile or {}; self.account_name=QLabel(profile.get("display_name", "")); self.account_name.setStyleSheet("font-size:18px;font-weight:750;")
        self.account_username=QLabel("@"+profile.get("username", "")); self.account_username.setObjectName("muted")
        self.account_email=QLabel(self.app.cloud.user_email()); self.account_email.setObjectName("muted"); self.account_email.setTextInteractionFlags(Qt.TextSelectableByMouse)
        created=profile.get("created_at",""); created_text="Unknown"
        if created:
            try: created_text=datetime.fromisoformat(str(created).replace("Z","+00:00")).astimezone().strftime("%d %b %Y at %H:%M")
            except Exception: created_text=str(created)
        self.account_created=QLabel("Created " + created_text); self.account_created.setObjectName("muted")
        profile_layout.addWidget(self.account_name); profile_layout.addWidget(self.account_username); profile_layout.addSpacing(3); profile_layout.addWidget(self.account_email); profile_layout.addWidget(self.account_created); layout.addWidget(profile_box)

        lastfm=QFrame(); lastfm.setObjectName("section"); lf=QVBoxLayout(lastfm); lf.addWidget(self.section_title("Last.fm"))
        self.account_lastfm_status=QLabel("Checking..."); self.account_lastfm_status.setStyleSheet("font-size:15px;font-weight:700;")
        self.account_lastfm_user=QLabel(""); self.account_lastfm_user.setObjectName("muted"); self.account_lastfm_user.setWordWrap(True)
        self.account_lastfm_desc=QLabel("Checking your listening history connection."); self.account_lastfm_desc.setObjectName("mutedSmall"); self.account_lastfm_desc.setWordWrap(True)
        self.account_lastfm_button=QPushButton("Connect Last.fm"); self.account_lastfm_button.clicked.connect(self.app.lastfm_button_clicked)
        lf.addWidget(self.account_lastfm_status); lf.addWidget(self.account_lastfm_user); lf.addWidget(self.account_lastfm_desc); lf.addWidget(self.account_lastfm_button,0,Qt.AlignLeft); layout.addWidget(lastfm)

        security=QFrame(); security.setObjectName("section"); sec=QVBoxLayout(security); sec.addWidget(self.section_title("Security"))
        password_row=QHBoxLayout(); password_words=QVBoxLayout(); password_title=QLabel("Password"); password_title.setStyleSheet("font-weight:650;font-size:13px;")
        password_desc=QLabel("Change the password used for email/password sign in."); password_desc.setObjectName("muted"); password_desc.setWordWrap(True); password_words.addWidget(password_title); password_words.addWidget(password_desc)
        password_btn=QPushButton("Change password"); password_btn.clicked.connect(self.change_password); password_row.addLayout(password_words,1); password_row.addWidget(password_btn); sec.addLayout(password_row); sec.addSpacing(6)
        mfa_row=QHBoxLayout(); mfa_words=QVBoxLayout(); mfa_title=QLabel("Two-factor authentication"); mfa_title.setStyleSheet("font-weight:650;font-size:13px;")
        self.mfa_status=QLabel(""); self.mfa_status.setObjectName("muted"); self.mfa_status.setWordWrap(True); mfa_words.addWidget(mfa_title); mfa_words.addWidget(self.mfa_status); mfa_row.addLayout(mfa_words,1)
        self.mfa_btn=QPushButton(); self.mfa_btn.clicked.connect(self.mfa_clicked); mfa_row.addWidget(self.mfa_btn); sec.addLayout(mfa_row)
        guide=QLabel("Use google authenticator to generate a one time code when logging in.")
        guide.setObjectName("muted"); guide.setWordWrap(True); sec.addWidget(guide); layout.addWidget(security)

        session=QFrame(); session.setObjectName("section"); ss=QVBoxLayout(session); ss.addWidget(self.section_title("Session"))
        logout=QPushButton("Log out"); logout.clicked.connect(self.app.logout_account); ss.addWidget(logout,0,Qt.AlignLeft); layout.addWidget(session)

        danger=QFrame(); danger.setObjectName("dangerSection"); dg=QVBoxLayout(danger); dg.addWidget(self.section_title("Delete account"))
        warning=QLabel("Deleting your account permanently removes your leaderboard ranking, friends, profile and listening data.")
        warning.setObjectName("muted"); warning.setWordWrap(True); delete=QPushButton("Delete account"); delete.setObjectName("dangerButton"); delete.clicked.connect(self.delete_account)
        dg.addWidget(warning); dg.addWidget(delete,0,Qt.AlignLeft); layout.addWidget(danger); layout.addStretch()
        scroll.setWidget(content); outer.addWidget(scroll); self.refresh_account_page(); return page

    def refresh_account_page(self):
        if not hasattr(self,"mfa_status"): return
        enabled=self.app.cloud.has_mfa(); self.mfa_status.setText("Enabled" if enabled else "Disabled")
        self.mfa_btn.setText("Disable 2FA" if enabled else "Set up 2FA")

    def change_password(self):
        d=ChangePasswordDialog(self.app.cloud,self.app.accent,self); d.exec()
        if d.changed: self.app.window.set_status("Password changed")

    def mfa_clicked(self):
        if self.app.cloud.has_mfa():
            d=MFADisableDialog(self.app.cloud,self.app.accent,self); d.exec()
            if d.disabled: self.refresh_account_page(); self.app.window.set_status("Two-factor authentication disabled")
            return
        d=MFASetupDialog(self.app.cloud,self.app.accent,self); d.exec()
        if d.enabled: self.refresh_account_page(); self.app.window.set_status("Two-factor authentication enabled")

    def delete_account(self):
        d=DeleteAccountDialog(self.app.cloud,self.app.accent,self); d.exec()
        if d.deleted: self.app.account_deleted()

    def debug_page(self):
        page=QWidget(); page.setObjectName("page"); outer=QVBoxLayout(page); outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); content=QWidget(); content.setObjectName("scrollBody"); layout=QVBoxLayout(content); layout.setContentsMargins(22,18,26,22); layout.setSpacing(12)
        layout.addLayout(self.page_header("Developer", "silly lil developer panel"))
        counts=QFrame(); counts.setObjectName("section"); c=QHBoxLayout(counts); c.setContentsMargins(16,14,16,14)
        reg=QVBoxLayout(); reg.addWidget(QLabel("Registered accounts")); self.dev_registered=QLabel("--"); self.dev_registered.setStyleSheet(f"color:{self.app.accent};font-size:22px;font-weight:800;"); reg.addWidget(self.dev_registered)
        active=QVBoxLayout(); active.addWidget(QLabel("Active users")); self.dev_active=QLabel("--"); self.dev_active.setStyleSheet(f"color:{self.app.accent};font-size:22px;font-weight:800;"); active.addWidget(self.dev_active)
        refresh_counts=QPushButton("Refresh counts"); refresh_counts.clicked.connect(self.refresh_developer_counts); c.addLayout(reg); c.addSpacing(34); c.addLayout(active); c.addStretch(); c.addWidget(refresh_counts,0,Qt.AlignVCenter); layout.addWidget(counts)
        info=QFrame(); info.setObjectName("section"); i=QVBoxLayout(info); self.debug_info=QLabel(); self.debug_info.setObjectName("mono"); self.debug_info.setTextInteractionFlags(Qt.TextSelectableByMouse); i.addWidget(self.debug_info); layout.addWidget(info)

        storage=QFrame(); storage.setObjectName("section"); st=QVBoxLayout(storage); st.addWidget(self.section_title("Local storage + diagnostics"))
        self.cache_size_label=QLabel(self.cache_size_text()); self.cache_size_label.setObjectName("muted"); st.addWidget(self.cache_size_label)
        runtime=QLabel(f"Python {sys.version.split()[0]}  •  hotkey {self.app.current_hotkey or 'F1'}"); runtime.setObjectName("mono"); runtime.setWordWrap(True); st.addWidget(runtime)
        tools=QHBoxLayout(); open_data=QPushButton("Open AppData"); clear_cache=QPushButton("Clear artwork cache"); copy_diag=QPushButton("Copy diagnostics")
        open_data.clicked.connect(self.app.open_data_folder); clear_cache.clicked.connect(self.clear_artwork_cache); copy_diag.clicked.connect(self.copy_diagnostics)
        tools.addWidget(open_data); tools.addWidget(clear_cache); tools.addWidget(copy_diag); tools.addStretch(); st.addLayout(tools); layout.addWidget(storage)

        users=QFrame(); users.setObjectName("section"); u=QVBoxLayout(users); u.addWidget(self.section_title("User management"))
        searchrow=QHBoxLayout(); self.dev_search=QLineEdit(); self.dev_search.setPlaceholderText("Username"); lookup=QPushButton("Find user"); lookup.clicked.connect(self.developer_lookup); searchrow.addWidget(self.dev_search,1); searchrow.addWidget(lookup); u.addLayout(searchrow)
        self.dev_user_info=QLabel("Search for a user to manage their account."); self.dev_user_info.setObjectName("muted"); self.dev_user_info.setWordWrap(True); u.addWidget(self.dev_user_info); self.dev_target=None
        self.dev_reason=QLineEdit(); self.dev_reason.setPlaceholderText("Reason"); u.addWidget(self.dev_reason)
        duration=QHBoxLayout(); duration.addWidget(QLabel("Suspension")); self.dev_duration=QComboBox(); self.dev_duration.addItems(["1 hour","6 hours","1 day","3 days","7 days","30 days","Permanent"]); duration.addWidget(self.dev_duration,1); u.addLayout(duration)
        actions=QHBoxLayout(); suspend=QPushButton("Suspend"); unsuspend=QPushButton("Unsuspend"); delete=QPushButton("Delete account"); delete.setObjectName("dangerButton"); suspend.clicked.connect(self.developer_suspend); unsuspend.clicked.connect(self.developer_unsuspend); delete.clicked.connect(self.developer_delete); actions.addWidget(suspend); actions.addWidget(unsuspend); actions.addWidget(delete); actions.addStretch(); u.addLayout(actions)
        self.dev_status=QLabel(""); self.dev_status.setObjectName("muted"); self.dev_status.setWordWrap(True); u.addWidget(self.dev_status); layout.addWidget(users)

        layout.addStretch(); scroll.setWidget(content); outer.addWidget(scroll); self.update_debug(); QTimer.singleShot(0,self.refresh_developer_counts); return page

    def add_debug_page(self, select=True):
        if self.debug_page_index is not None:
            if select:self.nav.setCurrentRow(self.debug_page_index)
            return
        self.nav.addItem("Developer"); self.debug_page_index=self.pages.count(); self.pages.addWidget(self.debug_page())
        if select:self.nav.setCurrentRow(self.debug_page_index)

    def update_debug(self):
        if not hasattr(self,"debug_info"):return
        track=self.app.track or {}
        self.debug_info.setText(f"version: {version}\naccount: @{(self.app.profile or {}).get('username','-')}\ndeveloper: {self.app.is_developer}\nmedia: Windows Media\nhotkey: {self.app.current_hotkey}\ntrack id: {track.get('id','-')}\ndata: {data_dir}")

    def refresh_developer_counts(self):
        if not self.app.is_developer:return
        def worker():
            try:self.app.signals.developer.emit("counts",self.app.cloud.developer_counts())
            except DeveloperAccessViolation:self.app.signals.developer.emit("security_lock",None)
            except Exception as e:self.app.signals.developer.emit("error",str(e))
        threading.Thread(target=worker,daemon=True).start()

    def developer_lookup(self):
        username=self.dev_search.text().strip().lstrip("@")
        if not username:self.dev_status.setText("Enter a username first."); return
        self.dev_status.setText("Looking up user...")
        def worker():
            try:self.app.signals.developer.emit("lookup",self.app.cloud.developer_lookup(username))
            except DeveloperAccessViolation:self.app.signals.developer.emit("security_lock",None)
            except Exception as e:self.app.signals.developer.emit("error",str(e))
        threading.Thread(target=worker,daemon=True).start()

    def developer_result(self, kind, result):
        if kind=="security_lock":
            QTimer.singleShot(0,QApplication.instance().quit)
            return
        if kind=="error":
            if hasattr(self,"dev_status"):self.dev_status.setText(str(result))
            return
        if kind=="counts":
            data=result or {}
            if hasattr(self,"dev_registered"):self.dev_registered.setText(f"{int(data.get('registered') or 0):,}")
            if hasattr(self,"dev_active"):self.dev_active.setText(f"{int(data.get('active') or 0):,}")
            return
        if kind=="lookup":
            self.dev_target=result or None
            if not self.dev_target:self.dev_user_info.setText("No user found."); self.dev_status.setText(""); return
            x=self.dev_target; status="Suspended permanently" if x.get("suspended_permanent") else ("Suspended until "+str(x.get("suspended_until")) if x.get("suspended_until") else "Not suspended")
            if x.get("delete_pending"):status+=" • deletion pending"
            self.dev_user_info.setText(f"{x.get('display_name') or ''}  •  @{x.get('username') or ''}\n{status}"); self.dev_status.setText(""); return
        self.dev_status.setText(str(result or "Done")); QTimer.singleShot(250,self.developer_lookup); QTimer.singleShot(300,self.refresh_developer_counts)

    def developer_suspend(self):
        if not self.dev_target:self.dev_status.setText("Find a user first."); return
        reason=self.dev_reason.text().strip()
        if not reason:self.dev_status.setText("Add a reason for the suspension."); return
        values={"1 hour":3600,"6 hours":21600,"1 day":86400,"3 days":259200,"7 days":604800,"30 days":2592000,"Permanent":None}; seconds=values[self.dev_duration.currentText()]; uid=self.dev_target.get("id")
        def worker():
            try:self.app.signals.developer.emit("action",self.app.cloud.developer_suspend(uid,reason,seconds))
            except DeveloperAccessViolation:self.app.signals.developer.emit("security_lock",None)
            except Exception as e:self.app.signals.developer.emit("error",str(e))
        self.dev_status.setText("Suspending user..."); threading.Thread(target=worker,daemon=True).start()

    def developer_unsuspend(self):
        if not self.dev_target:self.dev_status.setText("Find a user first."); return
        uid=self.dev_target.get("id")
        def worker():
            try:self.app.signals.developer.emit("action",self.app.cloud.developer_unsuspend(uid))
            except DeveloperAccessViolation:self.app.signals.developer.emit("security_lock",None)
            except Exception as e:self.app.signals.developer.emit("error",str(e))
        self.dev_status.setText("Removing suspension..."); threading.Thread(target=worker,daemon=True).start()

    def developer_delete(self):
        if not self.dev_target:self.dev_status.setText("Find a user first."); return
        reason=self.dev_reason.text().strip() or "Account removed by a developer."; name=self.dev_target.get("username") or "this user"
        d=CleanDialog(self,"Delete account?",f"@{name} will see the reason the next time they open the app. They can acknowledge it immediately, otherwise the account is deleted automatically after 24 hours.",self.app.accent,"Queue deletion")
        d.exec()
        if not d.ok:return
        uid=self.dev_target.get("id")
        def worker():
            try:self.app.signals.developer.emit("action",self.app.cloud.developer_queue_delete(uid,reason))
            except DeveloperAccessViolation:self.app.signals.developer.emit("security_lock",None)
            except Exception as e:self.app.signals.developer.emit("error",str(e))
        self.dev_status.setText("Queueing account deletion..."); threading.Thread(target=worker,daemon=True).start()

    def change_page(self, index):
        self.pages.setCurrentIndex(index)
        if index == 1 and not self.app.guest:
            self.reload_stats()
        if index == 2 and not self.app.guest:
            self.reload_leaderboard()
        if index == 3 and not self.app.guest:
            self.refresh_profile_page()
        if index == 7 and not self.app.guest:
            self.refresh_account_page()
        if self.debug_page_index == index:
            self.update_debug(); self.refresh_developer_counts()

    def clear_stats(self):
        while self.stats_layout.count() > 1:
            item = self.stats_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.stat_cards= []

    def update_stats_subtitle(self):
        if not hasattr(self,"stats_subtitle") or not hasattr(self,"kind") or not hasattr(self,"period"):return
        kind=self.kind.currentText().lower(); period=self.period.currentText()
        when={"Week":"the last 7 days","Month":"the last 30 days","Year":"the last 12 months","All time":"all saved listening history"}.get(period,period.lower())
        self.stats_subtitle.setText(f"Your top {kind} from {when}.")

    def schedule_stats_reload(self,*args):
        self.update_stats_subtitle()
        if hasattr(self,"stats_filter_timer"):self.stats_filter_timer.start()

    def live_stats_refresh(self):
        if hasattr(self,"stats_filter_timer") and self.stats_filter_timer.isActive():return
        if not self.app.guest and self.nav.currentRow()==1 and self.isVisible(): self.reload_stats(live=True)

    def reload_stats(self,*args,live=False):
        if not hasattr(self,"stats_layout"):return
        scroll_pos=self.stats_scroll.verticalScrollBar().value() if live else 0
        kind={"Artists":"artist","Albums":"album","Songs":"song"}[self.kind.currentText()]
        rows=self.app.db.top(kind,self.period.currentText(),50)

        # live stats used to rebuild every card every second which absolutely murdered Qt lol
        if live and rows and len(rows)==len(self.stat_cards):
            row_keys=[str(row["item_key"]) for row in rows]
            card_keys=[str(getattr(card,"item_key",None)) for card in self.stat_cards]
            if row_keys==card_keys and all(card.kind==kind for card in self.stat_cards):
                for card,row in zip(self.stat_cards,rows):card.update_values(row["plays"],row["listening_ms"])
                return

        self.stats_body.setUpdatesEnabled(False)
        try:
            self.clear_stats()
            if not rows:
                empty=QLabel("No stats yet. Play some music and come back later."); empty.setObjectName("empty"); self.stats_layout.insertWidget(0,empty); return
            missing=[]
            for index,row in enumerate(rows,1):
                card=StatCard(index,kind,row["name"],row["extra"],row["plays"],row["listening_ms"],row["duration_ms"],self.app.accent); card.item_key=row["item_key"]
                self.stats_layout.insertWidget(index-1,card); self.stat_cards.append(card)
                if row["image"]: self.app.load_stat_image(card,row["image"])
                elif kind=="artist": missing.append({"key":row["item_key"],"name":row["name"]})
            if missing:self.app.load_artist_images(missing)
        finally:
            self.stats_body.setUpdatesEnabled(True)
            self.stats_body.update()
        if live: QTimer.singleShot(0,lambda v=scroll_pos:self.stats_scroll.verticalScrollBar().setValue(v))


    def set_active_users(self,count):
        if hasattr(self,"active_users_label"):
            self.active_users_label.setText(f"{int(count or 0):,}")

    def set_status(self, text):
        text=str(text or "").strip()
        if not text:return
        self.toast.setText(text); self.toast.adjustSize(); self.position_toast(); self.toast.show(); self.toast.raise_(); self.toast_timer.start(2800)

    def position_toast(self):
        if not hasattr(self,"toast"):return
        self.toast.adjustSize(); self.toast.move(max(14,self.width()-self.toast.width()-24),max(46,self.height()-self.toast.height()-24))

    def resizeEvent(self,event):
        super().resizeEvent(event); self.position_toast()

    def update_overlay_button(self):
        if not hasattr(self,"overlay_toggle_btn"):return
        on=self.app.overlay.isVisible() if hasattr(self.app,"overlay") else False
        self.overlay_toggle_btn.setText("Overlay: Shown" if on else "Overlay: Hidden")
        self.overlay_toggle_btn.setObjectName("overlayOnButton" if on else "overlayOffButton")
        self.overlay_toggle_btn.style().unpolish(self.overlay_toggle_btn); self.overlay_toggle_btn.style().polish(self.overlay_toggle_btn)

    def set_home_art(self,data=b""):
        if not hasattr(self,"home_cover"):return
        pix=QPixmap()
        if data and pix.loadFromData(data):
            self.home_cover.setText(""); self.home_cover.setPixmap(pix.scaled(self.home_cover.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)); return
        self.home_cover.clear(); local=bool((self.app.track or {}).get("is_local")); self.home_cover.setText("♫\nLOCAL" if local else "♪")

    def set_lastfm_status(self,connected,username=""):
        for thing in getattr(self,"lastfm_notices",[]):thing.setVisible(not connected and not self.app.guest)
        if connected:
            self.home_status.setText("Connected"); self.home_status.setStyleSheet("font-size:16px;font-weight:700;color:#43d17a;")
            self.home_history_note.setText(f"@{username}" if username else "Last.fm is connected")
            self.home_lastfm_extra.setText("Listening history sync is on."); self.lastfm_btn.hide(); self.spotify_status_card.setObjectName("historyStatusOn")
            if hasattr(self,"account_lastfm_status"):self.account_lastfm_status.setText("Connected"); self.account_lastfm_status.setStyleSheet("font-size:15px;font-weight:700;color:#43d17a;")
            if hasattr(self,"account_lastfm_user"):self.account_lastfm_user.setText(f"@{username}" if username else "Connected account")
            if hasattr(self,"account_lastfm_desc"):self.account_lastfm_desc.setText("Listening history can sync while Amethyst is closed.")
            if hasattr(self,"account_lastfm_button"):self.account_lastfm_button.setText("Disconnect Last.fm")
        else:
            self.home_status.setText("Not connected"); self.home_status.setStyleSheet("font-size:16px;font-weight:700;color:#ff6262;")
            self.home_history_note.setText("Only plays Amethyst sees while it is open are saved.")
            self.home_lastfm_extra.setText("Amethyst still works normally without it."); self.lastfm_btn.setText("Connect Last.fm"); self.lastfm_btn.show(); self.lastfm_btn.setEnabled(True); self.spotify_status_card.setObjectName("historyStatusOff")
            if hasattr(self,"account_lastfm_status"):self.account_lastfm_status.setText("Not connected"); self.account_lastfm_status.setStyleSheet("font-size:15px;font-weight:700;color:#ff6262;")
            if hasattr(self,"account_lastfm_user"):self.account_lastfm_user.setText("No Last.fm account linked")
            if hasattr(self,"account_lastfm_desc"):self.account_lastfm_desc.setText("Only plays seen while Amethyst is open are added to your history.")
            if hasattr(self,"account_lastfm_button"):self.account_lastfm_button.setText("Connect Last.fm")
        self.spotify_status_card.style().unpolish(self.spotify_status_card); self.spotify_status_card.style().polish(self.spotify_status_card)

    def set_track(self, track):
        if track:
            self.home_track.setText(track.get("title","Unknown song")); self.home_artist.setText(track.get("artist","")); self.home_state.setText("Playing" if track.get("is_playing") else "Paused")
        else:
            self.home_track.setText("Nothing playing"); self.home_artist.setText(""); self.home_state.setText("Waiting for Spotify on this PC"); self.set_home_art(b"")

    def position_changed(self, value):
        self.app.settings["overlay_position"] = value
        if value == "Custom":
            self.app.settings["custom_x"] = self.app.overlay.x(); self.app.settings["custom_y"] = self.app.overlay.y()
        save_settings(self.app.settings)
        self.app.position_overlay()

    def opacity_changed(self, value):
        self.app.settings["overlay_opacity"] = value / 100
        save_settings(self.app.settings)
        self.app.overlay.apply_settings()

    def size_changed(self, value):
        self.app.settings["overlay_size_mode"] = value
        save_settings(self.app.settings); self.app.overlay.apply_settings(first=True)
        self.app.position_overlay()

    def colour_mode_changed(self, value):
        self.app.settings["overlay_colour_mode"] = value
        save_settings(self.app.settings)
        self.app.overlay.apply_settings()

    def choose_overlay_colour(self):
        d=ColourDialog(self, self.app.settings.get("overlay_colour", self.app.accent), self.app.accent, "Overlay colour")
        d.exec()
        if not d.colour:
            return
        self.app.settings["overlay_colour"] = d.colour
        self.app.settings["overlay_colour_mode"] = "Custom"
        save_settings(self.app.settings)
        self.colour_mode.setCurrentText("Custom")
        self.app.overlay.apply_settings()

    def reset_overlay_colour(self):
        self.app.settings["overlay_colour_mode"] = "Theme"; self.app.settings["overlay_colour"] = self.app.accent
        save_settings(self.app.settings)
        self.colour_mode.setCurrentText("Theme")
        self.app.overlay.apply_settings()

    def overlay_toggle(self, key, value):
        self.app.settings[key] = value
        save_settings(self.app.settings)
        self.app.overlay.apply_settings()
        self.app.position_overlay()

    def local_time_format_changed(self,value):
        self.app.settings["overlay_time_format"]=value
        save_settings(self.app.settings)
        self.app.overlay.update_clock()

    def choose_theme_colour(self):
        d = ColourDialog(self, self.app.accent, self.app.accent, "App colour")
        d.exec()
        if not d.colour:
            return
        self.app.settings["theme_colour"] = d.colour
        self.app.accent = d.colour; save_settings(self.app.settings)
        self.apply_style()
        self.app.overlay.apply_settings()
        for row in self.overlay_switches.values():
            row.check.set_accent(self.app.accent)

    def reset_theme_colour(self):
        self.app.settings["theme_colour"] = "#b64fff"
        self.app.accent = "#b64fff"
        save_settings(self.app.settings)
        self.apply_style()
        self.app.overlay.apply_settings()
        for row in self.overlay_switches.values():
            row.check.set_accent(self.app.accent)

    def auto_update_changed(self, checked):
        self.app.settings["auto_update"] = checked; save_settings(self.app.settings)
        self.set_status("Auto update is on. Amethyst will check GitHub when it starts." if checked else "Auto update is off.")

    def cache_size_text(self):
        try:
            files=[x for x in image_cache_dir.iterdir() if x.is_file()]; size=sum(x.stat().st_size for x in files); mb=size/1024/1024
            return f"Artwork cache: {len(files):,} files • {mb:.1f} MB"
        except Exception:return "Artwork cache: unavailable"

    def clear_artwork_cache(self):
        removed=0
        try:
            for file in image_cache_dir.iterdir():
                if file.is_file():
                    try:file.unlink(); removed+=1
                    except Exception:pass
            self.app.image_memory.clear()
        except Exception:pass
        if hasattr(self,"cache_size_label"): self.cache_size_label.setText(self.cache_size_text())
        self.app.window.set_status(f"Cleared {removed:,} cached artwork files")

    def copy_diagnostics(self):
        track=self.app.track or {}; text=(f"{brand} {version}\npython: {sys.version.split()[0]}\nmedia: Windows Media\nhotkey: {self.app.current_hotkey}\noverlay: {self.app.overlay.x()},{self.app.overlay.y()} {self.app.overlay.width()}x{self.app.overlay.height()}\ntrack id: {track.get('id','-')}\ndata: {data_dir}")
        QApplication.clipboard().setText(text); self.app.window.set_status("Diagnostics copied")

    def hotkey_capture_started(self):
        if not hasattr(self,"home_hotkey_note"):return
        self.home_hotkey_note.setText("Waiting for a key... press your new shortcut now.")
        self.home_hotkey_note.setStyleSheet(f"color:{self.app.accent};font-weight:800;")

    def home_hotkey_changed(self):
        text=self.home_hotkey_edit.keySequence().toString(QKeySequence.PortableText) or "F1"
        self.app.settings["overlay_hotkey"]=text
        save_settings(self.app.settings)
        self.app.register_hotkey()
        if self.app.current_hotkey != text:
            self.home_hotkey_edit.blockSignals(True)
            self.home_hotkey_edit.setKeySequence(QKeySequence(self.app.current_hotkey or "F1"))
            self.home_hotkey_edit.blockSignals(False)
            self.set_status(f"Couldn't use {text}. Hotkey set to {self.app.current_hotkey or 'F1'} instead.")
        if hasattr(self,"home_hotkey_note"):
            self.home_hotkey_note.setStyleSheet("")
            self.home_hotkey_note.setObjectName("muted")
            self.home_hotkey_note.style().unpolish(self.home_hotkey_note); self.home_hotkey_note.style().polish(self.home_hotkey_note)
            self.home_hotkey_note.setText(f"Current shortcut: {self.app.current_hotkey or 'F1'}. Click the box to replace it.")

    def apply_style(self):
        accent=self.app.accent; c=QColor(accent); soft=f"rgba({c.red()},{c.green()},{c.blue()},105)"; faint=f"rgba({c.red()},{c.green()},{c.blue()},55)"
        style = f"""
            QWidget {{ color:#eeeeF2; font-family:'Segoe UI'; font-size:12px; }}
            #base {{ background:#0b0b0e; border:1px solid {accent}; }}
            #page, #scrollBody, QScrollArea, QScrollArea > QWidget > QWidget {{ background:#111114; }}
            #titlebar {{ background:#111114; border-bottom:1px solid #26262b; }}
            #windowTitle {{ font-weight:700; }}
            #windowButton, #closeButton {{ border:none; border-radius:7px; background:transparent; color:#b8b8c1; font-size:17px; }}
            #windowButton:hover {{ background:#28282e; color:white; }}
            #closeButton:hover {{ background:#d94848; color:white; }}
            #nav {{ background:#121215; border:1px solid {soft}; border-radius:11px; padding:7px; outline:none; }}
            #nav::item {{ padding:12px 10px; margin:2px; border-radius:8px; color:#a8a8b1; }}
            #nav::item:hover {{ background:#1d1d21; color:white; }}
            #nav::item:selected {{ background:#25252a; color:white; border-left:3px solid {accent}; }}
            #pages {{ background:#111114; border:1px solid {soft}; border-radius:11px; }}
            #big {{ font-size:25px; font-weight:700; }}
            #muted {{ color:#96969f; }}
            #mutedSmall {{ color:#757580; font-size:10px; }}
            #hero {{ background:#18181c; border:1px solid {faint}; border-radius:12px; }}
            #homeCover {{ background:#202024; border:1px solid #303036; border-radius:10px; color:#8f8f99; font-size:25px; font-weight:700; }}
            #homeTrack {{ font-size:21px; font-weight:650; }}
            #statusPill {{ background:{accent}; color:#090a0d; border-radius:8px; padding:5px 9px; font-weight:750; }}
            #section {{ background:#17171b; border:1px solid {soft}; border-radius:11px; }}
            #historyStatusOn {{ background:#15301f; border:1px solid #28c76f; border-radius:11px; }}
            #historyStatusOff {{ background:#32191d; border:1px solid #e05260; border-radius:11px; }}
            #historyStatusChecking {{ background:#222228; border:1px solid {faint}; border-radius:11px; }}
            #lastfmNotice {{ background:#17171b; border:1px solid {faint}; border-radius:8px; }}
            #toast {{ background:#242429; border:1px solid {soft}; border-radius:9px; padding:9px 13px; color:#eeeeF2; font-weight:650; }}
            #dangerSection {{ background:#1a1214; border:1px solid #4a252a; border-radius:13px; }}
            #sectionTitle {{ font-size:15px; font-weight:700; }}
            #settingRow {{ background:#1b1b20; border:1px solid {soft}; border-radius:10px; }}
            #settingName {{ font-weight:650; font-size:13px; }}
            #statCard {{ background:#18181d; border:1px solid {soft}; border-radius:10px; }}
            #statCard:hover {{ background:#1e1e23; border-color:#37373e; }}
            #rank {{ color:#7e7e89; font-size:14px; font-weight:700; }}
            #statName {{ font-size:14px; font-weight:700; }}
            #playCount {{ font-size:14px; font-weight:700; color:{accent}; }}
            #empty {{ color:#777781; padding:30px; }}
            #mono {{ color:#bdbdc6; font-family:Consolas; line-height:1.4; }}
            QPushButton {{ background:#242429; border:1px solid {faint}; border-radius:8px; padding:8px 12px; color:#e7e7eb; }}
            QPushButton:hover {{ background:#2e2e34; border-color:{soft}; }}
            #primaryAction {{ background:{accent}; color:#090a0d; border:none; font-weight:750; }}
            #overlayOnButton {{ background:#173d2b; border:1px solid #2f8f61; color:#bff5d8; font-weight:750; }}
            #overlayOnButton:hover {{ background:#1d5138; border-color:#45b77d; }}
            #overlayOffButton {{ background:#401c20; border:1px solid #8b3d46; color:#ffd1d5; font-weight:750; }}
            #overlayOffButton:hover {{ background:#55242a; border-color:#b34d59; }}
            #dangerButton {{ background:#5b2025; border:1px solid #7f2d34; color:#ffdadd; font-weight:750; }}
            #dangerButton:hover {{ background:#742a31; border-color:#a43c45; }}
            QComboBox, QLineEdit, QKeySequenceEdit, QTextEdit {{ background:#202025; color:#eeeeF2; border:1px solid {faint}; border-radius:8px; padding:7px 9px; min-height:18px; selection-background-color:{accent}; selection-color:#090a0d; }}
            QComboBox {{ font-size:9pt; }}
            QComboBox:hover, QLineEdit:hover, QKeySequenceEdit:hover, QTextEdit:hover {{ border-color:{soft}; }}
            QComboBox::drop-down {{ border:none; width:24px; background:#202025; }}
            QComboBox QAbstractItemView, QListView {{ background:#202025; color:#eeeeF2; border:1px solid #38383f; selection-background-color:{accent}; selection-color:#090a0d; outline:none; padding:4px; }}
            QComboBox QAbstractItemView {{ font-size:9pt; }}
            QComboBox QAbstractItemView::item {{ background:#202025; color:#eeeeF2; min-height:26px; padding:4px 8px; }}
            QComboBox QAbstractItemView::item:selected {{ background:{accent}; color:#090a0d; }}
            QSlider::groove:horizontal {{ height:5px; background:#303036; border-radius:2px; }}
            QSlider::sub-page:horizontal {{ background:{accent}; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:14px; height:14px; margin:-5px 0; border-radius:7px; background:white; }}
            QScrollArea {{ border:none; }}
            QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:#34343b; border-radius:5px; min-height:30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """
        QApplication.instance().setStyleSheet(style)

class SpotifyApp:
    def __init__(self, splash=None):
        self.splash = splash
        if self.splash:self.splash.set_status("Reading settings.json...")
        self.settings=load_settings()
        self.accent= self.settings.get("theme_colour", "#b64fff")
        self.ready = False
        if self.splash:self.splash.set_status("Creating Supabase account client...")
        self.cloud = CloudClient(); self.guest=False
        if self.splash:
            self.splash.set_status("Restoring saved account session...")
            QApplication.processEvents()
        if not self.cloud.restore_session():
            if self.splash:
                self.splash.finish_now() if hasattr(self.splash,"finish_now") else self.splash.close(); self.splash=None
            auth = AuthDialog(self.cloud, self.accent)
            if auth.exec() != QDialog.Accepted:
                QTimer.singleShot(0, QApplication.instance().quit); return
            self.guest=bool(getattr(auth,"guest",False))
        elif self.splash:
            self.splash.set_status("Reading account profile...")
            if hasattr(self.splash,"set_target"): self.splash.set_target(88)
            QApplication.processEvents()

        if self.splash:self.splash.set_status("Loading profile data...")
        self.profile = {} if self.guest else (self.cloud.profile or {})
        self.is_developer=False if self.guest else self.cloud.is_developer()
        if self.splash:self.splash.set_status("Checking account suspension/deletion status...")

        while not self.guest:
            status=self.cloud.account_status()
            if status.get("delete_pending"):
                if self.splash:self.splash.finish_now() if hasattr(self.splash,"finish_now") else self.splash.close(); self.splash=None
                reason=status.get("delete_reason") or "No reason provided."
                try:self.cloud.finish_developer_delete()
                except Exception:
                    QTimer.singleShot(0,QApplication.instance().quit); return
                notice=DeveloperDeleteNoticeDialog(reason,self.accent)
                notice.exec(); QTimer.singleShot(0,QApplication.instance().quit); return
            if status.get("suspended"):
                if self.splash:self.splash.finish_now() if hasattr(self.splash,"finish_now") else self.splash.close(); self.splash=None
                suspended=SuspensionDialog(self.cloud,status,self.accent)
                if suspended.exec()!=QDialog.Accepted or not suspended.allowed: QTimer.singleShot(0,QApplication.instance().quit); return
            break

        if self.splash:
            self.splash.set_status("Cleaning old settings and preparing local data...")
            if hasattr(self.splash,"set_target"): self.splash.set_target(94)
        if "advanced_mode" in self.settings or "refresh_ms" in self.settings:
            self.settings.pop("advanced_mode",None); self.settings.pop("refresh_ms",None); save_settings(self.settings)
        if self.splash:self.splash.set_status("Opening stats.db...")
        self.db = StatsDB(":memory:") if self.guest else StatsDB()
        if self.splash:self.splash.set_status("Starting Windows media controls...")
        self.signals = Signals()
        self.track = None
        self.last_local_media_seen=0.0; self.local_media_missing=0
        self.play_candidate_key=None; self.play_candidate_ms=0; self.play_candidate_counted=False; self.last_progress_ms=0
        self.art_url = None
        self.artist_image_checked={}
        self.track_art_checked={}; self.track_art_cache={}
        self.current_hotkey = ""
        self.last_listen_tick = time.monotonic()
        self.last_listen_key = None
        self.last_cloud_sync = 0.0
        self.last_profile_sync = 0.0
        self.lastfm_connected=False; self.lastfm_username=""; self.lastfm_linking=False; self.lastfm_syncing=False
        self.image_memory = {}
        self.image_load_semaphore=threading.Semaphore(6)

        if self.splash:self.splash.set_status("Building windows and navigation...")
        self.overlay= Overlay(self); self.window=MainWindow(self)
        self.signals.art.connect(self.apply_art)
        self.signals.status.connect(self.window.set_status)
        self.signals.artist_images.connect(self.got_artist_images)
        self.signals.track_art.connect(self.got_track_art)
        self.signals.stat_image.connect(self.apply_stat_image)
        self.signals.leaderboard.connect(self.window.show_leaderboard)
        self.signals.developer.connect(self.window.developer_result)
        self.signals.active_users.connect(self.window.set_active_users)
        self.signals.profile.connect(self.window.profile_result)
        self.signals.public_profile.connect(self.window.show_public_profile)
        self.signals.history_synced.connect(self.history_backfilled)
        self.signals.security_check.connect(self.finish_developer_security_check)
        self.signals.local_playback.connect(self.got_local_playback)
        self.signals.lastfm_event.connect(self.handle_lastfm_event)

        if self.splash:self.splash.set_status("Starting Windows Media session listener...")
        self.local_media=MediaPoller(lambda track:self.signals.local_playback.emit(track))
        self.local_media_timer=QTimer(); self.local_media_timer.setInterval(1000); self.local_media_timer.timeout.connect(self.poll_local_media); self.local_media_timer.start()
        self.presence_timer=QTimer(); self.presence_timer.setInterval(60000); self.presence_timer.timeout.connect(self.send_presence)
        if not self.guest:self.presence_timer.start(); QTimer.singleShot(0,self.send_presence)

        if self.splash:self.splash.set_status("Registering hotkey and background timers...")
        self.hotkey_filter = HotkeyFilter(self)
        QApplication.instance().installNativeEventFilter(self.hotkey_filter)
        self.register_hotkey()
        QApplication.instance().aboutToQuit.connect(self.local_media.close)
        self.ready = True
        if self.splash:self.splash.set_status("Preloading artwork from local cache...")
        threading.Thread(target=self.preload_images, daemon=True).start(); QTimer.singleShot(100, self.start)
        if not self.guest:
            QTimer.singleShot(1000,self.verify_developer_state)
            QTimer.singleShot(2500,self.prefetch_missing_artist_images)

    def verify_developer_state(self):
        if self.guest:return
        def worker():
            self.signals.security_check.emit(bool(self.cloud.is_developer()))
        threading.Thread(target=worker,daemon=True).start()

    def finish_developer_security_check(self, real_developer):
        if self.is_developer and not real_developer:
            QApplication.instance().quit()

    def start(self):
        def show_main():
            self.window.show(); self.window.update_overlay_button()
            QApplication.instance().setQuitOnLastWindowClosed(True)
            # normal Amethyst playback is Windows Media now. no Spotify API login needed.
            if not self.guest:QTimer.singleShot(900,self.begin_lastfm_startup)
        if self.splash and hasattr(self.splash,"finish"):
            self.splash.set_status("Ready")
            splash=self.splash; self.splash=None; splash.finish(show_main); return
        if self.splash: self.splash.close(); self.splash=None
        show_main()



    def poll_local_media(self):
        try:self.local_media.request()
        except Exception:pass


    def got_local_playback(self,local):
        if local:
            self.local_media_missing=0; self.last_local_media_seen=time.monotonic(); self.got_playback(local); return
        self.local_media_missing+=1
        if self.local_media_missing>=3 and self.track:self.got_playback(None)

    def got_playback(self, track):
        now=time.monotonic(); previous_key=self.last_listen_key; previous_progress=self.last_progress_ms
        if track:
            early_key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
            cached_art=self.track_art_cache.get(early_key)
            if cached_art and not track.get("image"):track["image"]=cached_art
        self.track=track
        self.window.set_track(track); self.overlay.set_track(track)
        if not track:
            self.last_listen_key=None; self.last_listen_tick=now; self.last_progress_ms=0; self.play_candidate_key=None; self.play_candidate_ms=0; self.play_candidate_counted=False; self.art_url=None; self.signals.art.emit(b"",self.accent); return
        key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'; progress=int(track.get("progress_ms") or 0); duration=int(track.get("duration_ms") or 0)
        restarted=bool(previous_key==key and duration>0 and previous_progress>duration*.8 and progress<duration*.2)
        if key!=self.play_candidate_key or restarted:
            self.play_candidate_key=key; self.play_candidate_ms=0; self.play_candidate_counted=False
        delta_ms=0
        if not self.guest:
            if track.get("is_playing") and previous_key==key and not restarted:
                wall_ms=max(0,int((now-self.last_listen_tick)*1000))
                moved=max(0,progress-previous_progress)
                if moved and moved<=wall_ms+5000:delta_ms=min(wall_ms,moved+750)
                else:delta_ms=min(wall_ms,2000)
                if delta_ms>0:
                    self.db.add_listen(track,delta_ms); self.play_candidate_ms+=delta_ms
            threshold=30000
            if 0<duration<37500: threshold=max(20000,int(duration*.8))
            if track.get("is_playing") and not self.play_candidate_counted and self.play_candidate_ms>=threshold:
                self.db.add_play(track); local_start_ms=max(0,int(time.time()*1000)-max(0,progress)); self.db.record_play_event(track,local_start_ms,"local"); self.play_candidate_counted=True; threading.Thread(target=self.submit_cloud_play,daemon=True).start()
        self.last_listen_key=key; self.last_listen_tick=now; self.last_progress_ms=progress
        local_art=track.get("art_bytes") or b""
        url=track.get("image") if not track.get("is_local") else ""
        if local_art:
            art_key=f"local-art::{key}"
            if self.art_url!=art_key:
                self.art_url=art_key; self.signals.art.emit(bytes(local_art),self.accent)
        elif not url:
            if previous_key!=key:
                if self.art_url is not None:self.art_url=None
                self.signals.art.emit(b"",self.accent)
            self.resolve_track_art(track,key)
            missing_artists=[]
            for artist in track.get("artists") or []:
                if artist.get("name") and not artist.get("image"):missing_artists.append({"key":artist.get("id") or artist.get("name"),"name":artist.get("name")})
            if missing_artists:self.load_artist_images(missing_artists)
        elif url!=self.art_url:
            self.art_url=url; self.signals.art.emit(b"",self.accent); threading.Thread(target=self.get_art,args=(url,),daemon=True).start()
        if not self.guest and now-self.last_profile_sync>=300:
            config=(self.profile or {}).get("profile_showcase") or {}
            if isinstance(config,str):
                try:config=json.loads(config)
                except Exception:config={}
            if profile_showcase_is_visible(config):
                self.last_profile_sync=now; threading.Thread(target=self.sync_public_profile,daemon=True).start()
        self.window.update_debug()

    def public_showcase_from_db(self):
        saved=normalise_profile_showcase((self.profile or {}).get("profile_showcase") or {}); payload={"artists":{},"songs":{},"albums":{}}; kinds={"artists":"artist","songs":"song","albums":"album"}
        for key in ("artists","songs","albums"):
            for period in ("Week","Month","Year","All time"):
                visible=bool(((saved.get(key) or {}).get(period) or {}).get("visible")); items=[]
                if visible:
                    for row in self.db.top(kinds[key],period,5):
                        item={"name":str(row["name"] or "")[:120],"plays":max(0,int(row["plays"] or 0))}
                        if row["extra"]:item["extra"]=str(row["extra"] or "")[:120]
                        if row["image"]:item["image"]=str(row["image"] or "")[:500]
                        items.append(item)
                payload[key][period]={"visible":visible,"items":items}
        return payload

    def sync_public_profile(self):
        if self.guest or not self.cloud or not self.profile:return
        try:
            display=(self.profile or {}).get("display_name") or (self.profile or {}).get("username") or "User"; desc=(self.profile or {}).get("description") or ""
            self.cloud.update_profile(display,desc,self.public_showcase_from_db()); self.profile=self.cloud.profile or self.profile
        except Exception:pass

    def load_artist_images(self, artists):
        if not artists:return
        now=time.monotonic(); pending=[]
        for artist in artists:
            name=str((artist or {}).get("name") or "").strip(); key=(artist or {}).get("key") or (artist or {}).get("id") or name
            if not name:continue
            stamp=self.artist_image_checked.get(name.casefold(),0)
            if stamp and now-stamp<600:continue
            self.artist_image_checked[name.casefold()]=now; pending.append({"key":key,"name":name})
        if not pending:return
        def worker():
            found=[]
            for artist in pending:
                url=lookup_artist_image(artist["name"])
                if url and safe_image_url(url):found.append({"key":artist["key"],"name":artist["name"],"image":url})
                time.sleep(.04)
            if found:self.signals.artist_images.emit(found)
        threading.Thread(target=worker,daemon=True).start()

    def got_artist_images(self, artists):
        urls=[]
        for artist in artists:
            url=artist.get("image")
            self.db.update_image("artist",artist.get("key") or artist.get("id"),url)
            if url:urls.append(url)
        if urls:threading.Thread(target=self.cache_urls,args=(urls,),daemon=True).start()
        if self.window.nav.currentRow()==1:self.window.reload_stats()
        config=(self.profile or {}).get("profile_showcase") or {}
        if isinstance(config,str):
            try:config=json.loads(config)
            except Exception:config={}
        if urls and profile_showcase_is_visible(config):threading.Thread(target=self.sync_public_profile,daemon=True).start()

    def resolve_track_art(self,track,key):
        stamp=self.track_art_checked.get(key,0); now=time.monotonic()
        if stamp and now-stamp<60:return
        self.track_art_checked[key]=now
        artist=str(track.get("artist") or "").split(",")[0].strip(); title=str(track.get("title") or "").strip(); album=str(track.get("album") or "").strip()
        if not artist or not title:return
        def worker():
            url=lookup_track_art(artist,title,album)
            if url and safe_image_url(url):self.signals.track_art.emit(key,url)
        threading.Thread(target=worker,daemon=True).start()

    def got_track_art(self,key,url):
        track=self.track or {}; current=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
        if str(current)!=str(key):return
        self.track_art_cache[key]=url; track["image"]=url; self.art_url=url
        song_key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
        album_key=track.get("album_id") or f'{track.get("artist","")}::{track.get("album","")}'
        self.db.update_image("song",song_key,url); self.db.update_image("album",album_key,url)
        threading.Thread(target=self.get_art,args=(url,),daemon=True).start()

    def get_art(self, url):
        try:
            data = self.image_bytes(url)
            if not data:return
            image = Image.open(io.BytesIO(data)).convert("RGB"); image.thumbnail((60, 60))
            pixels = [p for p in image.getdata() if 35 < sum(p) / 3 < 235]
            if pixels:
                small = Image.new("RGB", (len(pixels), 1))
                small.putdata(pixels)
                r, g, b = [int(x) for x in ImageStat.Stat(small).mean]
                brightest = max(r, g, b)
                if brightest < 120:
                    scale = 120 / max(brightest, 1)
                    r, g, b = min(int(r*scale),255), min(int(g*scale),255), min(int(b*scale),255)
                colour=QColor(r, g, b).name()
            else:
                colour = self.accent
            if url!=self.art_url:return
            self.signals.art.emit(data,colour)
        except Exception:
            pass

    def apply_art(self,data,colour):
        self.overlay.set_art(data,colour); self.window.set_home_art(data)

    def cache_path(self, url):
        name= hashlib.sha256(url.encode("utf-8")).hexdigest() + ".img"
        return image_cache_dir / name

    def image_bytes(self, url):
        if not safe_image_url(url):return b""
        if url in self.image_memory:return self.image_memory[url]
        path=self.cache_path(url)
        try:
            if path.exists():
                data=path.read_bytes()
                if data and len(data)<=2*1024*1024 and valid_image_bytes(data):
                    self.image_memory[url]=data
                    return data
                if data:
                    try:path.unlink()
                    except Exception:pass
        except Exception:pass
        data=download_image_bytes(url)
        if data:
            self.image_memory[url]=data
            try:path.write_bytes(data)
            except Exception:pass
            return data
        return b""

    def cache_urls(self, urls):
        for url in dict.fromkeys(urls):
            self.image_bytes(url)

    def preload_images(self):
        self.cache_urls(self.db.image_urls())

    def prefetch_missing_artist_images(self):
        if self.guest:return
        try:rows=self.db.missing_artist_images(250)
        except Exception:return
        artists=[{"key":row["item_key"],"name":row["name"]} for row in rows if row["name"]]
        self.load_artist_images(artists)

    def apply_stat_image(self,card,data):
        if card not in getattr(self.window,"stat_cards",[]):return
        try:card.set_image(data)
        except RuntimeError:pass

    def load_stat_image(self, card, url):
        if url in self.image_memory:
            data=self.image_memory[url]
            delay=min(180,max(0,int(getattr(card,"rank",0))*3))
            QTimer.singleShot(delay,lambda c=card,d=data:self.apply_stat_image(c,d))
            return
        def worker():
            with self.image_load_semaphore:
                data=self.image_bytes(url)
            if data:self.signals.stat_image.emit(card,data)
        threading.Thread(target=worker,daemon=True).start()

    def send_presence(self):
        if self.guest:return
        def worker():
            try:self.signals.active_users.emit(self.cloud.heartbeat())
            except Exception:pass
        threading.Thread(target=worker,daemon=True).start()

    def begin_lastfm_startup(self):
        if self.guest:return
        def worker():
            try:data=self.cloud.lastfm_status()
            except Exception:data={"ok":False,"connected":False}
            self.signals.lastfm_event.emit("startup_status",data)
        threading.Thread(target=worker,daemon=True).start()

    def lastfm_button_clicked(self):
        if self.guest:return
        if self.lastfm_connected:
            d=CleanDialog(self.window,"Disconnect Last.fm?","Amethyst will stop recovering listening from while it was closed. Songs heard while Amethyst is open will still be counted.",self.accent,"Disconnect",True,"Keep connected")
            d.exec()
            if not d.ok:return
            def worker():
                try:self.cloud.disconnect_lastfm(); self.signals.lastfm_event.emit("disconnected",{})
                except Exception as e:self.signals.lastfm_event.emit("error",{"message":str(e)})
            threading.Thread(target=worker,daemon=True).start(); return
        self.connect_lastfm()

    def connect_lastfm(self):
        if self.guest or self.lastfm_linking:return
        self.lastfm_linking=True; self.window.set_status("Starting Last.fm setup...")
        def worker():
            try:self.signals.lastfm_event.emit("start_ready",self.cloud.start_lastfm_link())
            except Exception as e:self.signals.lastfm_event.emit("error",{"message":str(e)})
        threading.Thread(target=worker,daemon=True).start()

    def poll_lastfm_link(self):
        for _ in range(120):
            if not self.lastfm_linking:return
            time.sleep(1.5)
            try:data=self.cloud.lastfm_status()
            except Exception:continue
            if data.get("connected"):
                self.signals.lastfm_event.emit("linked",data); return
        self.signals.lastfm_event.emit("error",{"message":"Last.fm setup timed out. You can try again whenever you want."})

    def handle_lastfm_event(self,kind,data):
        data=data or {}
        if kind=="startup_status":
            if data.get("connected"):
                self.lastfm_connected=True; self.lastfm_username=str(data.get("username") or "")
                self.window.set_lastfm_status(True,self.lastfm_username)
                uid=str(getattr(getattr(self.cloud,"user",None),"id",""))
                seed=int(hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8],16) if uid else int(time.time())
                QTimer.singleShot(5000+(seed%55000),lambda:threading.Thread(target=self.sync_lastfm_history,daemon=True).start())
            elif data.get("ok") is False:
                self.window.set_lastfm_status(False,"")
                self.window.set_status("Last.fm service unavailable right now")
            else:
                self.window.set_lastfm_status(False,"")
                self.window.set_status("Last.fm is not connected")
            return

        if kind=="start_ready":
            url=str(data.get("authorize_url") or "")
            if not url:
                self.lastfm_linking=False; self.window.set_status("Couldn't start Last.fm setup"); return
            try:webbrowser.open(url,new=2)
            except Exception:pass
            self.lastfm_wait_dialog=LastFMWaitingDialog(self.window,self.accent)
            threading.Thread(target=self.poll_lastfm_link,daemon=True).start()
            self.lastfm_wait_dialog.exec()
            if not getattr(self.lastfm_wait_dialog,"connected",False):self.lastfm_linking=False
            return

        if kind=="linked":
            self.lastfm_linking=False; self.lastfm_connected=True; self.lastfm_username=str(data.get("username") or "")
            self.db.set_meta("lastfm_cursor_ms",int(time.time()*1000))
            if getattr(self,"lastfm_wait_dialog",None):
                try:self.lastfm_wait_dialog.success(self.lastfm_username)
                except Exception:pass
            self.window.set_lastfm_status(True,self.lastfm_username)
            self.window.set_status(f"Last.fm connected as @{self.lastfm_username}")
            QTimer.singleShot(180,self.show_lastfm_spotify_step)
            return

        if kind=="disconnected":
            self.lastfm_connected=False; self.lastfm_username=""; self.lastfm_linking=False
            self.window.set_lastfm_status(False,""); self.window.set_status("Last.fm disconnected")
            return

        if kind=="error":
            self.lastfm_linking=False
            message=str(data.get("message") or "Last.fm setup failed")
            self.window.set_status(message)
            if getattr(self,"lastfm_wait_dialog",None):
                try:self.lastfm_wait_dialog.status.setText(message)
                except Exception:pass
            return

    def show_lastfm_spotify_step(self):
        d=CleanDialog(
            self.window,
            "Spotify and Last.fm",
            f"Connected as @{self.lastfm_username}.\n\nIf Spotify is not linked to Last.fm yet, open the settings page and connect it there.",
            self.accent,
            "Open settings",
            True,
            "Done already"
        )
        d.exec()
        if d.ok:
            try:webbrowser.open(lastfm_setup_url,new=2)
            except Exception:pass

    def sync_lastfm_history(self):
        if self.guest or not self.lastfm_connected or self.lastfm_syncing:return
        self.lastfm_syncing=True
        try:
            now_ms=int(time.time()*1000); raw=self.db.get_meta("lastfm_cursor_ms")
            if raw is None:
                self.db.set_meta("lastfm_cursor_ms",now_ms); return
            try:after=int(raw)
            except Exception:after=now_ms
            if after>=now_ms-30000:return
            data=self.cloud.lastfm_recent(after)
            rows=data.get("tracks") or []; added=0
            rows=sorted(rows,key=lambda x:int(x.get("uts") or 0))
            for i,row in enumerate(rows):
                uts=int(row.get("uts") or 0)
                if not uts:continue
                artist=str(row.get("artist") or "").strip(); title=str(row.get("title") or "").strip(); album=str(row.get("album") or "").strip()
                if not artist or not title:continue
                track={
                    "id":f"lastfm::{artist.casefold()}::{title.casefold()}",
                    "title":title,
                    "artist":artist,
                    "artists":[{"name":artist,"image":""}],
                    "album":album or "Unknown album",
                    "album_id":f"lastfm::{artist.casefold()}::{(album or 'unknown album').casefold()}",
                    "image":"",
                    "is_local":False,
                    "duration_ms":0
                }
                next_uts=int((rows[i+1] if i+1<len(rows) else {}).get("uts") or 0)
                gap_ms=(next_uts-uts)*1000 if next_uts>uts else 180000
                listened=gap_ms if 30000<=gap_ms<=600000 else 180000
                played_at=datetime.fromtimestamp(uts).astimezone().isoformat()
                if self.db.add_history_play(track,played_at,listened,source="lastfm"):added+=1
            cursor=int(data.get("next_after_ms") or now_ms)
            self.db.set_meta("lastfm_cursor_ms",max(after,cursor))
            if added:
                try:self.cloud.record_play_batch(added)
                except Exception:pass
                self.signals.history_synced.emit(added)
            if data.get("truncated"):
                self.signals.status.emit("Last.fm caught up a large backlog • more will sync next time")
        except Exception as e:
            self.signals.status.emit(f"Last.fm catch-up couldn't finish: {e}")
        finally:
            self.lastfm_syncing=False



    def history_backfilled(self,count):
        if count<=0:return
        self.signals.status.emit(f"Caught up {count} Last.fm play{'s' if count!=1 else ''}")
        if self.window.nav.currentRow()==1:self.window.reload_stats()
        config=(self.profile or {}).get("profile_showcase") or {}
        if isinstance(config,str):
            try:config=json.loads(config)
            except Exception:config={}
        if profile_showcase_is_visible(config):threading.Thread(target=self.sync_public_profile,daemon=True).start()


    def submit_cloud_play(self):
        if self.guest:return
        try:self.cloud.record_play()
        except Exception:pass


    def load_leaderboard(self,query="",cursor=None,append=False):
        if self.guest:return
        def worker():
            try:
                rows,next_cursor,has_more=self.cloud.leaderboard_page(query=query,cursor=cursor,limit=50); self.signals.leaderboard.emit(rows,next_cursor,bool(append),bool(has_more))
            except Exception:self.signals.leaderboard.emit([],None,bool(append),False)
        threading.Thread(target=worker,daemon=True).start()

    def load_avatar_widget(self,widget,url):
        if not url:return
        if url in self.image_memory:widget.set_image(self.image_memory[url]); return
        def worker():
            data=self.image_bytes(url)
            if data:self.signals.stat_image.emit(widget,data)
        threading.Thread(target=worker,daemon=True).start()

    def open_public_profile(self,user_id):
        if self.guest or not user_id:return
        def worker():
            try:self.signals.public_profile.emit(self.cloud.public_profile(user_id) or {})
            except Exception:self.signals.public_profile.emit({})
        threading.Thread(target=worker,daemon=True).start()

    def load_own_rank(self):
        if self.guest:return
        def worker():
            try:self.signals.profile.emit("rank",self.cloud.my_leaderboard_rank() or {})
            except Exception:self.signals.profile.emit("rank",{})
        threading.Thread(target=worker,daemon=True).start()

    def control_value(self,field,default=None):return (self.track or {}).get(field,default)

    def show_control_state(self,changes):
        if not self.track:return
        self.track.update(changes); self.window.set_track(self.track); self.overlay.set_track(self.track)

    def local_media_active(self):return time.monotonic()-self.last_local_media_seen<5

    def play_pause(self):
        if not self.track or not self.local_media_active():return
        state=not bool(self.control_value("is_playing",False)); media_play_pause(); self.show_control_state({"is_playing":state})

    def next(self):
        if self.local_media_active():media_next_track()

    def previous(self):
        if self.local_media_active():media_previous_track()

    def seek(self,ms):
        if not self.local_media_active():return
        self.local_media.seek(ms); self.show_control_state({"progress_ms":max(0,int(ms))})

    def toggle_shuffle(self):
        if not self.track or not self.local_media_active():return
        state=not bool(self.control_value("shuffle",False)); self.local_media.shuffle(state); self.show_control_state({"shuffle":state})

    def toggle_repeat(self):
        if not self.track or not self.local_media_active():return
        current=self.control_value("repeat","off"); state={"off":"context","context":"track","track":"off"}.get(current,"off")
        self.local_media.repeat(state); self.show_control_state({"repeat":state})

    def volume_up(self):
        if self.local_media_active():media_volume_up()

    def volume_down(self):
        if self.local_media_active():media_volume_down()

    def sign_in_from_guest(self):
        self.restart()

    def logout_account(self):
        d = CleanDialog(self.window, "Log out?", "You'll need to sign in again next time unless you choose Remember me.", self.accent, "Log out")
        d.exec()
        if not d.ok:
            return
        self.cloud.logout()
        self.restart()

    def account_deleted(self):
        self.overlay.hide()
        d=CleanDialog(self.window,"Account deleted","Your online account, profile and leaderboard entry were deleted. Local listening stats on this PC were left alone.",self.accent,"Restart",False)
        d.exec(); self.restart()

    def show_overlay(self):
        self.overlay.show(); self.position_overlay(); self.window.update_overlay_button()

    def hide_overlay(self):
        self.overlay.hide(); self.window.update_overlay_button()

    def toggle_overlay(self):
        self.hide_overlay() if self.overlay.isVisible() else self.show_overlay()

    def position_overlay(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        pos = self.settings.get("overlay_position", "Bottom centre")
        if pos == "Custom":
            self.overlay.move(int(self.settings.get("custom_x", 100)), int(self.settings.get("custom_y", 100)))
            return

        area = screen.availableGeometry()
        if self.settings.get("overlay_size_mode") != "Custom": self.overlay.adjustSize()
        w, h = self.overlay.width(), self.overlay.height()
        pad=18
        x, y = area.left() + pad, area.top() + pad
        if "centre" in pos.lower() and pos not in ("Left centre", "Right centre"):
            x = area.left() + (area.width() - w) // 2
        if "right" in pos.lower():
            x = area.right() - w - pad
        if pos in ("Left centre", "Centre", "Right centre"):
            y = area.top() + (area.height() - h) // 2
        elif "bottom" in pos.lower():
            y = area.bottom() - h - pad
        self.overlay.move(x, y)

    def hotkey_parts(self, text):
        parts = [x.strip() for x in text.replace("Meta", "Win").split("+") if x.strip()]
        if not parts:
            parts = ["F1"]
        key=parts[-1].upper()
        mods = MOD_NOREPEAT
        for p in parts[:-1]:
            p = p.lower()
            if p in ("ctrl", "control"):
                mods |= MOD_CTRL
            elif p == "alt":
                mods |= MOD_ALT
            elif p == "shift":
                mods |= MOD_SHIFT
            elif p in ("win", "meta"):
                mods |= MOD_WIN
        if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            vk = 0x70 + int(key[1:]) - 1
        elif len(key) == 1 and key.isalnum():
            vk = ord(key)
        else:
            return None
        return mods, vk

    def register_hotkey(self):
        if sys.platform != "win32":
            return
        try:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        except Exception:
            pass
        hotkey = self.settings.get("overlay_hotkey", "F1")
        parsed = self.hotkey_parts(hotkey)
        if not parsed:
            hotkey, parsed = "F1", self.hotkey_parts("F1")
        mods, vk = parsed
        ok=ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, mods, vk)
        if not ok and hotkey != "F1":
            hotkey = "F1"; mods, vk = self.hotkey_parts("F1")
            ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, mods, vk)
            self.settings["overlay_hotkey"] = "F1"
            save_settings(self.settings)
        self.current_hotkey = hotkey
        self.window.update_debug()

    def open_data_folder(self):
        try:
            os.startfile(str(data_dir))
        except Exception:
            pass

    def fmt(self, ms):
        seconds = max(0, int((ms or 0) / 1000))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def restart(self):
        try:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        except Exception:
            pass
        subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])], cwd=os.path.dirname(os.path.abspath(sys.argv[0])))
        QApplication.quit()