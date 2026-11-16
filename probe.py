#!/usr/bin/env python3
"""Media state probe for the onur/seekbar lock-screen card.

Prints one JSON object per line describing what is *actually* playing.

Why this exists: Firefox/Zen expose a single MPRIS player for all tabs, and a
short sound in another tab (e.g. a 2 s WhatsApp notification) can grab it and
never hand it back, while Deezer keeps playing. MPRIS then reports a stale
"Paused" WhatsApp entry. We cross-check MPRIS with the real audio streams from
PulseAudio/PipeWire and fall back to the stream's title when MPRIS is stale.

Usage:  probe.py           -> stream forever (used by the plugin)
        probe.py --once    -> print current state once (for testing)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

POLL = 0.3                 # seconds between checks
HEARTBEAT = 1.0            # re-emit at least this often (keeps the timeline moving)
JUNK_MAX_LEN = 10.0        # MPRIS entries shorter than this are notification blips
STREAM_GRACE = 3.0         # keep the stream fallback this long after the stream goes quiet
CACHE = os.path.expanduser("~/.cache/noctalia-seekbar")
ART_DIR = os.path.join(CACHE, "art")
DB_PATH = os.path.join(CACHE, "tracks.json")
FOCUS_PATH = os.path.join(CACHE, "focus")   # written by the plugin on every button press
FOCUS_TTL = 600.0          # a player you paused from the card stays on the card this long

BROWSER_PLAYERS = ("firefox", "zen", "chromium", "chrome", "brave", "vivaldi", "librewolf", "floorp")
BROWSER_APPS = {"zen", "firefox", "chromium", "google chrome", "brave", "vivaldi", "librewolf", "floorp"}
# "<title> - <artist> - <Service>" (Deezer) / "<title> - <Service>" (others)
SERVICES = {"deezer", "spotify", "youtube", "youtube music", "soundcloud", "bandcamp",
            "tidal", "apple music", "twitch", "qobuz"}

FMT = "\t".join(["{{playerName}}", "{{status}}", "{{position}}", "{{mpris:length}}",
                 "{{mpris:artUrl}}", "{{artist}}", "{{title}}"])


def run(argv, timeout=2.0):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def key_of(title, artist):
    return f"{title.strip().casefold()}|{artist.strip().casefold()}"


# ---------------------------------------------------------------- track cache
class TrackDB:
    """Remembers length + cover per track so the stream fallback can show them."""

    def __init__(self):
        self.data = {}
        self.dirty = False
        try:
            with open(DB_PATH) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass

    def get(self, k):
        return self.data.get(k, {})

    def put(self, k, **kw):
        e = self.data.setdefault(k, {})
        for name, v in kw.items():
            if v and e.get(name) != v:
                e[name] = v
                self.dirty = True
        e["seen"] = int(time.time())

    def save(self):
        if not self.dirty:
            return
        if len(self.data) > 400:  # keep the newest 300
            keep = sorted(self.data.items(), key=lambda kv: kv[1].get("seen", 1e12))[-300:]
            self.data = dict(keep)
            live = {e.get("art") for e in self.data.values() if isinstance(e, dict)}
            for f in os.listdir(ART_DIR):
                p = os.path.join(ART_DIR, f)
                if p not in live:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        tmp = DB_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, DB_PATH)
            self.dirty = False
        except OSError:
            pass


class ArtCache:
    """Copies/downloads cover art into our own cache.

    Browsers delete their temp art file on track change, so we copy it the
    moment we first see it and always serve our copy.
    """

    def __init__(self):
        self.inflight = set()
        self.lock = threading.Lock()

    @staticmethod
    def path_for(url):
        return os.path.join(ART_DIR, hashlib.sha1(url.encode()).hexdigest()[:16] + ".img")

    def fetch(self, url):
        """Return a cached local path, or '' while it is not available yet."""
        if not url:
            return ""
        dst = self.path_for(url)
        if os.path.exists(dst):
            return dst
        if url.startswith("file://"):
            src = urllib.parse.unquote(url[7:])
            try:
                shutil.copyfile(src, dst + ".tmp")
                os.replace(dst + ".tmp", dst)
                return dst
            except OSError:
                return ""
        if url.startswith(("http://", "https://")):
            with self.lock:
                if url in self.inflight:
                    return ""
                self.inflight.add(url)
            threading.Thread(target=self._download, args=(url, dst), daemon=True).start()
        return ""

    def _download(self, url, dst):
        try:
            with urllib.request.urlopen(url, timeout=8) as r, open(dst + ".tmp", "wb") as f:
                f.write(r.read(8 * 1024 * 1024))
            os.replace(dst + ".tmp", dst)
        except Exception:
            pass
        finally:
            with self.lock:
                self.inflight.discard(url)


# ---------------------------------------------------------------- sources
def mpris_players():
    out = run(["playerctl", "-a", "metadata", "--format", FMT])
    players = []
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) < 7:
            continue
        name, status, pos, length, art, artist, title = f[:7]
        if not title.strip():
            continue
        try:
            pos_s = int(pos) / 1e6 if pos else 0.0
        except ValueError:
            pos_s = 0.0
        try:
            len_s = int(float(length)) / 1e6 if length else 0.0
        except ValueError:
            len_s = 0.0
        players.append({
            "player": name, "status": status, "pos": pos_s, "len": len_s,
            "artUrl": art, "artist": artist, "title": title,
            "browser": name.split(".")[0].lower() in BROWSER_PLAYERS,
        })
    return players


def parse_stream_name(name):
    toks = [t.strip() for t in name.split(" - ")]
    if len(toks) < 2:
        return None
    service = toks[-1].lower()
    if service not in SERVICES:
        return None
    toks = toks[:-1]
    if service == "deezer" and len(toks) >= 2:
        return " - ".join(toks[:-1]), toks[-1]
    return " - ".join(toks), ""


def browser_streams():
    """Uncorked browser audio streams whose name looks like a track."""
    out = run(["pactl", "-f", "json", "list", "sink-inputs"])
    res = []
    try:
        items = json.loads(out) if out else []
    except ValueError:
        return res
    for s in items:
        p = s.get("properties", {})
        app = (p.get("application.name") or "").lower()
        if app not in BROWSER_APPS or s.get("corked"):
            continue
        parsed = parse_stream_name(p.get("media.name") or "")
        if parsed:
            res.append({"title": parsed[0], "artist": parsed[1], "id": s.get("index")})
    return res


def read_focus():
    try:
        st = os.stat(FOCUS_PATH)
        if time.time() - st.st_mtime > FOCUS_TTL:
            return ""
        with open(FOCUS_PATH) as f:
            return f.read().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- decision
class Probe:
    def __init__(self):
        self.db = TrackDB()
        self.art = ArtCache()
        self.stream_track = None   # (key, started_at, title, artist)
        self.stream_seen_at = 0.0
        self.last_save = 0.0

    def remember(self, p):
        k = key_of(p["title"], p["artist"])
        art = self.art.fetch(p["artUrl"])
        self.db.put(k, len=round(p["len"], 1) if p["len"] > JUNK_MAX_LEN else 0, art=art)
        return art or self.db.get(k).get("art", "")

    def state(self):
        now = time.time()
        players = mpris_players()
        real = [p for p in players if not (0 < p["len"] <= JUNK_MAX_LEN)]
        for p in real:
            if p["status"] in ("Playing", "Paused"):
                p["art"] = self.remember(p)

        # the player you last controlled from the card sticks while it is paused
        focus = read_focus()
        if focus:
            for p in real:
                if p["player"] == focus and p["status"] == "Paused":
                    self.stream_track = None
                    return self.mpris_state(p)

        playing = [p for p in real if p["status"] == "Playing"]
        # a native player (Spotify app, mpv, ...) that is playing always wins
        for p in playing:
            if not p["browser"]:
                return self.mpris_state(p)

        streams = browser_streams()
        if streams:
            self.stream_seen_at = now
        browser_playing = playing[0] if playing else None

        # MPRIS agrees with a running stream -> trust MPRIS
        if browser_playing:
            bk = key_of(browser_playing["title"], browser_playing["artist"])
            if not streams or any(key_of(s["title"], s["artist"]) == bk or
                                  s["title"].casefold() == browser_playing["title"].casefold()
                                  for s in streams):
                self.stream_track = None
                return self.mpris_state(browser_playing)

        # MPRIS says Paused but the very same track is audibly playing: Zen's
        # transient Paused blip on track switch -> keep it interactive
        for p in real:
            if p["browser"] and p["status"] == "Paused" and any(
                    s["title"].casefold() == p["title"].casefold() for s in streams):
                self.stream_track = None
                return dict(self.mpris_state(p), status="Playing")

        # MPRIS is stale (paused/blip/other tab) but the browser is playing a track
        if streams:
            s = streams[0]
            k = key_of(s["title"], s["artist"])
            if not self.stream_track or self.stream_track[0] != k:
                prev = self.db.data.get("_stream", {})
                if self.stream_track:            # we watched the switch happen
                    started = now
                elif prev.get("key") == k:       # same track as our last run
                    started = prev.get("started")
                else:                            # joined mid-track: unknown
                    started = None
                self.stream_track = (k, started, s["title"], s["artist"])
                self.db.data["_stream"] = {"key": k, "started": started}
                self.db.dirty = True
            return self.stream_state(now)
        if self.stream_track and now - self.stream_seen_at < STREAM_GRACE:
            return self.stream_state(now)
        self.stream_track = None

        if browser_playing:
            return self.mpris_state(browser_playing)
        paused = [p for p in real if p["status"] == "Paused"]
        if paused:
            return self.mpris_state(paused[0])
        return {"mode": "none"}

    def mpris_state(self, p):
        k = key_of(p["title"], p["artist"])
        length = p["len"] if p["len"] > 0 else self.db.get(k).get("len", 0)
        return {
            "mode": "mpris", "player": p["player"], "status": p["status"],
            "title": p["title"], "artist": p["artist"], "art": p.get("art", ""),
            "pos": round(p["pos"], 1), "len": round(length, 1),
            "control": True, "seek": length > 0,
        }

    def stream_state(self, now):
        k, started, title, artist = self.stream_track
        cached = self.db.get(k)
        length = cached.get("len", 0)
        pos = -1.0                               # -1 = unknown elapsed time
        if started is not None:
            pos = now - started
            if length:
                pos = min(pos, length)
        return {
            "mode": "stream", "player": "", "status": "Playing",
            "title": title, "artist": artist, "art": cached.get("art", ""),
            "pos": round(pos, 1), "len": length, "control": False, "seek": False,
        }

    def tick(self):
        st = self.state()
        if time.time() - self.last_save > 10:
            self.db.save()
            self.last_save = time.time()
        return st


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    probe = Probe()
    if "--once" in sys.argv:
        print(json.dumps(probe.tick(), ensure_ascii=False))
        probe.db.save()
        return
    last_sig, last_emit = None, 0.0
    parent = os.getppid()
    try:
        while True:
            if os.getppid() != parent:   # noctalia went away
                break
            try:
                st = probe.tick()
            except Exception as e:   # never die: the card would freeze
                print(f"probe error: {e!r}", file=sys.stderr)
                time.sleep(1)
                continue
            sig = json.dumps({k: v for k, v in st.items() if k != "pos"}, sort_keys=True)
            now = time.time()
            if sig != last_sig or now - last_emit >= HEARTBEAT:
                sys.stdout.write(json.dumps(st, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                last_sig, last_emit = sig, now
            time.sleep(POLL)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        probe.db.save()


if __name__ == "__main__":
    main()
