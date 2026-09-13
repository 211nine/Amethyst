import asyncio
import ctypes
import threading
import time
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002
def media_key(vk):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
def play_pause():media_key(VK_MEDIA_PLAY_PAUSE)
def next_track():media_key(VK_MEDIA_NEXT_TRACK)
def previous_track():media_key(VK_MEDIA_PREV_TRACK)
def volume_up():media_key(VK_VOLUME_UP)
def volume_down():media_key(VK_VOLUME_DOWN)

class WindowsMedia:
    def __init__(self):
        self.ready=False; self.manager=None; self.available=False
        self.last_key=None; self.last_raw_progress=0; self.last_progress=0; self.last_tick=time.monotonic(); self.last_playing=False
        self.last_art_key=None; self.last_art_bytes=b""; self.last_art_attempt=0.0

    # windows media api is genuinely the worst thing ever bro ts so annoying
    async def start(self):
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
            self.manager=await GlobalSystemMediaTransportControlsSessionManager.request_async()
            self.ready=True; self.available=True
        except Exception:
            self.ready=True; self.available=False

    def _ms(self,value):
        if value is None:return 0
        try:
            if hasattr(value,"total_seconds"):return max(0,int(value.total_seconds()*1000))
        except Exception:pass
        try:
            raw=getattr(value,"duration",None)
            if raw is not None:return max(0,int(raw/10000))
        except Exception:pass
        try:
            raw=getattr(value,"ticks",None)
            if raw is not None:return max(0,int(raw/10000))
        except Exception:pass
        try:return max(0,int(float(value)*1000))
        except Exception:return 0

    def _playing(self,status):
        try:
            value=getattr(status,"value",status)
            if int(value)==4:return True
            if int(value)==5:return False
        except Exception:pass
        text=str(status or "").casefold()
        return "playing" in text and "paused" not in text

    def _spotify_session(self):
        if not self.available or not self.manager:return None
        try:
            for session in self.manager.get_sessions():
                if "spotify" in str(session.source_app_user_model_id or "").casefold():return session
        except Exception:pass
        return None

    def _repeat_name(self,value):
        try:value=int(getattr(value,"value",value))
        except Exception:
            text=str(value or "").casefold()
            if "track" in text:return "track"
            if "list" in text:return "context"
            return "off"
        return {1:"track",2:"context"}.get(value,"off")

    async def _thumbnail_bytes(self,props,key):
        now=time.monotonic()
        if key==self.last_art_key and self.last_art_bytes:return self.last_art_bytes
        if key==self.last_art_key and now-self.last_art_attempt<2.5:return b""
        if key!=self.last_art_key:
            self.last_art_key=key; self.last_art_bytes=b""
        self.last_art_attempt=now
        thumb=getattr(props,"thumbnail",None)
        if not thumb:return b""
        try:
            from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
            stream=await thumb.open_read_async(); reported=max(0,int(getattr(stream,"size",0) or 0)); capacity=min(reported or 5*1024*1024,5*1024*1024)
            buffer=Buffer(capacity); await stream.read_async(buffer,buffer.capacity,InputStreamOptions.READ_AHEAD)
            if buffer.length<=0:return b""
            reader=DataReader.from_buffer(buffer); data=bytes(reader.read_bytes(buffer.length))
            if data:self.last_art_bytes=data
            return data
        except Exception:return b""

    async def current(self):
        if not self.ready:await self.start()
        if not self.available or not self.manager:return None
        try:
            session=self._spotify_session()
            if session is None:return None
            props=await session.try_get_media_properties_async(); timeline=session.get_timeline_properties(); playback=session.get_playback_info()
            title=props.title or ""; artist=props.artist or ""; album=props.album_title or ""
            shuffle=bool(getattr(playback,"is_shuffle_active",False) or False)
            repeat=self._repeat_name(getattr(playback,"auto_repeat_mode",None))
            start_ms=self._ms(getattr(timeline,"start_time",None)); end_ms=self._ms(getattr(timeline,"end_time",None))
            duration_ms=max(0,end_ms-start_ms) if end_ms>start_ms else end_ms
            raw_progress=max(0,self._ms(getattr(timeline,"position",None))-start_ms)
            playing=self._playing(getattr(playback,"playback_status",None)); key=f"{artist}::{title}"; now=time.monotonic(); art_bytes=await self._thumbnail_bytes(props,key)
            if key!=self.last_key:
                progress_ms=raw_progress
            else:
                elapsed=max(0,int((now-self.last_tick)*1000)); expected=self.last_progress+(elapsed if self.last_playing else 0)
                if abs(raw_progress-expected)>2500 and raw_progress!=self.last_raw_progress:
                    progress_ms=raw_progress
                elif playing:
                    progress_ms=expected
                else:
                    progress_ms=raw_progress if raw_progress!=self.last_raw_progress else self.last_progress
            if duration_ms:progress_ms=min(progress_ms,duration_ms)
            self.last_key=key; self.last_raw_progress=raw_progress; self.last_progress=max(0,int(progress_ms)); self.last_tick=now; self.last_playing=playing
            return {"id":f"local::{artist}::{title}","title":title or "Unknown song","artist":artist,"artists":[{"name":artist,"image":""}] if artist else [],"album":album or "Unknown album","album_id":f"local::{artist}::{album}","image":"","art_bytes":art_bytes,"is_local":False,"duration_ms":duration_ms,"progress_ms":self.last_progress,"is_playing":playing,"shuffle":shuffle,"repeat":repeat,"volume":None,"source":"windows"}
        except Exception:return None

    async def seek(self,ms):
        if not self.ready:await self.start()
        session=self._spotify_session()
        if session is None:return False
        try:return bool(await session.try_change_playback_position_async(max(0,int(ms))*10000))
        except Exception:return False

    async def shuffle(self,state):
        if not self.ready:await self.start()
        session=self._spotify_session()
        if session is None:return False
        try:return bool(await session.try_change_shuffle_active_async(bool(state)))
        except Exception:return False

    async def repeat(self,state):
        if not self.ready:await self.start()
        session=self._spotify_session()
        if session is None:return False
        try:
            from winrt.windows.media import MediaPlaybackAutoRepeatMode
            values={
                "off":getattr(MediaPlaybackAutoRepeatMode,"NONE",getattr(MediaPlaybackAutoRepeatMode,"none",0)),
                "track":getattr(MediaPlaybackAutoRepeatMode,"TRACK",getattr(MediaPlaybackAutoRepeatMode,"track",1)),
                "context":getattr(MediaPlaybackAutoRepeatMode,"LIST",getattr(MediaPlaybackAutoRepeatMode,"list",2))
            }
            return bool(await session.try_change_auto_repeat_mode_async(values.get(str(state),values["off"])))
        except Exception:return False

