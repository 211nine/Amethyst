import os,sys,re,hashlib,subprocess
from pathlib import Path
from urllib.parse import urlparse
import requests
api="https://api.github.com"
headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2026-03-10","User-Agent":"Amethyst-Updater"}

def _version(text):
    s=str(text or "").strip().lower().lstrip("v"); nums=[int(x) for x in re.findall(r"\d+",s)[:4]]
    while len(nums)<4:nums.append(0)
    return tuple(nums)+(0 if any(x in s for x in ("dev","alpha","beta","rc")) else 1,)

def latest(repo,current):
    if not repo or "/" not in repo:return None
    r=requests.get(f"{api}/repos/{repo}/releases/latest",headers=headers,timeout=(5,10)); r.raise_for_status(); data=r.json(); tag=str(data.get("tag_name") or data.get("name") or "")
    if not tag or _version(tag)<=_version(current):return None
    data["_tag"]=tag; data["_repo"]=repo; return data

def pick_asset(release):
    assets=[x for x in (release.get("assets") or []) if str(x.get("state") or "uploaded")=="uploaded"]
    found=[x for x in assets if str(x.get("name") or "").lower().endswith(".exe") and "amethyst" in str(x.get("name") or "").lower()]
    return max(found,key=lambda x:int(x.get("size") or 0)) if found else None

def download(release,folder,progress=None):
    asset=pick_asset(release)
    if not asset:raise RuntimeError("The latest release has no Amethyst .exe asset to install.")
    url=str(asset.get("browser_download_url") or ""); repo=str(release.get("_repo") or "")
    parsed=urlparse(url)
    if parsed.scheme!="https" or parsed.hostname!="github.com" or (repo and f"/{repo}/releases/download/" not in parsed.path):raise RuntimeError("The update asset URL is invalid.")
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True); out=folder/Path(str(asset.get("name") or "Amethyst.exe")).name
    h=hashlib.sha256(); done=0; total=int(asset.get("size") or 0)
    with requests.get(url,stream=True,headers={"User-Agent":"Amethyst-Updater"},timeout=(5,90)) as r:
        r.raise_for_status()
        if not total:total=int(r.headers.get("content-length") or 0)
        with open(out,"wb") as f:
            for chunk in r.iter_content(262144):
                if not chunk:continue
                f.write(chunk); h.update(chunk); done+=len(chunk)
                if progress:progress(done,total)
    if total and done!=total:
        try:out.unlink()
        except Exception:pass
        raise RuntimeError("The downloaded update was incomplete.")
    digest=str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:") and h.hexdigest().lower()!=digest.split(":",1)[1].strip().lower():
        try:out.unlink()
        except Exception:pass
        raise RuntimeError("The downloaded update failed its SHA256 check.")
    return out,asset

def _ps(value):return "'"+str(value).replace("'","''")+"'"

def install(downloaded):
    if not getattr(sys,"frozen",False):raise RuntimeError("Automatic updates only install in the packaged app.")
    downloaded=Path(downloaded).resolve(); target=Path(sys.executable).resolve(); update_root=downloaded.parent; pid=os.getpid()
    if downloaded.suffix.lower()!=".exe":raise RuntimeError("Unsupported update file.")
    script=update_root/"finish-update.ps1"
    body=f'''$ErrorActionPreference='Stop'\ntry {{ Wait-Process -Id {pid} -Timeout 45 }} catch {{}}\nStart-Sleep -Milliseconds 700\nCopy-Item -LiteralPath {_ps(downloaded)} -Destination {_ps(target)} -Force\nStart-Process -FilePath {_ps(target)} -WorkingDirectory {_ps(target.parent)}\nStart-Sleep -Milliseconds 500\nRemove-Item -LiteralPath {_ps(update_root)} -Recurse -Force\n'''
    script.write_text(body,encoding="utf-8"); flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    subprocess.Popen(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",str(script)],creationflags=flags,close_fds=True); return True