import sys,importlib
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QProgressBar,QFrame,QHBoxLayout

class LoadingScreen(QWidget):
    def __init__(self,accent="#b64fff"):
        super().__init__(); self.accent=accent; self.value=0; self.target=90; self.done_callback=None
        self.setFixedSize(500,225); self.setWindowFlags(Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint); self.setAttribute(Qt.WA_TranslucentBackground,True); self.setObjectName("loadingScreen")
        outer=QVBoxLayout(self); outer.setContentsMargins(6,6,6,6); card=QFrame(); card.setObjectName("card"); card.setAttribute(Qt.WA_StyledBackground,True); outer.addWidget(card)
        layout=QVBoxLayout(card); layout.setContentsMargins(28,24,28,22); layout.setSpacing(11)
        top=QHBoxLayout(); top.setSpacing(12); icon=QLabel(); icon.setFixedSize(42,42); icon.setPixmap(QIcon("icon.png").pixmap(42,42)); top.addWidget(icon)
        words=QVBoxLayout(); words.setSpacing(1); title=QLabel("Amethyst"); title.setObjectName("title"); sub=QLabel("Starting up"); sub.setObjectName("small"); words.addWidget(title); words.addWidget(sub); top.addLayout(words); top.addStretch(); layout.addLayout(top)
        self.status=QLabel("Starting..."); self.status.setObjectName("status"); self.status.setWordWrap(True); layout.addWidget(self.status)
        row=QHBoxLayout(); row.setSpacing(10); self.bar=QProgressBar(); self.bar.setRange(0,1000); self.bar.setValue(0); self.bar.setTextVisible(False); row.addWidget(self.bar,1)
        self.percent=QLabel("0%"); self.percent.setObjectName("percent"); self.percent.setFixedWidth(38); self.percent.setAlignment(Qt.AlignRight|Qt.AlignVCenter); row.addWidget(self.percent); layout.addLayout(row)
        self.setStyleSheet(f"""#loadingScreen{{background:transparent}}#card{{background:#151519;border:1px solid {accent};border-radius:12px}}QLabel{{background:transparent;color:#f0f0f4;border:none}}#title{{font-size:23px;font-weight:700}}#small{{color:#777781;font-size:11px}}#status{{color:#b8b8c0;font-size:13px;padding-top:5px}}#percent{{color:#777781;font-size:11px}}QProgressBar{{background:#26262b;border:none;border-radius:3px;min-height:6px;max-height:6px}}QProgressBar::chunk{{background:{accent};border-radius:3px}}""")
        self.timer=QTimer(self); self.timer.setInterval(18); self.timer.timeout.connect(self.tick); self.timer.start()
    def tick(self):
        if self.value<self.target:
            step=max(1,int((self.target-self.value)*.025)); self.value=min(self.target,self.value+step); self.bar.setValue(self.value); self.percent.setText(f"{int(self.value/10)}%")
        if self.value>=1000 and self.done_callback:
            callback=self.done_callback; self.done_callback=None; self.timer.stop(); self.status.setText("Ready"); QApplication.processEvents(); QTimer.singleShot(250,lambda:self.finish_done(callback))
    def finish_done(self,callback):self.close(); callback()
    def set_status(self,text):self.status.setText(str(text)); QApplication.processEvents()
    def set_target(self,value):self.target=max(self.target,min(950,int(value)*10))
    def finish(self,callback):self.status.setText("Finishing startup..."); self.done_callback=callback; self.target=1000
    def finish_now(self):self.value=1000; self.target=1000; self.bar.setValue(1000); self.percent.setText("100%"); self.status.setText("Ready"); QApplication.processEvents(); self.close()

if __name__=="__main__":
    app=QApplication(sys.argv); app.setApplicationName("Amethyst"); app.setWindowIcon(QIcon("icon.png")); app.setQuitOnLastWindowClosed(False)
    splash=LoadingScreen(); splash.show(); splash.move(splash.screen().availableGeometry().center()-splash.rect().center()); app.processEvents(); holder={}
    def load_ui():
        try:
            splash.set_status("Reading local settings..."); splash.set_target(8)
            from config import load_settings,app_version,github_repo,data_dir
            settings=load_settings()
            deps=[("Pillow","PIL"),("requests","requests"),("keyring","keyring"),("Supabase","supabase"),("Windows media controls","winrt.windows.media.control")]
            for i,(name,module_name) in enumerate(deps):
                splash.set_status(f"Loading dependency: {name}..."); splash.set_target(12+i*5); importlib.import_module(module_name)
            splash.set_status("Checking update settings..."); splash.set_target(42)
            if settings.get("auto_update",False) and getattr(sys,"frozen",False):
                splash.set_status("Checking GitHub for updates..."); splash.set_target(44)
                try:
                    from updater import latest,download,install
                    release=latest(github_repo,app_version)
                    if release:
                        tag=release.get("_tag") or release.get("tag_name") or "new version"; splash.set_status(f"Update found: {tag}"); splash.set_target(49)
                        def update_progress(done,total):
                            if total:
                                pct=min(100,int(done*100/total)); splash.set_status(f"Downloading {tag}... {pct}%"); splash.set_target(49+int(pct*.20))
                            else:splash.set_status(f"Downloading {tag}...")
                            QApplication.processEvents()
                        file,_=download(release,data_dir/"updates",update_progress); splash.set_status(f"Installing {tag}..."); splash.set_target(72)
                        if install(file):
                            splash.set_status("Update ready. Restarting Amethyst..."); splash.set_target(95); QTimer.singleShot(350,app.quit); return
                    else:splash.set_status("No updates found."); splash.set_target(64)
                except Exception as e:
                    print("Auto update check failed:",e); splash.set_status("Couldn't check for updates. Continuing..."); splash.set_target(64)
            else:
                splash.set_status("Automatic updates disabled." if not settings.get("auto_update",False) else "Source build: skipping auto update."); splash.set_target(64)
            splash.set_status("Loading Amethyst interface..."); splash.set_target(70); module=importlib.import_module("ui")
            splash.set_status("Creating application services..."); splash.set_target(76); app.setApplicationName(module.brand); holder["player"]=module.SpotifyApp(splash)
        except Exception as e:
            print("Startup failed:",e); splash.set_status(f"Startup failed while loading: {type(e).__name__}. Check the console."); splash.set_target(100); QTimer.singleShot(1800,app.quit)
    QTimer.singleShot(80,load_ui); sys.exit(app.exec())