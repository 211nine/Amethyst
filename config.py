import os
import json
from pathlib import Path
app_version = "0.11.20"
github_repo = "211nine/Amethyst"
local_appdata = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
data_dir = Path(local_appdata) / "Amethyst"
data_dir.mkdir(parents=True, exist_ok=True)
settings_file = data_dir / "settings.json"
db_file = data_dir / "stats.db"
image_cache_dir = data_dir / "image_cache"
image_cache_dir.mkdir(parents=True, exist_ok=True)
supabase_url = "https://vsurkfqmqxwtbexromaj.supabase.co"
supabase_key = "sb_publishable_czz9X62fH8G287HOSPYEew_-Z24buzh"
auth_callback = "http://127.0.0.1:8765/auth/callback"
lastfm_function_url = f"{supabase_url.rstrip('/')}/functions/v1/lastfm"
lastfm_setup_url = "https://www.last.fm/settings/applications"
default_settings = {
    "theme_colour": "#b64fff",
    "overlay_position": "Bottom centre",
    "custom_x": 100,
    "custom_y": 100,
    "overlay_opacity": 0.96,
    "overlay_size_mode": "Default",
    "overlay_scale": 100,
    "custom_width": 430,
    "custom_height": 310,
    "overlay_colour_mode": "Theme",
    "overlay_colour": "#4f9cff",
    "show_art": True,
    "show_progress": True,
    "show_times": True,
    "show_shuffle": True,
    "show_repeat": True,
    "show_volume": False,
    "overlay_hotkey": "F1",
    "auto_update": True
}

def load_settings():
    if not settings_file.exists():
        save_settings(default_settings.copy())
        return default_settings.copy()

    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    out = default_settings.copy()
    out.update(data)

    if out.get("overlay_colour_mode") == "Auto":
        out["overlay_colour_mode"] = "Album artwork"

    if "overlay_size_mode" not in data:
        old_scale = int(out.get("overlay_scale", 100))
        if old_scale <= 90:
            out["overlay_size_mode"] = "Compact"
        elif old_scale >= 115:
            out["overlay_size_mode"] = "Large"
        else:
            out["overlay_size_mode"] = "Default"

    return out

# settings.json breaking itself randomly is so tuff boii 🥶
def save_settings(data):
    settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")