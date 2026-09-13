import json
import threading
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote
import keyring
import requests
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions
from PySide6.QtCore import Qt, Signal, QObject, QByteArray
from PySide6.QtGui import QPixmap, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QDialog, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QStackedWidget
from config import supabase_url, supabase_key, auth_callback, lastfm_function_url
service_name = "Amethyst"
credential_name = "supabase-session"
security_lock_value = "__AMETHYST_SECURITY_LOCK__"

class DeveloperAccessViolation(RuntimeError):
    pass

def get_value(obj, name, default=None):
    if obj is None: return default
    if isinstance(obj, dict): return obj.get(name, default)
    return getattr(obj, name, default)

class AuthSignals(QObject):
    callback = Signal(str)
    work_done = Signal(bool, object, object)

class CloudClient:
    def __init__(self):
        self.client = create_client(supabase_url, supabase_key, options=SyncClientOptions(auto_refresh_token=True, persist_session=False, flow_type="pkce"))
        self.profile = None; self.user = None; self.remember = False; self.last_heartbeat_total = 0

    def save_session(self, session=None):
        if not self.remember: return
        session = session or self.client.auth.get_session()
        if not session: return
        keyring.set_password(service_name, credential_name, json.dumps({"access_token": session.access_token, "refresh_token": session.refresh_token}))

    def clear_saved_session(self):
        try: keyring.delete_password(service_name, credential_name)
        except Exception: pass

    def restore_session(self):
        try:
            raw = keyring.get_password(service_name, credential_name)
            if not raw: return False
            data = json.loads(raw)
            result = self.client.auth.set_session(data["access_token"], data["refresh_token"])
            self.remember = True; self.user = result.user
            if self.mfa_required():
                self.clear_saved_session(); self.user = None; self.profile = None
                return False
            self.load_profile(); self.save_session(result.session)
            return bool(self.user and self.profile)
        except Exception:
            self.clear_saved_session(); return False

    def login(self, email, password, remember=False):
        result = self.client.auth.sign_in_with_password({"email": email, "password": password})
        self.remember = remember; self.user = result.user
        self.load_profile()
        if not self.mfa_required():
            if remember: self.save_session(result.session)
            else: self.clear_saved_session()
        return self.user

    def signup(self, email, password, username, display_name):
        return self.client.auth.sign_up({"email": email, "password": password, "options": {"data": {"username": username, "display_name": display_name}}})

    def send_magic_link(self, email):
        return self.client.auth.sign_in_with_otp({"email": email, "options": {"email_redirect_to": auth_callback, "should_create_user": False}})

    def send_signup_magic(self, email, username, display_name):
        return self.client.auth.sign_in_with_otp({"email": email, "options": {"email_redirect_to": auth_callback, "should_create_user": True, "data": {"username": username, "display_name": display_name}}})

    def exchange_code(self, code, remember=False):
        result = self.client.auth.exchange_code_for_session({"auth_code": code})
        self.remember = remember; self.user = result.user
        self.load_profile()
        if not self.mfa_required():
            if remember: self.save_session(result.session)
            else: self.clear_saved_session()
        return self.user

    def load_profile(self):
        if not self.user:
            try: self.user = self.client.auth.get_user().user
            except Exception: return None
        if not self.user: return None
        result = self.client.table("profiles").select("id,username,display_name,description,avatar_path,profile_showcase,created_at").eq("id", str(self.user.id)).limit(1).execute()
        self.profile = result.data[0] if result.data else None
        return self.profile

    def is_developer(self):
        try: return bool(self.client.rpc("is_developer").execute().data)
        except Exception: return False

    def update_profile(self, display_name, description, showcase):
        display_name=str(display_name or "").strip(); description=str(description or "").strip()
        if not display_name: raise ValueError("Display name can't be empty.")
        if len(display_name)>32: raise ValueError("Display name can only be 32 characters.")
        if len(description)>160: raise ValueError("Description can only be 160 characters.")
        result=self.client.rpc("update_my_profile", {"p_display_name":display_name,"p_description":description,"p_showcase":showcase or {}}).execute()
        self.load_profile(); return result

    def change_username(self, username, password):
        username=str(username or "").strip().lstrip("@")
        if len(username)<3 or len(username)>24: raise ValueError("Username must be 3 to 24 characters.")
        if not username.replace("_","").isalnum(): raise ValueError("Username can only use letters, numbers and underscores.")
        self.reauthenticate_password(password)
        result=self.client.rpc("change_my_username", {"p_username":username}).execute()
        self.load_profile(); return result

    def avatar_url(self, path=None):
        path=str(path if path is not None else (self.profile or {}).get("avatar_path","") or "").strip().lstrip("/")
        if not path:return ""
        return f"{supabase_url.rstrip('/')}/storage/v1/object/public/avatars/{path}"

    def upload_avatar(self, data):
        if not self.user: raise RuntimeError("Not signed in.")
        data=bytes(data or b"")
        if not data: raise ValueError("No image data was supplied.")
        if len(data)>20480: raise ValueError("Profile picture is too large after compression.")
        uid=str(get_value(self.user,"id","") or "")
        if not uid: raise RuntimeError("Couldn't find this account id.")
        old=str((self.profile or {}).get("avatar_path","") or "")
        path=f"{uid}/avatar.jpg"; bucket=self.client.storage.from_("avatars")
        try:
            bucket.upload(path=path,file=data,file_options={"content-type":"image/jpeg","cache-control":"86400","upsert":"true"})
            check=bucket.download(path)
            if not check:raise RuntimeError("Supabase accepted the upload but the file couldn't be read back.")
            self.client.rpc("set_my_avatar", {"p_path":path}).execute(); self.load_profile()
            if str((self.profile or {}).get("avatar_path","") or "")!=path:raise RuntimeError("The picture uploaded but the profile didn't save it.")
        except Exception:
            try:bucket.remove([path])
            except Exception:pass
            raise
        if old and old!=path:
            try:bucket.remove([old])
            except Exception:pass
        return self.avatar_url(path)

    def remove_avatar(self):
        old=str((self.profile or {}).get("avatar_path","") or "")
        self.client.rpc("set_my_avatar", {"p_path":""}).execute()
        if old:
            try:self.client.storage.from_("avatars").remove([old])
            except Exception:pass
        self.load_profile(); return True

    def record_play(self):
        if not self.user:return False
        result=self.client.rpc("record_play").execute(); return bool(result.data)

    def record_play_batch(self,count):
        if not self.user:return 0
        try:return int(self.client.rpc("record_play_batch",{"p_count":min(max(int(count),0),500)}).execute().data or 0)
        except Exception:return 0

    def leaderboard_page(self,query="",cursor=None,limit=50):
        query=str(query or "").strip(); limit=min(max(int(limit),1),50); cursor=cursor or {}
        args={"p_query":query,"p_limit":limit+1,"p_after_plays":cursor.get("plays"),"p_after_user_id":cursor.get("user_id")}
        data=self.client.rpc("leaderboard_page",args).execute().data or []; has_more=len(data)>limit; data=data[:limit]; rows=[]
        for x in data:
            rows.append({"user_id":x.get("user_id"),"total_plays":x.get("total_plays") or 0,"rank":x.get("rank"),"profiles":{"id":x.get("user_id"),"username":x.get("username"),"display_name":x.get("display_name"),"avatar_path":x.get("avatar_path") or ""}})
        next_cursor=None
        if data and has_more:
            last=data[-1]; next_cursor={"plays":int(last.get("total_plays") or 0),"user_id":last.get("user_id")}
        return rows,next_cursor,has_more

    def public_profile(self,user_id):
        data=self.client.rpc("public_profile",{"p_user_id":str(user_id)}).execute().data
        return data or {}

    def my_leaderboard_rank(self):
        data=self.client.rpc("my_leaderboard_rank").execute().data
        return data or {}

    def account_status(self):
        if not self.user: return {}
        try:
            data=self.client.rpc("account_status").execute().data
            return data or {}
        except Exception: return {}

    def heartbeat(self):
        if not self.user:return self.last_heartbeat_total
        try:
            value=int(self.client.rpc("app_heartbeat").execute().data or 0)
            if value>=0:self.last_heartbeat_total=value
            return self.last_heartbeat_total
        except Exception:return self.last_heartbeat_total

    def _access_token(self):
        session=self.client.auth.get_session()
        token=get_value(session,"access_token","")
        if not token:raise RuntimeError("Your Amethyst session expired. Sign in again.")
        return token

    def _lastfm(self, action, data=None, timeout=20):
        if not self.user:raise RuntimeError("Sign in to Amethyst first.")
        payload={"action":action}
        if data:payload.update(data)
        headers={
            "Authorization":f"Bearer {self._access_token()}",
            "apikey":supabase_key,
            "Content-Type":"application/json"
        }
        try:
            response=requests.post(lastfm_function_url,json=payload,headers=headers,timeout=timeout)
            body=response.json()
        except requests.RequestException as e:
            raise RuntimeError("Couldn't reach the Last.fm connection service.") from e
        except Exception as e:
            raise RuntimeError("The Last.fm connection service returned an invalid response.") from e
        if response.status_code>=400 or not body.get("ok",False):
            raise RuntimeError(str(body.get("error") or "Last.fm request failed."))
        return body

    def lastfm_status(self):
        try:return self._lastfm("status")
        except Exception:return {"ok":False,"connected":False}

    def start_lastfm_link(self):
        return self._lastfm("start")

    def lastfm_recent(self, after_ms):
        return self._lastfm("recent",{"after_ms":max(0,int(after_ms or 0))},timeout=35)

    def disconnect_lastfm(self):
        return self._lastfm("disconnect")

    def _developer_result(self, data):
        if isinstance(data, dict) and data.get("account_restricted"):
            raise DeveloperAccessViolation("Developer access required")
        if str(data or "") == security_lock_value:
            raise DeveloperAccessViolation("Developer access required")
        return data

    def developer_counts(self):
        try:
            data=self.client.rpc("developer_app_counts").execute().data
            return self._developer_result(data) or {}
        except DeveloperAccessViolation:
            raise
        except Exception:return {}

    def developer_lookup(self, username):
        data=self.client.rpc("developer_lookup_user", {"p_username": str(username or "").strip().lstrip("@")} ).execute().data
        return self._developer_result(data) or {}

    def developer_suspend(self, user_id, reason, seconds=None):
        data=self.client.rpc("developer_suspend_user", {"p_user_id": str(user_id), "p_reason": str(reason or "").strip(), "p_seconds": None if seconds is None else int(seconds)}).execute().data
        return self._developer_result(data)

    def developer_unsuspend(self, user_id):
        data=self.client.rpc("developer_unsuspend_user", {"p_user_id": str(user_id)}).execute().data
        return self._developer_result(data)

    def developer_queue_delete(self, user_id, reason):
        data=self.client.rpc("developer_queue_delete", {"p_user_id": str(user_id), "p_reason": str(reason or "").strip() or "Account removed by a developer."}).execute().data
        return self._developer_result(data)

    def finish_developer_delete(self):
        result=self.client.rpc("acknowledge_developer_delete").execute()
        self.clear_saved_session(); self.user=None; self.profile=None
        return result

    def mfa_status(self):
        try:
            r = self.client.auth.mfa.get_authenticator_assurance_level()
            current = get_value(r, "current_level", get_value(r, "currentLevel", "aal1"))
            next_level = get_value(r, "next_level", get_value(r, "nextLevel", "aal1"))
            return str(current or "aal1"), str(next_level or "aal1")
        except Exception: return "aal1", "aal1"

    def mfa_required(self):
        current, next_level = self.mfa_status()
        return current != "aal2" and next_level == "aal2"

    def totp_factors(self, verified_only=True):
        try:
            r = self.client.auth.mfa.list_factors()
            factors = get_value(r, "totp", []) or []
            if verified_only: factors = [x for x in factors if str(get_value(x, "status", "verified")).lower() == "verified"]
            return factors
        except Exception: return []

    def has_mfa(self):
        return bool(self.totp_factors(True))

    def verify_mfa(self, code, factor_id=None):
        factors = self.totp_factors(True)
        if not factor_id and factors: factor_id = get_value(factors[0], "id")
        if not factor_id: raise RuntimeError("No authenticator factor was found on this account.")
        result = self.client.auth.mfa.challenge_and_verify({"factor_id": factor_id, "code": code.strip()})
        self.user = self.client.auth.get_user().user
        self.load_profile()
        if self.remember: self.save_session()
        else: self.clear_saved_session()
        return result

    def begin_mfa_setup(self):
        result = self.client.auth.mfa.enroll({"factor_type": "totp", "friendly_name": "Google Authenticator"})
        totp = get_value(result, "totp", {})
        return {"factor_id": get_value(result, "id", ""), "qr_code": get_value(totp, "qr_code", ""), "secret": get_value(totp, "secret", ""), "uri": get_value(totp, "uri", "")}

    def finish_mfa_setup(self, factor_id, code):
        result = self.client.auth.mfa.challenge_and_verify({"factor_id": factor_id, "code": code.strip()})
        if self.remember: self.save_session()
        return result

    def cancel_mfa_setup(self, factor_id):
        if not factor_id: return
        try: self.client.auth.mfa.unenroll({"factor_id": factor_id})
        except Exception: pass

    def disable_mfa(self, code):
        factors = self.totp_factors(True)
        if not factors: return False
        factor_id = get_value(factors[0], "id", "")
        self.verify_mfa(code, factor_id)
        self.client.auth.mfa.unenroll({"factor_id": factor_id})
        return True

    def user_email(self):
        return str(get_value(self.user, "email", "") or "")

    def update_password(self, password):
        password = str(password or "")
        if len(password) < 8: raise ValueError("Password must be at least 8 characters.")
        result = self.client.auth.update_user({"password": password})
        try: self.user = result.user or self.client.auth.get_user().user
        except Exception: pass
        if self.remember: self.save_session()
        return result

    def reauthenticate_password(self, password):
        email=self.user_email(); password=str(password or "")
        if not email: raise RuntimeError("Couldn't find the email on this account.")
        if not password: raise RuntimeError("Enter your password.")
        old_id=str(get_value(self.user,"id","") or "")
        result=self.client.auth.sign_in_with_password({"email":email,"password":password})
        user=get_value(result,"user")
        if not user or (old_id and str(get_value(user,"id","") or "")!=old_id): raise RuntimeError("That password didn't verify this account.")
        self.user=user; self.load_profile()
        if self.remember and not self.mfa_required(): self.save_session(get_value(result,"session"))
        return result

    def delete_account(self):
        result = self.client.rpc("delete_my_account").execute()
        self.clear_saved_session(); self.user = None; self.profile = None
        return result

    def logout(self):
        self.clear_saved_session()
        try: self.client.auth.sign_out()
        except Exception: pass
        self.user = None; self.profile = None