class MediaPoller:
    def __init__(self,callback):
        self.callback=callback; self.loop=None; self.media=None; self.pending=False; self.closed=False
        self.ready=threading.Event(); self.thread=threading.Thread(target=self._run,daemon=True); self.thread.start()

    def _run(self):
        self.loop=asyncio.new_event_loop(); asyncio.set_event_loop(self.loop); self.media=WindowsMedia()
        try:self.loop.run_until_complete(self.media.start())
        except Exception:pass
        self.ready.set()
        try:self.loop.run_forever()
        finally:
            try:self.loop.close()
            except Exception:pass

    def request(self):
        if self.closed or not self.ready.is_set() or not self.loop or not self.media or self.pending:return
        self.pending=True
        try:future=asyncio.run_coroutine_threadsafe(self.media.current(),self.loop)
        except Exception:
            self.pending=False; return
        def done(f):
            self.pending=False
            try:data=f.result()
            except Exception:data=None
            try:self.callback(data)
            except Exception:pass
        future.add_done_callback(done)

    def _action(self,name,*args):
        if self.closed or not self.ready.is_set() or not self.loop or not self.media:return
        try:asyncio.run_coroutine_threadsafe(getattr(self.media,name)(*args),self.loop)
        except Exception:pass

    def seek(self,ms):self._action("seek",int(ms))
    def shuffle(self,state):self._action("shuffle",bool(state))
    def repeat(self,state):self._action("repeat",str(state))

    def close(self):
        self.closed=True
        try:
            if self.loop:self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:pass