import sqlite3
from datetime import date, timedelta, datetime
from config import db_file

class StatsDB:
    def __init__(self,path=None):
        target=path if path is not None else db_file
        self.db=sqlite3.connect(target,check_same_thread=False); self.db.row_factory=sqlite3.Row; self.setup()

    def setup(self):
        cur=self.db.cursor()
        cur.execute("""create table if not exists items (kind text not null,item_key text not null,name text not null,extra text default '',image text default '',duration_ms integer not null default 0,primary key(kind,item_key))""")
        cur.execute("""create table if not exists daily (day text not null,kind text not null,item_key text not null,plays integer not null default 0,listening_ms integer not null default 0,primary key(day,kind,item_key))""")
        cur.execute("""create table if not exists totals (kind text not null,item_key text not null,plays integer not null default 0,listening_ms integer not null default 0,primary key(kind,item_key))""")
        cur.execute("""create table if not exists app_meta (key text primary key,value text)""")
        cur.execute("""create table if not exists spotify_history (played_at text primary key,track_key text not null)""")
        cur.execute("""create table if not exists play_events (source text not null,played_at_ms integer not null,track_key text not null,primary key(source,played_at_ms,track_key))""")
        cur.execute("""create index if not exists play_events_lookup on play_events(track_key,played_at_ms)""")
        self.db.commit(); self.add_column("items","duration_ms","integer not null default 0"); self.add_column("daily","listening_ms","integer not null default 0"); self.add_column("totals","listening_ms","integer not null default 0")
        self.db.execute("update daily set listening_ms=0 where kind<>'artist' and listening_ms<>0")
        self.db.execute("update totals set listening_ms=0 where kind<>'artist' and listening_ms<>0"); self.db.commit()

    def add_column(self,table,name,definition):
        cols={row["name"] for row in self.db.execute(f"pragma table_info({table})")}
        if name not in cols: self.db.execute(f"alter table {table} add column {name} {definition}"); self.db.commit()

    def add_item(self,kind,key,name,extra="",image="",duration_ms=0):
        self.db.execute("insert into items(kind,item_key,name,extra,image,duration_ms) values(?,?,?,?,?,?) on conflict(kind,item_key) do update set name=excluded.name,extra=excluded.extra,image=case when excluded.image<>'' then excluded.image else items.image end,duration_ms=case when excluded.duration_ms>0 then excluded.duration_ms else items.duration_ms end",(kind,key,name,extra,image,int(duration_ms or 0)))

    def update_image(self,kind,key,image):
        if not image:return
        self.db.execute("update items set image=? where kind=? and item_key=?",(image,kind,key)); self.db.commit()

    def track_rows(self,track):
        track_key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
        album_key=track.get("album_id") or f'{track.get("artist","")}::{track.get("album","")}'
        rows=[("song",track_key,track.get("title","Unknown song"),track.get("artist",""),track.get("image","") if not track.get("is_local") else "",track.get("duration_ms",0)),("album",album_key,track.get("album","Unknown album"),track.get("artist",""),track.get("image","") if not track.get("is_local") else "",0)]
        artists=track.get("artists") or []; seen=set()
        if artists:
            for artist in artists:
                name=(artist.get("name") or "Unknown artist").strip(); clean=name.casefold()
                if not name or clean in seen: continue
                seen.add(clean); key=artist.get("id") or name
                rows.append(("artist",key,name,"",artist.get("image","") or "",0))
        elif track.get("artist"):
            for name in [x.strip() for x in str(track.get("artist") or "").split(",") if x.strip()]:
                clean=name.casefold()
                if clean in seen: continue
                seen.add(clean); rows.append(("artist",name,name,"","",0))
        return rows


    def history_key(self,track):
        artist=str((track or {}).get("artist") or "").strip().casefold()
        title=str((track or {}).get("title") or "").strip().casefold()
        return f"{artist}::{title}"

    def record_play_event(self,track,played_at_ms=None,source="local"):
        if not track:return
        played_at_ms=int(played_at_ms or datetime.now().timestamp()*1000); key=self.history_key(track)
        if not key.strip(":"):return
        self.db.execute("insert or ignore into play_events(source,played_at_ms,track_key) values(?,?,?)",(str(source),played_at_ms,key)); self.db.commit()

    def has_nearby_play(self,track,played_at_ms,window_ms=45000):
        key=self.history_key(track)
        if not key.strip(":"):return False
        row=self.db.execute("select 1 from play_events where track_key=? and played_at_ms between ? and ? limit 1",(key,int(played_at_ms)-int(window_ms),int(played_at_ms)+int(window_ms))).fetchone()
        return bool(row)

    def add_history_play(self,track,played_at,estimated_listen_ms=0,source="history"):
        if not track or not played_at:return False
        key=track.get("id") or f'{track.get("artist","")}::{track.get("title","")}'
        try:played_ms=int(datetime.fromisoformat(str(played_at).replace("Z","+00:00")).timestamp()*1000)
        except Exception:played_ms=0
        if played_ms and self.has_nearby_play(track,played_ms):return False
        try:
            self.db.execute("insert into spotify_history(played_at,track_key) values(?,?)",(str(played_at),str(key))); self.db.commit()
        except sqlite3.IntegrityError:return False
        try:event_day=datetime.fromisoformat(str(played_at).replace("Z","+00:00")).astimezone().date().isoformat()
        except Exception:event_day=date.today().isoformat()
        self.add_play(track,event_day)
        if estimated_listen_ms>0:self.add_listen(track,int(estimated_listen_ms),event_day)
        if played_ms:self.record_play_event(track,played_ms,source)
        return True
    def add_play(self,track,day=None):
        if not track:return
        day=day or date.today().isoformat()
        for kind,key,name,extra,image,duration_ms in self.track_rows(track):
            self.add_item(kind,key,name,extra,image,duration_ms)
            self.db.execute("insert into daily(day,kind,item_key,plays,listening_ms) values(?,?,?,1,0) on conflict(day,kind,item_key) do update set plays=plays+1",(day,kind,key))
            self.db.execute("insert into totals(kind,item_key,plays,listening_ms) values(?,?,1,0) on conflict(kind,item_key) do update set plays=plays+1",(kind,key))
        self.db.commit(); self.trim_daily()

    def add_listen(self,track,delta_ms,day=None):
        if not track or delta_ms<=0:return
        day=day or date.today().isoformat(); delta_ms=int(delta_ms)
        for kind,key,name,extra,image,duration_ms in self.track_rows(track):
            if kind!="artist":continue
            self.add_item(kind,key,name,extra,image,duration_ms)
            self.db.execute("insert into daily(day,kind,item_key,plays,listening_ms) values(?,?,?,0,?) on conflict(day,kind,item_key) do update set listening_ms=listening_ms+excluded.listening_ms",(day,kind,key,delta_ms))
            self.db.execute("insert into totals(kind,item_key,plays,listening_ms) values(?,?,0,?) on conflict(kind,item_key) do update set listening_ms=listening_ms+excluded.listening_ms",(kind,key,delta_ms))
        self.db.commit()

    # stops the database slowly becoming massive. you know what else is massive??
    # the lowwwwww taper fadeee haahahahahahahahahaahahahahahahahahahaahhahahahahahaahhaahahahahaahahah
    def trim_daily(self):
        keep_from=(date.today()-timedelta(days=400)).isoformat(); self.db.execute("delete from daily where day < ?",(keep_from,)); self.db.commit()

    def top(self,kind,period,limit=20):
        if kind=="artist":
            if period=="All time":
                return self.db.execute("""
                    select min(i.item_key) item_key,min(i.name) name,'' extra,
                           max(case when i.image<>'' then i.image else '' end) image,0 duration_ms,
                           sum(t.plays) plays,sum(t.listening_ms) listening_ms
                    from totals t join items i on i.kind=t.kind and i.item_key=t.item_key
                    where t.kind='artist' group by lower(trim(i.name))
                    having sum(t.listening_ms)>=60000
                    order by listening_ms desc,plays desc,name collate nocase limit ?
                """,(limit,)).fetchall()
            days={"Week":7,"Month":30,"Year":365}.get(period,7); start=(date.today()-timedelta(days=days-1)).isoformat()
            return self.db.execute("""
                select min(i.item_key) item_key,min(i.name) name,'' extra,
                       max(case when i.image<>'' then i.image else '' end) image,0 duration_ms,
                       sum(d.plays) plays,sum(d.listening_ms) listening_ms
                from daily d join items i on i.kind=d.kind and i.item_key=d.item_key
                where d.kind='artist' and d.day>=? group by lower(trim(i.name))
                having sum(d.listening_ms)>=60000
                order by listening_ms desc,plays desc,name collate nocase limit ?
            """,(start,limit)).fetchall()

        if period=="All time":
            return self.db.execute("""
                select i.item_key,i.name,i.extra,i.image,i.duration_ms,t.plays,0 listening_ms
                from totals t join items i on i.kind=t.kind and i.item_key=t.item_key
                where t.kind=? order by t.plays desc,i.name collate nocase limit ?
            """,(kind,limit)).fetchall()
        days={"Week":7,"Month":30,"Year":365}.get(period,7); start=(date.today()-timedelta(days=days-1)).isoformat()
        return self.db.execute("""
            select i.item_key,i.name,i.extra,i.image,i.duration_ms,sum(d.plays) plays,0 listening_ms
            from daily d join items i on i.kind=d.kind and i.item_key=d.item_key
            where d.kind=? and d.day>=? group by d.item_key
            order by plays desc,i.name collate nocase limit ?
        """,(kind,start,limit)).fetchall()


    def image_urls(self): return [r["image"] for r in self.db.execute("select distinct image from items where image<>''").fetchall()]
    def missing_artist_images(self,limit=100): return self.db.execute("select item_key,name from items where kind='artist' and (image is null or image='') limit ?",(limit,)).fetchall()
    def set_meta(self,key,value): self.db.execute("insert into app_meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(key,str(value))); self.db.commit()
    def get_meta(self,key,default=None):
        row=self.db.execute("select value from app_meta where key=?",(key,)).fetchone(); return row["value"] if row else default