class CallbackServer:
    def __init__(self, signals): self.signals = signals; self.server = None
    def start(self):
        if self.server: return
        signals = self.signals; owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed=urlparse(self.path)
                if parsed.path!="/auth/callback":self.send_error(404); return
                code=(parse_qs(parsed.query).get("code") or [""])[0]
                body=b"<html><body style='font-family:Segoe UI;background:#111;color:#eee;padding:40px'><h2>You can go back to Amethyst now.</h2></body></html>"
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
                if code:signals.callback.emit(code)
                def stop():
                    try: owner.server.shutdown(); owner.server.server_close()
                    except Exception: pass
                    owner.server = None
                threading.Thread(target=stop, daemon=True).start()
            def log_message(self, format, *args): pass
        try:
            self.server = HTTPServer(("127.0.0.1", 8765), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        except OSError: self.server = None

class MFAChallengeDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud = cloud; self.accent = accent; self.ok = False
        self.setWindowTitle("Two-factor authentication"); self.setFixedSize(410, 270)
        layout = QVBoxLayout(self); layout.setContentsMargins(26, 24, 26, 24); layout.setSpacing(11)
        title = QLabel("Two-factor authentication"); title.setObjectName("title")
        desc = QLabel("Open Google Authenticator and enter the 6-digit code."); desc.setWordWrap(True); desc.setObjectName("muted")
        self.code = QLineEdit(); self.code.setPlaceholderText("6-digit code"); self.code.setMaxLength(6); self.code.returnPressed.connect(self.verify)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        buttons = QHBoxLayout(); cancel = QPushButton("Cancel"); verify = QPushButton("Verify"); verify.setObjectName("primary")
        cancel.clicked.connect(self.reject); verify.clicked.connect(self.verify); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(verify)
        layout.addWidget(title); layout.addWidget(desc); layout.addSpacing(4); layout.addWidget(self.code); layout.addWidget(self.status); layout.addStretch(); layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(accent)); self.code.setFocus()
    def verify(self):
        code = self.code.text().strip()
        if len(code) != 6 or not code.isdigit(): self.status.setText("Enter the 6-digit code from your authenticator app."); return
        self.status.setText("Checking code...")
        try:
            self.cloud.verify_mfa(code); self.ok = True; self.accept()
        except Exception: self.status.setText("That code didn't work. Check the code and try again."); self.code.selectAll(); self.code.setFocus()

class MFASetupDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud = cloud; self.accent = accent; self.factor_id = ""; self.enabled = False
        self.setWindowTitle("Set up 2FA"); self.setMinimumSize(650, 670); self.resize(680, 700)
        layout = QVBoxLayout(self); layout.setContentsMargins(26, 22, 26, 22); layout.setSpacing(10)
        title = QLabel("Set up two-factor authentication"); title.setObjectName("title")
        intro = QLabel("This uses a time-based 6-digit code from Google Authenticator. After you enable it, you'll need a current code after logging in with your email and password."); intro.setWordWrap(True); intro.setObjectName("muted")
        guide = QLabel("1. Install or open Google Authenticator on your phone.\n2. Tap the + button, then choose Scan a QR code.\n3. Scan the QR code below. If scanning doesn't work, choose Enter a setup key and use the secret shown below.\n4. Google Authenticator will add an entry for this app and show a 6-digit code that changes about every 30 seconds.\n5. Enter that code at the bottom of this window and press Enable 2FA.\n\nDon't remove the authenticator entry after enabling it. This app doesn't have recovery codes yet, so losing access to the authenticator could lock you out until the account is recovered manually.")
        guide.setWordWrap(True); guide.setObjectName("guide")
        self.qr = QLabel("Creating QR code..."); self.qr.setAlignment(Qt.AlignCenter); self.qr.setFixedSize(220, 220); self.qr.setObjectName("qr")
        self.secret = QLineEdit(); self.secret.setReadOnly(True); self.secret.setPlaceholderText("Setup secret")
        self.code = QLineEdit(); self.code.setPlaceholderText("6-digit code from Google Authenticator"); self.code.setMaxLength(6)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        buttons = QHBoxLayout(); cancel = QPushButton("Cancel"); enable = QPushButton("Enable 2FA"); enable.setObjectName("primary")
        cancel.clicked.connect(self.cancel); enable.clicked.connect(self.verify); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(enable)
        layout.addWidget(title); layout.addWidget(intro); layout.addWidget(guide); layout.addWidget(self.qr, 0, Qt.AlignHCenter); layout.addWidget(QLabel("Manual setup key")); layout.addWidget(self.secret); layout.addWidget(self.code); layout.addWidget(self.status); layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(accent))
        try:
            data = self.cloud.begin_mfa_setup(); self.factor_id = data.get("factor_id", ""); self.secret.setText(data.get("secret", "")); self.show_qr(data.get("qr_code", ""))
        except Exception as e: self.status.setText("Couldn't start 2FA setup: " + str(e)); enable.setEnabled(False)

    def show_qr(self, raw):
        if not raw: self.qr.setText("QR code unavailable\nUse the setup key below"); return
        try:
            svg = raw
            if raw.startswith("data:image/svg+xml"):
                payload = raw.split(",", 1)[1]
                if ";base64" in raw.split(",", 1)[0]: svg = base64.b64decode(payload)
                else: svg = unquote(payload).encode("utf-8")
            elif isinstance(raw, str): svg = raw.encode("utf-8")
            renderer = QSvgRenderer(QByteArray(svg))
            if not renderer.isValid(): raise ValueError("invalid qr")
            image = QImage(210, 210, QImage.Format_ARGB32); image.fill(Qt.white)
            painter = QPainter(image); renderer.render(painter); painter.end()
            self.qr.setPixmap(QPixmap.fromImage(image))
        except Exception: self.qr.setText("QR code couldn't be drawn\nUse the setup key below")

    def verify(self):
        code = self.code.text().strip()
        if len(code) != 6 or not code.isdigit(): self.status.setText("Enter the current 6-digit code from Google Authenticator."); return
        self.status.setText("Checking code...")
        try:
            self.cloud.finish_mfa_setup(self.factor_id, code); self.enabled = True; self.accept()
        except Exception: self.status.setText("That code didn't work. Wait for a fresh code and try again."); self.code.selectAll(); self.code.setFocus()

    def cancel(self):
        self.reject()

    def reject(self):
        if not self.enabled: self.cloud.cancel_mfa_setup(self.factor_id)
        super().reject()


class MFADisableDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud = cloud; self.accent = accent; self.disabled = False
        self.setWindowTitle("Disable 2FA"); self.setFixedSize(430, 300)
        layout = QVBoxLayout(self); layout.setContentsMargins(26, 24, 26, 24); layout.setSpacing(11)
        title = QLabel("Disable two-factor authentication?"); title.setObjectName("title")
        desc = QLabel("Enter 2FA code."); desc.setWordWrap(True); desc.setObjectName("muted")
        self.code = QLineEdit(); self.code.setPlaceholderText("6-digit code"); self.code.setMaxLength(6); self.code.returnPressed.connect(self.disable)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        buttons = QHBoxLayout(); cancel = QPushButton("Cancel"); disable = QPushButton("Disable 2FA"); disable.setObjectName("danger")
        cancel.clicked.connect(self.reject); disable.clicked.connect(self.disable); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(disable)
        layout.addWidget(title); layout.addWidget(desc); layout.addWidget(self.code); layout.addWidget(self.status); layout.addStretch(); layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(accent)); self.code.setFocus()

    def disable(self):
        code = self.code.text().strip()
        if len(code) != 6 or not code.isdigit(): self.status.setText("Enter the current 6-digit code from Google Authenticator."); return
        self.status.setText("Checking code...")
        try:
            self.cloud.disable_mfa(code); self.disabled = True; self.accept()
        except Exception:
            self.status.setText("That code didn't work. Wait for a fresh code and try again."); self.code.selectAll(); self.code.setFocus()

class ChangePasswordDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud=cloud; self.accent=accent; self.changed=False
        self.setWindowTitle("Change password"); self.setFixedSize(450, 360)
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,24); layout.setSpacing(11)
        title=QLabel("Change your password"); title.setObjectName("title")
        desc=QLabel("Enter a new password for this account. You stay signed in after changing it."); desc.setObjectName("muted"); desc.setWordWrap(True)
        self.password=QLineEdit(); self.password.setPlaceholderText("New password"); self.password.setEchoMode(QLineEdit.Password)
        self.confirm=QLineEdit(); self.confirm.setPlaceholderText("Confirm new password"); self.confirm.setEchoMode(QLineEdit.Password); self.confirm.returnPressed.connect(self.change)
        self.status=QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        buttons=QHBoxLayout(); cancel=QPushButton("Cancel"); change=QPushButton("Change password"); change.setObjectName("primary")
        cancel.clicked.connect(self.reject); change.clicked.connect(self.change); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(change)
        layout.addWidget(title); layout.addWidget(desc); layout.addSpacing(4); layout.addWidget(self.password); layout.addWidget(self.confirm); layout.addWidget(self.status); layout.addStretch(); layout.addLayout(buttons)
        self.setStyleSheet(dialog_style(accent)); self.password.setFocus()

    def change(self):
        password=self.password.text(); confirm=self.confirm.text()
        if len(password)<8: self.status.setText("Use at least 8 characters."); return
        if password!=confirm: self.status.setText("The passwords don't match."); return
        self.status.setText("Changing password..."); QApplication.processEvents()
        try:
            self.cloud.update_password(password); self.changed=True; self.accept()
        except Exception as e:
            text=str(e)
            if "nonce" in text.lower() or "reauth" in text.lower(): text="Supabase wants you to re-verify before changing this password. Sign out and back in, then try again."
            self.status.setText(text)

class DeleteAccountDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud=cloud; self.accent=accent; self.deleted=False; self.has_mfa=cloud.has_mfa(); self.signals=AuthSignals(); self.signals.work_done.connect(self.finish_work)
        self.setWindowTitle("Delete account"); self.setFixedSize(520,500 if self.has_mfa else 450)
        layout=QVBoxLayout(self); layout.setContentsMargins(28,25,28,25); layout.setSpacing(11)
        title=QLabel("Delete your account"); title.setObjectName("title")
        warning=QLabel("This permanently deletes your online account, profile and leaderboard entry. Your local listening stats on this PC are not deleted."); warning.setWordWrap(True); warning.setObjectName("muted")
        username=(cloud.profile or {}).get("username",""); confirm_text=QLabel(f"Re-enter your username and password to confirm deletion of @{username}."); confirm_text.setWordWrap(True); confirm_text.setObjectName("muted")
        self.confirm=QLineEdit(); self.confirm.setPlaceholderText("@"+username)
        self.password=QLineEdit(); self.password.setPlaceholderText("Password"); self.password.setEchoMode(QLineEdit.Password)
        self.status=QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        layout.addWidget(title); layout.addWidget(warning); layout.addSpacing(4); layout.addWidget(confirm_text); layout.addWidget(self.confirm); layout.addWidget(self.password)
        if self.has_mfa:
            info=QLabel("2FA is enabled on this account, so you'll also need the current 6-digit code from Google Authenticator."); info.setWordWrap(True); info.setObjectName("muted")
            self.code=QLineEdit(); self.code.setPlaceholderText("6-digit Google Authenticator code"); self.code.setMaxLength(6); layout.addWidget(info); layout.addWidget(self.code)
        else:
            self.code=None
            info=QLabel("2FA isn't enabled, so the account will be deleted as soon as your username and password are verified."); info.setWordWrap(True); info.setObjectName("muted"); layout.addWidget(info)
        layout.addWidget(self.status); layout.addStretch(); buttons=QHBoxLayout(); cancel=QPushButton("Cancel"); delete=QPushButton("Permanently delete account"); delete.setObjectName("danger")
        cancel.clicked.connect(self.reject); delete.clicked.connect(self.delete_account); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(delete); layout.addLayout(buttons); self.setStyleSheet(dialog_style(accent)); self.confirm.setFocus()

    def confirmed(self):
        username=(self.cloud.profile or {}).get("username",""); typed=self.confirm.text().strip().lstrip("@")
        if typed.lower()!=username.lower(): self.status.setText("Type your username exactly before deleting the account."); return False
        if not self.password.text(): self.status.setText("Enter your account password."); return False
        if self.has_mfa:
            code=self.code.text().strip()
            if len(code)!=6 or not code.isdigit(): self.status.setText("Enter the current 6-digit Google Authenticator code."); return False
        return True

    def run(self,fn,success):
        self.status.setText("Verifying account...")
        def worker():
            try:self.signals.work_done.emit(True,fn(),success)
            except Exception as e:self.signals.work_done.emit(False,str(e),success)
        threading.Thread(target=worker,daemon=True).start()

    def finish_work(self,ok,result,success):
        if ok:success(result)
        else:
            text=str(result)
            low=text.lower()
            if "invalid login" in low or "invalid credentials" in low or "email or password" in low: text="That password is incorrect."
            elif "mfa" in low or "factor" in low or "totp" in low: text="That authenticator code didn't work. Wait for a fresh code and try again."
            self.status.setText(text)

    def delete_account(self):
        if not self.confirmed(): return
        password=self.password.text(); code=self.code.text().strip() if self.has_mfa else ""
        def work():
            self.cloud.reauthenticate_password(password)
            if self.has_mfa: self.cloud.verify_mfa(code)
            return self.cloud.delete_account()
        self.run(work,self.deleted_ok)

    def deleted_ok(self,result=None): self.deleted=True; self.accept()

class SuspensionDialog(QDialog):
    def __init__(self, cloud, status, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud=cloud; self.status_data=status or {}; self.allowed=False
        permanent=bool(self.status_data.get("permanent"))
        self.setWindowTitle("Account suspended"); self.setFixedSize(500,270 if permanent else 310); self.setWindowFlags(Qt.Dialog|Qt.FramelessWindowHint)
        layout=QVBoxLayout(self); layout.setContentsMargins(28,26,28,24); layout.setSpacing(14)
        title=QLabel("Your account has been permanently suspended" if permanent else "Your account has been suspended"); title.setObjectName("title"); title.setWordWrap(True)
        reason=QLabel(str(self.status_data.get("reason") or "No reason provided.")); reason.setObjectName("muted"); reason.setWordWrap(True)
        exit_btn=QPushButton("Exit"); exit_btn.clicked.connect(lambda: QApplication.instance().quit())
        layout.addWidget(title); layout.addWidget(reason)
        self.remaining=None; self.timer=None
        if not permanent:
            self.remaining=QLabel(); self.remaining.setStyleSheet(f"color:{accent};font-size:22px;font-weight:800;"); layout.addWidget(self.remaining)
            self.timer=__import__('PySide6.QtCore',fromlist=['QTimer']).QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(1000); self.tick()
        layout.addStretch(); layout.addWidget(exit_btn,0,Qt.AlignRight); self.setStyleSheet(dialog_style(accent))

    def tick(self):
        if self.status_data.get("permanent") or self.remaining is None:return
        raw=self.status_data.get("suspended_until")
        try:
            end=datetime.fromisoformat(str(raw).replace("Z","+00:00")); now=datetime.now(timezone.utc); seconds=max(0,int((end-now).total_seconds()))
        except Exception: seconds=0
        h,rem=divmod(seconds,3600); m,sec=divmod(rem,60); self.remaining.setText(f"{h:02d}:{m:02d}:{sec:02d}")
        if seconds<=0:
            try:new=self.cloud.account_status()
            except Exception:return
            if not new.get("suspended"):
                self.allowed=True; self.timer.stop(); self.accept()
            else:self.status_data=new

class DeveloperDeleteNoticeDialog(QDialog):
    def __init__(self, reason, accent="#b64fff", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Account deleted"); self.setFixedSize(500,260); self.setWindowFlags(Qt.Dialog|Qt.FramelessWindowHint)
        layout=QVBoxLayout(self); layout.setContentsMargins(28,26,28,24); layout.setSpacing(14)
        title=QLabel("Your account has been deleted"); title.setObjectName("title")
        reason_label=QLabel(str(reason or "No reason provided.")); reason_label.setObjectName("muted"); reason_label.setWordWrap(True)
        exit_btn=QPushButton("Exit"); exit_btn.clicked.connect(lambda: QApplication.instance().quit())
        layout.addWidget(title); layout.addWidget(reason_label); layout.addStretch(); layout.addWidget(exit_btn,0,Qt.AlignRight); self.setStyleSheet(dialog_style(accent))

def dialog_style(accent):
    return f"""
        QDialog {{ background:#111114; color:#eeeeF2; font-family:'Segoe UI'; }}
        QWidget {{ color:#eeeeF2; font-family:'Segoe UI'; }}
        QLabel#title {{ font-size:21px; font-weight:800; }} #muted {{ color:#a7a7b1; }} #guide {{ color:#d1d1d8; line-height:1.35; }}
        #qr {{ background:white; border:1px solid #34343b; border-radius:10px; color:#111; }}
        QLineEdit {{ background:#0c0c0f; border:1px solid #303038; border-radius:9px; padding:10px; color:#eeeeF2; selection-background-color:{accent}; selection-color:#090a0d; }}
        QLineEdit:focus {{ border:1px solid {accent}; }}
        QPushButton {{ background:#202025; border:1px solid #303038; border-radius:9px; padding:9px 13px; color:#eeeeF2; }} QPushButton:hover {{ background:#29292f; }}
        QPushButton#primary {{ background:{accent}; border:none; color:#0b0b0e; font-weight:800; }}
        QPushButton#danger {{ background:#5b2025; border:1px solid #7f2d34; color:#ffdadd; font-weight:750; }} QPushButton#danger:hover {{ background:#742a31; border-color:#a43c45; }}
    """

class AuthDialog(QDialog):
    def __init__(self, cloud, accent="#b64fff", parent=None):
        super().__init__(parent); self.cloud = cloud; self.accent = accent; self.logged_in = False; self.guest = False
        self.signals = AuthSignals(); self.server = CallbackServer(self.signals)
        self.signals.callback.connect(self.magic_callback); self.signals.work_done.connect(self.finish_work)
        self.setWindowTitle("Sign in"); self.setFixedSize(470, 600); self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        outer = QVBoxLayout(self); outer.setContentsMargins(14, 14, 14, 14)
        card = QFrame(); card.setObjectName("card"); outer.addWidget(card)
        layout = QVBoxLayout(card); layout.setContentsMargins(28, 26, 28, 26); layout.setSpacing(12)
        title = QLabel("Amethyst"); title.setObjectName("authTitle")
        sub = QLabel("Sign in to access your stats, profile and leaderboard."); sub.setObjectName("muted"); sub.setWordWrap(True)
        layout.addWidget(title); layout.addWidget(sub)
        tabs = QHBoxLayout(); self.login_tab = QPushButton("Log in"); self.signup_tab = QPushButton("Sign up")
        self.login_tab.setObjectName("authTab"); self.signup_tab.setObjectName("authTab"); self.login_tab.setCheckable(True); self.signup_tab.setCheckable(True)
        self.login_tab.clicked.connect(lambda: self.switch_page(0)); self.signup_tab.clicked.connect(lambda: self.switch_page(1)); tabs.addWidget(self.login_tab); tabs.addWidget(self.signup_tab); layout.addLayout(tabs)
        self.pages = QStackedWidget(); self.pages.addWidget(self.login_page()); self.pages.addWidget(self.signup_page()); layout.addWidget(self.pages, 1); self.switch_page(0)
        self.status = QLabel(""); self.status.setObjectName("status"); self.status.setWordWrap(True); layout.addWidget(self.status)
        guest = QPushButton("Continue as guest"); guest.clicked.connect(self.use_guest); layout.addWidget(guest)
        close = QPushButton("Exit"); close.clicked.connect(self.reject); layout.addWidget(close); self.setStyleSheet(self.style())

    def use_guest(self):
        self.guest = True
        self.accept()

    def switch_page(self, index):
        self.pages.setCurrentIndex(index); self.login_tab.setChecked(index == 0); self.signup_tab.setChecked(index == 1)
    def field(self, placeholder, password=False):
        box = QLineEdit(); box.setPlaceholderText(placeholder)
        if password: box.setEchoMode(QLineEdit.Password)
        return box
    def login_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(10)
        self.login_email = self.field("Email"); self.login_password = self.field("Password", True); self.login_remember = QCheckBox("Remember me on this PC"); self.login_remember.setObjectName("rememberBox")
        login = QPushButton("Log in"); login.setObjectName("primary"); login.clicked.connect(self.do_login)
        magic = QPushButton("Email me a sign-in link"); magic.clicked.connect(self.do_magic)
        layout.addWidget(self.login_email); layout.addWidget(self.login_password); layout.addWidget(self.login_remember); layout.addWidget(login); layout.addWidget(magic); layout.addStretch(); return page
    def signup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(10)
        self.signup_email = self.field("Email"); self.signup_username = self.field("Username"); self.signup_display = self.field("Display name"); self.signup_password = self.field("Password", True)
        self.signup_remember = QCheckBox("Remember me on this PC"); self.signup_remember.setObjectName("rememberBox")
        signup = QPushButton("Create account"); signup.setObjectName("primary"); signup.clicked.connect(self.do_signup)
        magic = QPushButton("Create account with email link"); magic.clicked.connect(self.do_signup_magic)
        for w in (self.signup_email, self.signup_username, self.signup_display, self.signup_password, self.signup_remember, signup, magic): layout.addWidget(w)
        note = QLabel("We'll send a verification email first. Your app profile and leaderboard account are created only after the email is verified."); note.setObjectName("muted"); note.setWordWrap(True); layout.addWidget(note); layout.addStretch(); return page

    def run(self, fn, success):
        self.status.setText("Working...")
        def worker():
            try: self.signals.work_done.emit(True, fn(), success)
            except Exception as e: self.signals.work_done.emit(False, str(e), success)
        threading.Thread(target=worker, daemon=True).start()
    def finish_work(self, ok, result, success):
        if ok: success(result)
        else: self.status.setText(str(result))
    def do_login(self):
        email = self.login_email.text().strip(); password = self.login_password.text()
        if not email or not password: self.status.setText("Enter your email and password."); return
        self.run(lambda: self.cloud.login(email, password, self.login_remember.isChecked()), self.finish_login)
    def do_signup(self):
        email = self.signup_email.text().strip(); username = self.signup_username.text().strip(); display = self.signup_display.text().strip() or username; password = self.signup_password.text()
        if not email or not username or not password: self.status.setText("Email, username and password are required."); return
        if len(username) < 3 or len(username) > 24: self.status.setText("Username must be 3 to 24 characters."); return
        self.run(lambda: self.cloud.signup(email, password, username, display), self.signup_finished)
    def signup_finished(self, result):
        self.login_email.setText(self.signup_email.text().strip()); self.login_remember.setChecked(self.signup_remember.isChecked()); self.switch_page(0)
        self.status.setText("Verification email sent. Verify the email first, then come back here and log in. Your profile isn't created until verification is complete.")
    def do_signup_magic(self):
        email = self.signup_email.text().strip(); username = self.signup_username.text().strip(); display = self.signup_display.text().strip() or username
        if not email or not username: self.status.setText("Email and username are required."); return
        if len(username) < 3 or len(username) > 24: self.status.setText("Username must be 3 to 24 characters."); return
        self.login_remember.setChecked(self.signup_remember.isChecked()); self.server.start()
        if not self.server.server: self.status.setText("Port 8765 is already in use, so the email-link callback couldn't start."); return
        self.run(lambda: self.cloud.send_signup_magic(email, username, display), lambda result: self.status.setText("Verification link sent. Open it on this PC. Your profile is created after the link verifies your email."))
    def do_magic(self):
        email = self.login_email.text().strip()
        if not email: self.status.setText("Enter your email first."); return
        self.server.start()
        if not self.server.server: self.status.setText("Port 8765 is already in use, so the sign-in link callback couldn't start."); return
        self.run(lambda: self.cloud.send_magic_link(email), lambda result: self.status.setText("Link sent. Open the email on this PC and keep this window open."))
    def magic_callback(self, code):
        self.status.setText("Finishing sign in..."); self.run(lambda: self.cloud.exchange_code(code, self.login_remember.isChecked()), self.finish_login)
    def finish_login(self, user):
        if not user: self.status.setText("Couldn't sign in."); return
        if self.cloud.mfa_required():
            d = MFAChallengeDialog(self.cloud, self.accent, self)
            if d.exec() != QDialog.Accepted: self.status.setText("Two-factor authentication is required for this account."); return
        self.cloud.load_profile()
        if not self.cloud.profile:
            self.status.setText("Your email is verified, but your profile wasn't created. Try logging in again in a moment."); return
        self.logged_in = True; self.accept()
    def style(self):
        return f"""
            QDialog {{ background:#0b0b0e; font-family:'Segoe UI'; }} QWidget {{ color:#eeeeF2; font-family:'Segoe UI'; }}
            #card {{ background:#121215; border:1px solid #29292f; border-radius:16px; }} QLabel#authTitle {{ color:#f4f4f7; background:transparent; font-size:22px; font-weight:800; }}
            #muted {{ color:#a7a7b1; }} #status {{ color:#c9c9d1; min-height:34px; }}
            QLineEdit {{ background:#0c0c0f; border:1px solid #303038; border-radius:9px; padding:11px; color:#eeeeF2; selection-background-color:{self.accent}; selection-color:#090a0d; }} QLineEdit:focus {{ border:1px solid {self.accent}; }} QLineEdit::placeholder {{ color:#777781; }}
            QPushButton {{ background:#202025; border:1px solid #303038; border-radius:9px; padding:10px 13px; color:#eeeeF2; }} QPushButton:hover {{ background:#29292f; border-color:#46464f; }}
            QPushButton#authTab {{ background:#1d1d22; color:#b9b9c3; border:1px solid #303038; font-weight:600; }} QPushButton#authTab:hover {{ color:#eeeeF2; border-color:#484852; }} QPushButton#authTab:checked {{ background:{self.accent}; color:#0b0b0e; border:1px solid {self.accent}; font-weight:800; }}
            #primary {{ background:{self.accent}; border:none; color:#0b0b0e; font-weight:800; }}
            QCheckBox#rememberBox {{ color:#c7c7d0; spacing:9px; background:transparent; }} QCheckBox#rememberBox::indicator {{ width:16px; height:16px; border:1px solid #666672; border-radius:4px; background:#202026; }} QCheckBox#rememberBox::indicator:hover {{ border:1px solid {self.accent}; background:#27272e; }} QCheckBox#rememberBox::indicator:checked {{ background:{self.accent}; border:1px solid {self.accent}; }}
        """