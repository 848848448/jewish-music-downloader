import json
import os
import sys
import time
import traceback
import base64
from datetime import datetime

try:
    import requests
except ImportError:
    print("❌ 'requests' פעלט. אינסטאליר עס:")
    print("   גיי צו ☰ → Pip → שרייב 'requests' → Install")
    sys.exit(1)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── CONFIG ───────────────────────────────────────────
API_URL = "https://jewishmusic.fm:8443/graphql"
AUDIO_API_BASE = "https://jewishmusic.fm:8443/api/audio-file"
APP_DIR = os.path.join(os.path.expanduser("~"), "JewishMusicApp")
CACHE_FILE = os.path.join(APP_DIR, "artists_cache.json")
ACCOUNTS_FILE = os.path.join(APP_DIR, "accounts.json")
ACTIVITY_LOG = os.path.join(APP_DIR, "activity.json")
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Music", "JewishMusic")

os.makedirs(APP_DIR, exist_ok=True)

# ─── CURRENT STATE ────────────────────────────────────
current_user = None  # set after login


# ─── HELPERS ──────────────────────────────────────────
def clear():
    os.system("clear" if os.name != "nt" else "cls")


def box(title, items=None, width=50):
    print()
    print("┌" + "─" * width + "┐")
    print("│" + f" 🎵  {title}".ljust(width) + "│")
    print("├" + "─" * width + "┤")
    if items:
        for item in items:
            line = f"  {item}"
            if len(line) > width:
                line = line[:width - 1] + "…"
            print("│" + line.ljust(width) + "│")
    print("└" + "─" * width + "┘")


def prompt(text="» "):
    try:
        return input(f"\n  {text}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "exit"


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def ensure_download_dir():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return DOWNLOAD_DIR


def check_token_expiry(token):
    try:
        parts = token.strip().split(".")
        if len(parts) != 3:
            return True, 999
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        exp = decoded.get("exp", 0)
        diff = exp - time.time()
        return diff > 0, int(diff / 60)
    except Exception:
        return True, 999



# ─── ACCOUNTS ─────────────────────────────────────────
def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def get_user_dir(username):
    d = os.path.join(APP_DIR, "users", username)
    os.makedirs(d, exist_ok=True)
    return d


def get_user_file(username, filename):
    return os.path.join(get_user_dir(username), filename)


def create_account(name, token, is_admin=False):
    accounts = load_accounts()
    accounts[name] = {
        "token": token.strip(),
        "is_admin": is_admin,
        "created": now_str(),
        "last_login": now_str(),
    }
    save_accounts(accounts)
    # init user files
    for fname, default in [("favorites.json", '{"artists":[],"albums":[]}'),
                           ("playlists.json", "{}"),
                           ("history.json", "[]")]:
        fp = get_user_file(name, fname)
        if not os.path.exists(fp):
            with open(fp, "w") as f:
                f.write(default)


def login(username):
    global current_user
    accounts = load_accounts()
    if username in accounts:
        accounts[username]["last_login"] = now_str()
        save_accounts(accounts)
        current_user = {
            "name": username,
            "token": accounts[username]["token"],
            "is_admin": accounts[username].get("is_admin", False),
        }
        return True
    return False


def get_token():
    if current_user:
        return current_user["token"]
    return ""


def is_admin():
    return current_user and current_user.get("is_admin", False)


# ─── ACTIVITY LOG ─────────────────────────────────────
def log_activity(action, details=""):
    try:
        log = []
        if os.path.exists(ACTIVITY_LOG):
            with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
                log = json.load(f)
        log.append({
            "user": current_user["name"] if current_user else "?",
            "action": action,
            "details": details,
            "time": now_str(),
        })
        # Keep last 500 entries
        log = log[-500:]
        with open(ACTIVITY_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def log_download(track, artist="", album=""):
    """Log a download to both activity log and user history."""
    fname = (track.get("file", "") or "").split("/")[-1] or f"Track {track.get('id')}"
    log_activity("download", f"{fname} — {artist} — {album}")
    # User history
    if current_user:
        hf = get_user_file(current_user["name"], "history.json")
        try:
            history = []
            if os.path.exists(hf):
                with open(hf, "r", encoding="utf-8") as f:
                    history = json.load(f)
            history.append({
                "file": fname,
                "artist": artist,
                "album": album,
                "time": now_str(),
                "track_id": track.get("id"),
            })
            history = history[-200:]
            with open(hf, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ─── FAVORITES (per user) ────────────────────────────
def load_favorites():
    if not current_user:
        return {"artists": [], "albums": []}
    fp = get_user_file(current_user["name"], "favorites.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"artists": [], "albums": []}


def save_favorites(favs):
    if current_user:
        fp = get_user_file(current_user["name"], "favorites.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(favs, f, ensure_ascii=False, indent=2)


def add_favorite_artist(artist):
    favs = load_favorites()
    aid = artist.get("id")
    if not any(a.get("id") == aid for a in favs["artists"]):
        favs["artists"].append({
            "id": aid, "enName": artist.get("enName", ""),
            "heName": artist.get("heName", ""),
        })
        save_favorites(favs)
        print(f"  ⭐ {artist.get('enName')} צוגעלייגט!")
        log_activity("favorite_artist", artist.get("enName", ""))
    else:
        print(f"  ℹ️ שוין אין פאַוואָריטן")


def add_favorite_album(album, artist_name=""):
    favs = load_favorites()
    alb_id = album.get("id")
    if not any(a.get("id") == alb_id for a in favs["albums"]):
        favs["albums"].append({
            "id": alb_id, "enName": album.get("enName", ""),
            "artist": artist_name,
            "track_count": len(album.get("tracks", [])),
        })
        save_favorites(favs)
        print(f"  ⭐ {album.get('enName')} צוגעלייגט!")
        log_activity("favorite_album", album.get("enName", ""))
    else:
        print(f"  ℹ️ שוין אין פאַוואָריטן")


def remove_favorite_artist(artist_id):
    favs = load_favorites()
    favs["artists"] = [a for a in favs["artists"] if a.get("id") != artist_id]
    save_favorites(favs)


def remove_favorite_album(album_id):
    favs = load_favorites()
    favs["albums"] = [a for a in favs["albums"] if a.get("id") != album_id]
    save_favorites(favs)


# ─── PLAYLISTS (per user) ────────────────────────────
def load_playlists():
    if not current_user:
        return {}
    fp = get_user_file(current_user["name"], "playlists.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_playlists(pls):
    if current_user:
        fp = get_user_file(current_user["name"], "playlists.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(pls, f, ensure_ascii=False, indent=2)


def add_to_playlist(playlist_name, track, artist_name="", album_name=""):
    pls = load_playlists()
    if playlist_name not in pls:
        pls[playlist_name] = []
    tid = track.get("id")
    if not any(t.get("id") == tid for t in pls[playlist_name]):
        pls[playlist_name].append({
            "id": tid, "file": track.get("file", ""),
            "artist": artist_name, "album": album_name,
        })
        save_playlists(pls)
        fname = (track.get("file", "") or "").split("/")[-1] or f"Track {tid}"
        print(f"  📋 {fname} → '{playlist_name}'")
    else:
        print(f"  ℹ️ שוין אין פּלייליסט")


# ─── API ──────────────────────────────────────────────
def gql(query_str):
    try:
        r = requests.post(
            API_URL, json={"query": query_str}, verify=False,
            headers={"Content-Type": "application/json"}, timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            if "errors" not in data:
                return data.get("data", {})
    except requests.exceptions.Timeout:
        print("  ⏰ סערווער פּאַמעלעך...")
    except requests.exceptions.ConnectionError:
        print("  📡 קיין אינטערנעט")
    except Exception as e:
        print(f"  ❌ {e}")
    return None


def fetch_artists():
    if os.path.exists(CACHE_FILE):
        if time.time() - os.path.getmtime(CACHE_FILE) < 86400:
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached:
                        print(f"  ⚡ {len(cached)} זינגערס (cache)")
                        return cached
            except Exception:
                pass
    all_artists = []
    skip = 0
    batch = 500
    print("  ⏳ לאָדט זינגערס...")
    while True:
        data = gql(f'query {{ artists(skip: {skip}, take: {batch}) {{ id enName heName }} }}')
        if not data:
            break
        artists = data.get("artists", [])
        if not artists:
            break
        all_artists.extend(artists)
        print(f"     {len(all_artists)}...", end="\r")
        if len(artists) < batch:
            break
        skip += batch
    print()
    if all_artists:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(all_artists, f, ensure_ascii=False)
        except Exception:
            pass
    return all_artists


def fetch_artist_albums(artist_id):
    data = gql(f'query {{ artist(where: {{ id: {artist_id} }}) {{ id enName heName albums {{ id enName tracks {{ id file }} }} }} }}')
    return data.get("artist") if data else None


def fetch_new_releases():
    data = gql('query { albums(take: 50) { id enName artists { enName } tracks { id file } } }')
    if data:
        albums = data.get("albums", [])
        return sorted(albums, key=lambda x: int(x.get("id", 0)), reverse=True)[:10]
    return []


def search_songs(query):
    data = gql(f'query {{ tracks(where: {{ file: {{ contains: "{query}" }} }}, take: 30) {{ id file album {{ id enName artists {{ enName }} }} }} }}')
    return data.get("tracks", []) if data else []


def search_albums_api(query):
    data = gql(f'query {{ albums(where: {{ enName: {{ contains: "{query}" }} }}, take: 20) {{ id enName artists {{ enName }} tracks {{ id file }} }} }}')
    return data.get("albums", []) if data else []


# ─── DOWNLOAD ─────────────────────────────────────────
def download_track(track, album_dir=None, label="", artist_name="", album_name=""):
    track_id = track.get("id")
    file_path = track.get("file", "")
    if not track_id:
        print("  ❌ קיין טראַק ID")
        return False

    filename = file_path.split("/")[-1] if file_path else f"track_{track_id}.mp3"
    if not filename.endswith((".mp3", ".wav", ".m4a", ".flac")):
        filename += ".mp3"

    save_dir = album_dir or ensure_download_dir()
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    if os.path.exists(save_path):
        print(f"  ✓ שוין דאָ: {filename}")
        return True

    token = get_token()
    url = f"{AUDIO_API_BASE}?trackId={track_id}&token={token}"

    try:
        r = requests.get(url, verify=False, stream=True, timeout=60)

        if r.status_code == 403:
            print("  🔒 טאָקען אויסגעלאָפן!")
            new_t = prompt("נייער טאָקען (אָדער 'skip'): ")
            if new_t and new_t != "skip":
                if current_user:
                    accounts = load_accounts()
                    accounts[current_user["name"]]["token"] = new_t.strip()
                    save_accounts(accounts)
                    current_user["token"] = new_t.strip()
                print("  ✅ טאָקען אַפּדעיטעד!")
                return download_track(track, album_dir, label, artist_name, album_name)
            return False
        if r.status_code != 200:
            print(f"  ❌ שגיאה: {r.status_code}")
            return False

        total = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded / total * 100)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    print(f"\r  ⬇ {label}{bar} {pct}% ({format_size(downloaded)})", end="", flush=True)

        size_str = format_size(os.path.getsize(save_path))
        print(f"\r  ✅ {filename} ({size_str})" + " " * 30)
        log_download(track, artist_name, album_name)
        return True

    except Exception as e:
        print(f"\n  ❌ {e}")
        if os.path.exists(save_path):
            os.remove(save_path)
        return False


def download_album(album, artist_name=""):
    album_name = album.get("enName", "Unknown")
    tracks = album.get("tracks", [])
    if not tracks:
        print("  ❌ קיין לידער")
        return
    album_dir = os.path.join(ensure_download_dir(), artist_name, album_name)
    os.makedirs(album_dir, exist_ok=True)
    total = len(tracks)
    print(f"\n  📥 {total} לידער → {album_dir}\n")
    success = 0
    for i, t in enumerate(tracks, 1):
        if download_track(t, album_dir, f"[{i}/{total}] ", artist_name, album_name):
            success += 1
    print(f"\n  🎉 {success}/{total} דאַונלאָודעד!")


# ─── PLAYER ───────────────────────────────────────────
def find_downloaded_files(directory=None):
    directory = directory or DOWNLOAD_DIR
    files = []
    if not os.path.exists(directory):
        return files
    for root, dirs, filenames in os.walk(directory):
        for fn in sorted(filenames):
            if fn.endswith((".mp3", ".wav", ".m4a", ".flac")):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, DOWNLOAD_DIR)
                files.append({"name": fn, "path": full, "rel": rel})
    return files


def play_file(filepath):
    if not os.path.exists(filepath):
        print(f"  ❌ נישט געפֿונען")
        return
    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
        print(f"  ▶️  {os.path.basename(filepath)}")
        print(f"     [Enter = שטאָפּ]")
        input()
        pygame.mixer.music.stop()
        pygame.mixer.quit()
        return
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️ pygame: {e}")
    try:
        if sys.platform == "linux":
            os.system(f'am start -a android.intent.action.VIEW -d "file://{filepath}" -t "audio/*" 2>/dev/null')
            print(f"  ▶️  עפֿנט אין סיסטעם שפּילער...")
            return
    except Exception:
        pass
    print("  ❌ אינסטאליר pygame: ☰ → Pip → pygame")


def show_player():
    files = find_downloaded_files()
    if not files:
        print("  ❌ קיין דאַונלאָודעד לידער")
        return
    page = 0
    per_page = 15
    while True:
        total_pages = (len(files) - 1) // per_page + 1
        start = page * per_page
        batch = files[start:start + per_page]
        items = [f"📂 {len(files)} לידער", "─" * 46]
        for i, f in enumerate(batch, start + 1):
            items.append(f"{i:3d}. 🎵 {f['rel']}")
        items.append("─" * 46)
        if total_pages > 1:
            items.append(f"  בלאט {page+1}/{total_pages} (n/p)")
        items.append("0  →  צוריק")
        box("▶️ שפּילער", items)
        ch = prompt("# צו שפּילן: ")
        if ch in ("0", "back", ""):
            return
        elif ch == "n" and page < total_pages - 1:
            page += 1
        elif ch == "p" and page > 0:
            page -= 1
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(files):
                    play_file(files[idx]["path"])
                    log_activity("play", files[idx]["name"])
            except ValueError:
                pass


# ─── SCREENS ──────────────────────────────────────────
def show_album(album, artist_name=""):
    album_name = album.get("enName", "Unknown")
    tracks = album.get("tracks", [])
    if not tracks:
        print("  ❌ קיין לידער")
        return
    while True:
        items = [
            f"📀 {album_name} ({len(tracks)} לידער)",
        ]
        if artist_name:
            items.append(f"🎤 {artist_name}")
        items += ["─" * 46, "⬇ all → דאַונלאָוד אַלע", "⭐ fav → פאַוואָריטן", "📋 pl → פּלייליסט", "─" * 46]
        for i, t in enumerate(tracks, 1):
            fname = (t.get("file", "") or "").split("/")[-1] or f"Track {i}"
            items.append(f"{i:2d}. {fname}")
        items += ["─" * 46, "0  → צוריק"]
        box(album_name, items)
        ch = prompt("» ")
        if ch in ("0", "back", ""):
            return
        elif ch == "all":
            download_album(album, artist_name)
        elif ch == "fav":
            add_favorite_album(album, artist_name)
        elif ch == "pl":
            name = prompt("פּלייליסט נאָמען: ")
            if name and name not in ("exit", ""):
                for t in tracks:
                    add_to_playlist(name, t, artist_name, album_name)
                print(f"  ✅ {len(tracks)} לידער → '{name}'")
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(tracks):
                    t = tracks[idx]
                    fname = (t.get("file", "") or "").split("/")[-1] or f"Track {idx+1}"
                    print(f"\n  🎵 {fname}")
                    print(f"  [d]אַונלאָוד  [p]לייליסט  [0]צוריק")
                    act = prompt("» ")
                    if act == "d":
                        download_track(t, artist_name=artist_name, album_name=album_name)
                    elif act == "p":
                        pn = prompt("פּלייליסט נאָמען: ")
                        if pn and pn not in ("exit", ""):
                            add_to_playlist(pn, t, artist_name, album_name)
            except ValueError:
                pass


def show_artist(artist):
    artist_name = artist.get("enName", "Unknown")
    print(f"\n  ⏳ לאָדט {artist_name}...")
    details = fetch_artist_albums(artist.get("id"))
    if not details or not details.get("albums"):
        print("  ❌ קיין אלבומס")
        return
    albums = details["albums"]
    while True:
        items = [f"🎤 {artist_name}", "─" * 46,
                 "⭐ fav → פאַוואָריטן", "⬇ all → דאַונלאָוד אַלע", "─" * 46]
        for i, a in enumerate(albums, 1):
            tc = len(a.get("tracks", []))
            items.append(f"{i:2d}. {a.get('enName', '?')} ({tc})")
        items += ["─" * 46, "0  → צוריק"]
        box(artist_name, items)
        ch = prompt("» ")
        if ch in ("0", "back", ""):
            return
        elif ch == "fav":
            add_favorite_artist(artist)
        elif ch == "all":
            for a in albums:
                download_album(a, artist_name)
            print("  🎉 אַלע אלבומס דאַונלאָודעד!")
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(albums):
                    show_album(albums[idx], artist_name)
            except ValueError:
                pass


def show_new_releases():
    print("\n  ⏳ נייע רילעסעס...")
    albums = fetch_new_releases()
    if not albums:
        print("  ❌ גאָרנישט")
        return
    items = []
    for i, a in enumerate(albums, 1):
        arts = a.get("artists", [])
        artist = ", ".join(x.get("enName", "?") for x in arts) if arts else "?"
        items.append(f"{i:2d}. {a.get('enName', '?')} — {artist} ({len(a.get('tracks', []))})")
    items += ["─" * 46, "0  → צוריק"]
    box("🔥 נייע רילעסעס", items)
    ch = prompt("» ")
    if ch in ("0", "back", ""):
        return
    try:
        idx = int(ch) - 1
        if 0 <= idx < len(albums):
            arts = albums[idx].get("artists", [])
            show_album(albums[idx], arts[0].get("enName", "") if arts else "")
    except (ValueError, IndexError):
        pass


def show_search_menu(artists):
    box("🔍 זוכן", ["1. 🎤 זינגער", "2. 📀 אלבום", "3. 🎵 ליד", "─" * 46, "0  → צוריק"])
    ch = prompt("» ")
    if ch in ("0", "back", ""):
        return
    elif ch == "1":
        q = prompt("זינגער: ")
        if not q or q == "exit":
            return
        results = sorted([a for a in artists if q.lower() in (a.get("enName") or "").lower()
                          or q.lower() in (a.get("heName") or "").lower()],
                         key=lambda x: x.get("enName", ""))
        if results:
            show_list_and_select(results, "זינגערס", lambda a: show_artist(a),
                                 lambda a: f"{a.get('enName', '?')}" + (f" ({a['heName']})" if a.get("heName") else ""))
        else:
            print(f"  ❌ קיין זינגער '{q}'")
    elif ch == "2":
        q = prompt("אלבום: ")
        if not q or q == "exit":
            return
        print("  ⏳ זוכט...")
        albums = search_albums_api(q)
        if albums:
            show_list_and_select(albums, f"אלבומס: '{q}'",
                                 lambda a: show_album(a, (a.get("artists", [{}])[0].get("enName", "") if a.get("artists") else "")),
                                 lambda a: f"{a.get('enName', '?')} — {', '.join(x.get('enName', '?') for x in a.get('artists', []))}")
        else:
            print(f"  ❌ קיין אלבומס '{q}'")
    elif ch == "3":
        q = prompt("ליד: ")
        if not q or q == "exit":
            return
        print("  ⏳ זוכט...")
        tracks = search_songs(q)
        if tracks:
            items = []
            for i, t in enumerate(tracks, 1):
                fname = (t.get("file", "") or "").split("/")[-1] or "?"
                alb = t.get("album", {})
                arts = alb.get("artists", [])
                items.append(f"{i:2d}. {fname}")
                if arts or alb.get("enName"):
                    items.append(f"    {arts[0].get('enName', '') if arts else ''} — {alb.get('enName', '')}")
            items += ["─" * 46, "0  → צוריק"]
            box(f"🎵 לידער: '{q}'", items)
            ch2 = prompt("# צו דאַונלאָודן: ")
            if ch2 not in ("0", "back", ""):
                try:
                    idx = int(ch2) - 1
                    if 0 <= idx < len(tracks):
                        download_track(tracks[idx])
                except (ValueError, IndexError):
                    pass
        else:
            print(f"  ❌ קיין לידער '{q}'")


def show_list_and_select(items_list, title, on_select, format_fn):
    """Generic list display with selection."""
    items = []
    for i, item in enumerate(items_list, 1):
        items.append(f"{i:2d}. {format_fn(item)}")
    items += ["─" * 46, "0  → צוריק"]
    box(f"🔍 {len(items_list)} {title}", items)
    ch = prompt("» ")
    if ch in ("0", "back", ""):
        return
    try:
        idx = int(ch) - 1
        if 0 <= idx < len(items_list):
            on_select(items_list[idx])
    except (ValueError, IndexError):
        pass


def show_favorites():
    favs = load_favorites()
    fa = favs.get("artists", [])
    fb = favs.get("albums", [])
    if not fa and not fb:
        print("  ❌ קיין פאַוואָריטן")
        return
    while True:
        items = []
        if fa:
            items.append("🎤 זינגערס:")
            for i, a in enumerate(fa, 1):
                items.append(f"  a{i}. {a.get('enName', '?')}")
        if fb:
            items.append("📀 אלבומס:")
            for i, a in enumerate(fb, 1):
                items.append(f"  b{i}. {a.get('enName', '?')} — {a.get('artist', '')}")
        items += ["─" * 46, "del a#/b# → אראפנעמען", "0  → צוריק"]
        box("⭐ פאַוואָריטן", items)
        ch = prompt("» ")
        if ch in ("0", "back", ""):
            return
        elif ch.startswith("del "):
            target = ch[4:].strip()
            try:
                if target.startswith("a"):
                    idx = int(target[1:]) - 1
                    if 0 <= idx < len(fa):
                        remove_favorite_artist(fa[idx].get("id"))
                        print(f"  🗑️ {fa.pop(idx).get('enName')}")
                elif target.startswith("b"):
                    idx = int(target[1:]) - 1
                    if 0 <= idx < len(fb):
                        remove_favorite_album(fb[idx].get("id"))
                        print(f"  🗑️ {fb.pop(idx).get('enName')}")
            except (ValueError, IndexError):
                pass
        elif ch.startswith("a"):
            try:
                idx = int(ch[1:]) - 1
                if 0 <= idx < len(fa):
                    show_artist(fa[idx])
            except (ValueError, IndexError):
                pass
        elif ch.startswith("b"):
            try:
                idx = int(ch[1:]) - 1
                if 0 <= idx < len(fb):
                    det = fetch_artist_albums(fb[idx].get("id"))
                    if det and det.get("albums"):
                        show_album(det["albums"][0], fb[idx].get("artist", ""))
            except (ValueError, IndexError):
                pass


def show_playlists():
    pls = load_playlists()
    if not pls:
        print("  ❌ קיין פּלייליסטן")
        return
    while True:
        names = list(pls.keys())
        items = []
        for i, name in enumerate(names, 1):
            items.append(f"{i:2d}. 📋 {name} ({len(pls[name])} לידער)")
        items += ["─" * 46, "del # → מעק", "0  → צוריק"]
        box("📋 פּלייליסטן", items)
        ch = prompt("» ")
        if ch in ("0", "back", ""):
            return
        elif ch.startswith("del "):
            try:
                idx = int(ch[4:].strip()) - 1
                if 0 <= idx < len(names):
                    del pls[names[idx]]
                    save_playlists(pls)
                    print(f"  🗑️ '{names[idx]}' אויסגעמעקט")
            except (ValueError, IndexError):
                pass
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(names):
                    name = names[idx]
                    tracks = pls[name]
                    items2 = [f"📋 {name} — {len(tracks)} לידער", "─" * 46, "⬇ all → דאַונלאָוד"]
                    for i, t in enumerate(tracks, 1):
                        fname = (t.get("file", "") or "").split("/")[-1] or f"Track {i}"
                        items2.append(f"{i:2d}. {fname}")
                    items2 += ["─" * 46, "0  → צוריק"]
                    box(name, items2)
                    ch2 = prompt("» ")
                    if ch2 == "all":
                        for i, t in enumerate(tracks, 1):
                            download_track(t, label=f"[{i}/{len(tracks)}] ")
                    elif ch2 not in ("0", "back", ""):
                        try:
                            idx2 = int(ch2) - 1
                            if 0 <= idx2 < len(tracks):
                                download_track(tracks[idx2])
                        except ValueError:
                            pass
            except ValueError:
                pass


def show_stats():
    total_files = 0
    total_size = 0
    artists_set = set()
    albums_set = set()
    if os.path.exists(DOWNLOAD_DIR):
        for root, dirs, files in os.walk(DOWNLOAD_DIR):
            for fn in files:
                if fn.endswith((".mp3", ".wav", ".m4a", ".flac")):
                    total_files += 1
                    total_size += os.path.getsize(os.path.join(root, fn))
            rel = os.path.relpath(root, DOWNLOAD_DIR)
            parts = rel.split(os.sep)
            if len(parts) >= 1 and parts[0] != ".":
                artists_set.add(parts[0])
            if len(parts) >= 2:
                albums_set.add(parts[1])

    favs = load_favorites()
    pls = load_playlists()
    valid, minutes = check_token_expiry(get_token())

    items = [
        "─" * 46,
        f"👤  משתמש:             {current_user['name'] if current_user else '?'}",
        f"🎵  לידער:             {total_files}",
        f"🎤  זינגערס:           {len(artists_set)}",
        f"📀  אלבומס:            {len(albums_set)}",
        f"💾  גרייס:             {format_size(total_size)}",
        "─" * 46,
        f"⭐  פאַוואָריטן:        {len(favs.get('artists', []))} זינגערס, {len(favs.get('albums', []))} אלבומס",
        f"📋  פּלייליסטן:         {len(pls)}",
        "─" * 46,
    ]
    if valid:
        t_str = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
        sym = "⚠️" if minutes < 30 else "✅"
        items.append(f"🔑  טאָקען: {sym} {t_str}")
    else:
        items.append("🔑  טאָקען: ❌ אויסגעלאָפן!")
    box("📊 סטאַטיסטיק", items)


def show_history():
    """Show current user's download history."""
    if not current_user:
        return
    hf = get_user_file(current_user["name"], "history.json")
    if not os.path.exists(hf):
        print("  ❌ קיין היסטאָריע")
        return
    try:
        with open(hf, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        print("  ❌ קען נישט לייענען")
        return
    if not history:
        print("  ❌ קיין היסטאָריע")
        return

    last = history[-20:]
    last.reverse()
    items = [f"📜 לעצטע {len(last)} דאַונלאָודס", "─" * 46]
    for h in last:
        items.append(f"  {h.get('time', '')}  {h.get('file', '?')}")
        if h.get("artist"):
            items.append(f"    {h.get('artist', '')} — {h.get('album', '')}")
    items += ["─" * 46, "0  → צוריק"]
    box("📜 היסטאָריע", items)
    prompt("» ")


def show_token_menu():
    valid, minutes = check_token_expiry(get_token())
    items = []
    if valid:
        t_str = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
        sym = "⚠️" if minutes < 30 else "✅"
        items.append(f"{sym} טאָקען גילטיק — {t_str}")
    else:
        items.append("❌ טאָקען אויסגעלאָפן!")
    items += ["─" * 46, "1. פּעיסט נייעם טאָקען", "0  → צוריק"]
    box("🔑 טאָקען", items)
    ch = prompt("» ")
    if ch == "1":
        new_t = prompt("נייער JWT: ")
        if new_t and new_t not in ("exit", "back", "0", ""):
            if current_user:
                accounts = load_accounts()
                accounts[current_user["name"]]["token"] = new_t.strip()
                save_accounts(accounts)
                current_user["token"] = new_t.strip()
            v, m = check_token_expiry(new_t)
            print(f"  ✅ געשפּאָרט!" + (f" גילטיק {m // 60}h" if v else " ⚠️ זעט אויסגעלאָפן אויס"))


# ─── ADMIN ────────────────────────────────────────────
def admin_panel():
    if not is_admin():
        print("  🚫 נאָר אדמינס")
        return

    while True:
        accounts = load_accounts()
        items = ["👥 אַלע אַקאַונטס:", "─" * 46]
        names = list(accounts.keys())
        for i, name in enumerate(names, 1):
            acc = accounts[name]
            valid, minutes = check_token_expiry(acc.get("token", ""))
            t_sym = "✅" if valid and minutes > 30 else ("⚠️" if valid else "❌")
            admin_tag = " 👑" if acc.get("is_admin") else ""
            last = acc.get("last_login", "?")
            items.append(f"{i:2d}. {name}{admin_tag} | {t_sym} | last: {last}")

        # Activity summary
        try:
            if os.path.exists(ACTIVITY_LOG):
                with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
                    logs = json.load(f)
                downloads = [l for l in logs if l.get("action") == "download"]
                items += ["─" * 46, f"📊 סה״כ דאַונלאָודס אין לאָג: {len(downloads)}"]
                # Per user breakdown
                user_counts = {}
                for l in downloads:
                    u = l.get("user", "?")
                    user_counts[u] = user_counts.get(u, 0) + 1
                for u, c in sorted(user_counts.items(), key=lambda x: -x[1]):
                    items.append(f"   {u}: {c} דאַונלאָודס")
        except Exception:
            pass

        items += ["─" * 46,
                  "users  → 👥 סערווער באַניצערס (Firebase)",
                  "add    → נייער אַקאַונט",
                  "del #  → מעק אַקאַונט",
                  "log    → אַקטיוויטעט לאָג",
                  "setup  → 🔧 סעטאַפּ מחדש",
                  "#      → זע אַקאַונט דעטאַלן",
                  "0      → צוריק"]

        box("👑 אדמין פּאַנעל", items)
        ch = prompt("» ")

        if ch in ("0", "back", ""):
            return
        elif ch == "users":
            show_server_users()
        elif ch == "add":
            name = prompt("נאָמען: ")
            if not name or name in ("exit", ""):
                continue
            token = prompt("JWT טאָקען: ")
            if not token or token in ("exit", ""):
                continue
            make_admin = prompt("אדמין? (y/n): ").lower() == "y"
            create_account(name, token, make_admin)
            print(f"  ✅ {name} באַשאַפן!")
            log_activity("admin_create_account", name)
        elif ch.startswith("del "):
            try:
                idx = int(ch[4:].strip()) - 1
                if 0 <= idx < len(names):
                    target = names[idx]
                    if target == current_user["name"]:
                        print("  ❌ קענסט נישט מעקן דיך אליין")
                    else:
                        del accounts[target]
                        save_accounts(accounts)
                        print(f"  🗑️ {target} אויסגעמעקט")
                        log_activity("admin_delete_account", target)
            except (ValueError, IndexError):
                pass
        elif ch == "log":
            show_activity_log()
        elif ch == "setup":
            rerun_setup()
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(names):
                    show_account_details(names[idx])
            except (ValueError, IndexError):
                pass




# ─── FIREBASE ADMIN ───────────────────────────────────
FIREBASE_KEY_FILE = os.path.join(APP_DIR, "firebase-key.json")
FIREBASE_PROJECT = "admob-app-id-3549225188"
_firebase_app = None


def init_firebase():
    """Initialize Firebase Admin SDK."""
    global _firebase_app
    if _firebase_app:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        print("  ❌ firebase-admin פעלט!")
        print("     אינסטאליר: ☰ → Pip → 'firebase-admin' → Install")
        return False

    if not os.path.exists(FIREBASE_KEY_FILE):
        print("  ❌ Firebase service account key נישט געפֿונען.")
        print(f"     לייג דעם JSON פֿײַל דאָ: {FIREBASE_KEY_FILE}")
        print()
        print("  📋 ווי צו באַקומען דעם קי:")
        print("     1. גיי צו Firebase Console → Project Settings")
        print("     2. Service Accounts → Generate New Private Key")
        print("     3. קאָפּיר דעם JSON פֿײַל צו:")
        print(f"        {FIREBASE_KEY_FILE}")
        print()
        # Offer to paste it
        ch = prompt("פּעיסט דעם JSON אינהאַלט (אָדער 0): ")
        if ch and ch != "0":
            try:
                key_data = json.loads(ch)
                with open(FIREBASE_KEY_FILE, "w") as f:
                    json.dump(key_data, f)
                print("  ✅ קי געשפּאָרט!")
            except json.JSONDecodeError:
                print("  ❌ דאָס איז נישט גילטיקער JSON")
                return False

    if not os.path.exists(FIREBASE_KEY_FILE):
        return False

    try:
        cred = credentials.Certificate(FIREBASE_KEY_FILE)
        _firebase_app = firebase_admin.initialize_app(cred)
        return True
    except Exception as e:
        print(f"  ❌ Firebase init שגיאה: {e}")
        return False


def firebase_list_users():
    """List all Firebase Auth users."""
    if not init_firebase():
        return None
    from firebase_admin import auth

    all_users = []
    try:
        page = auth.list_users()
        while page:
            for user in page.users:
                all_users.append({
                    "uid": user.uid,
                    "email": user.email or "",
                    "name": user.display_name or "",
                    "photo": user.photo_url or "",
                    "phone": user.phone_number or "",
                    "disabled": user.disabled,
                    "email_verified": user.email_verified,
                    "created": datetime.fromtimestamp(user.user_metadata.creation_timestamp / 1000).strftime("%Y-%m-%d %H:%M") if user.user_metadata.creation_timestamp else "?",
                    "last_login": datetime.fromtimestamp(user.user_metadata.last_sign_in_timestamp / 1000).strftime("%Y-%m-%d %H:%M") if user.user_metadata.last_sign_in_timestamp else "?",
                    "providers": [p.provider_id for p in user.provider_data] if user.provider_data else [],
                })
            page = page.get_next_page()
    except Exception as e:
        print(f"  ❌ שגיאה: {e}")
        return None
    return all_users


def firebase_create_user():
    """Create a new Firebase Auth user."""
    if not init_firebase():
        return
    from firebase_admin import auth

    email = prompt("אימייל: ")
    if not email or email in ("exit", "back", "0", ""):
        return
    password = prompt("פּאַסוואָרט (מינימום 6): ")
    if not password or len(password) < 6:
        print("  ❌ פּאַסוואָרט מוז זיין מינימום 6 תווים")
        return
    name = prompt("נאָמען (אָדער Enter): ")

    try:
        kwargs = {"email": email, "password": password, "email_verified": False}
        if name:
            kwargs["display_name"] = name
        user = auth.create_user(**kwargs)
        print(f"  ✅ באַניצער באַשאַפן!")
        print(f"     UID: {user.uid}")
        print(f"     אימייל: {email}")
        log_activity("firebase_create_user", email)
    except Exception as e:
        print(f"  ❌ שגיאה: {e}")


def firebase_disable_user(uid, disable=True):
    """Enable or disable a Firebase Auth user."""
    if not init_firebase():
        return
    from firebase_admin import auth
    try:
        auth.update_user(uid, disabled=disable)
        status = "דיסעיבלד" if disable else "ענעיבלד"
        print(f"  ✅ באַניצער {status}!")
        log_activity("firebase_disable_user" if disable else "firebase_enable_user", uid)
    except Exception as e:
        print(f"  ❌ שגיאה: {e}")


def firebase_delete_user(uid):
    """Delete a Firebase Auth user."""
    if not init_firebase():
        return
    from firebase_admin import auth
    try:
        auth.delete_user(uid)
        print(f"  ✅ באַניצער אויסגעמעקט!")
        log_activity("firebase_delete_user", uid)
    except Exception as e:
        print(f"  ❌ שגיאה: {e}")


def firebase_reset_password(email):
    """Generate password reset link."""
    if not init_firebase():
        return
    from firebase_admin import auth
    try:
        link = auth.generate_password_reset_link(email)
        print(f"  ✅ פּאַסוואָרט רעסעט לינק:")
        print(f"     {link}")
        log_activity("firebase_reset_password", email)
    except Exception as e:
        print(f"  ❌ שגיאה: {e}")


def show_server_users():
    """Display and manage all Firebase Auth users."""
    print("\n  ⏳ לאָדט באַניצערס פון Firebase...")
    users = firebase_list_users()

    if users is None:
        return

    if not users:
        print("  ❌ קיין באַניצערס")
        return

    page = 0
    per_page = 10

    while True:
        total_pages = (len(users) - 1) // per_page + 1
        start = page * per_page
        batch = users[start:start + per_page]

        items = [f"👥 {len(users)} באַניצערס אין Firebase", "─" * 46]
        for i, u in enumerate(batch, start + 1):
            name = u.get("name") or u.get("email", "?")
            status = "🚫" if u.get("disabled") else "✅"
            items.append(f"{i:3d}. {status} {name}")
            if u.get("email") and u["email"] != name:
                items.append(f"      {u['email']}")

        items.append("─" * 46)
        if total_pages > 1:
            items.append(f"  בלאט {page + 1}/{total_pages}  (n/p)")
        items += [
            "add     → באַשאַף נייער באַניצער",
            "#       → דעטאַלן + אָפּציעס",
            "refresh → לאָד פֿריש",
            "0       → צוריק",
        ]

        box("👥 Firebase באַניצערס", items)
        ch = prompt("» ")

        if ch in ("0", "back", ""):
            return
        elif ch == "n" and page < total_pages - 1:
            page += 1
        elif ch == "p" and page > 0:
            page -= 1
        elif ch == "add":
            firebase_create_user()
            users = firebase_list_users() or []
        elif ch == "refresh":
            print("  ⏳ רעפרעשט...")
            users = firebase_list_users() or []
        else:
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(users):
                    result = show_firebase_user_details(users[idx])
                    if result == "refresh":
                        users = firebase_list_users() or []
            except (ValueError, IndexError):
                pass


def show_firebase_user_details(user):
    """Show detailed info + management for a Firebase user."""
    name = user.get("name") or user.get("email", "?")
    disabled = user.get("disabled", False)

    while True:
        items = [
            f"👤 {name}",
            "─" * 46,
            f"UID:          {user.get('uid', '?')}",
            f"אימייל:       {user.get('email', '—')}",
            f"נאָמען:        {user.get('name', '—')}",
            f"טעלעפאָן:      {user.get('phone', '—')}",
            f"באשאפן:       {user.get('created', '?')}",
            f"לעצטע לאָגין:  {user.get('last_login', '?')}",
            f"וועריפֿיצירט:  {'✅' if user.get('email_verified') else '❌'}",
            f"סטאַטוס:       {'🚫 דיסעיבלד' if disabled else '✅ אַקטיוו'}",
            f"פּראָוויידערס:  {', '.join(user.get('providers', []))}",
        ]
        if user.get("photo"):
            items.append(f"בילד:         {user['photo'][:40]}...")

        items += [
            "─" * 46,
            "dis  → דיסעיבל/ענעיבל" if not disabled else "en   → ענעיבל צוריק",
            "del  → מעק באַניצער ⚠️",
            "pass → פּאַסוואָרט רעסעט לינק",
            "0    → צוריק",
        ]

        box(f"👤 {name}", items)
        ch = prompt("» ")

        if ch in ("0", "back", ""):
            return
        elif ch == "dis" and not disabled:
            confirm = prompt("ביסט זיכער? (y/n): ")
            if confirm.lower() == "y":
                firebase_disable_user(user["uid"], True)
                user["disabled"] = True
                disabled = True
                return "refresh"
        elif ch == "en" and disabled:
            firebase_enable_ok = prompt("ענעיבלן? (y/n): ")
            if firebase_enable_ok.lower() == "y":
                firebase_disable_user(user["uid"], False)
                user["disabled"] = False
                disabled = False
                return "refresh"
        elif ch == "del":
            confirm = prompt("⚠️ מעקן פֿאַר אייביק! (שרייב DELETE): ")
            if confirm == "DELETE":
                firebase_delete_user(user["uid"])
                return "refresh"
            else:
                print("  ↩️ באטל")
        elif ch == "pass":
            if user.get("email"):
                firebase_reset_password(user["email"])
            else:
                print("  ❌ קיין אימייל פֿאַר דעם באַניצער")


def show_account_details(username):
    acc = accounts[username]
    valid, minutes = check_token_expiry(acc.get("token", ""))

    items = [
        f"👤 {username}" + (" 👑" if acc.get("is_admin") else ""),
        "─" * 46,
        f"באשאפן:    {acc.get('created', '?')}",
        f"לעצטע לאָגין: {acc.get('last_login', '?')}",
    ]
    if valid:
        items.append(f"טאָקען:    ✅ {minutes // 60}h {minutes % 60}m")
    else:
        items.append("טאָקען:    ❌ אויסגעלאָפן")

    # User's history
    hf = get_user_file(username, "history.json")
    if os.path.exists(hf):
        try:
            with open(hf, "r", encoding="utf-8") as f:
                history = json.load(f)
            items += ["─" * 46, f"📜 {len(history)} דאַונלאָודס סה״כ"]
            for h in history[-5:]:
                items.append(f"  {h.get('time', '')} {h.get('file', '?')}")
        except Exception:
            pass

    # User's favorites
    ff = get_user_file(username, "favorites.json")
    if os.path.exists(ff):
        try:
            with open(ff, "r", encoding="utf-8") as f:
                favs = json.load(f)
            fa = len(favs.get("artists", []))
            fb = len(favs.get("albums", []))
            items.append(f"⭐ {fa} פאַוו. זינגערס, {fb} פאַוו. אלבומס")
        except Exception:
            pass

    box(f"👤 {username}", items)
    prompt("Enter → צוריק ")


def show_activity_log():
    """Show full activity log (admin only)."""
    if not os.path.exists(ACTIVITY_LOG):
        print("  ❌ קיין לאָג")
        return
    try:
        with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
            logs = json.load(f)
    except Exception:
        print("  ❌ קען נישט לייענען")
        return
    if not logs:
        print("  ❌ ליידיק")
        return

    page = 0
    per_page = 15
    logs_rev = list(reversed(logs))

    while True:
        total_pages = (len(logs_rev) - 1) // per_page + 1
        start = page * per_page
        batch = logs_rev[start:start + per_page]
        items = [f"📜 {len(logs)} אַקטיוויטעטן", "─" * 46]
        for l in batch:
            icon = {"download": "⬇", "play": "▶️", "favorite_artist": "⭐",
                    "favorite_album": "⭐", "admin_create_account": "➕",
                    "admin_delete_account": "🗑️"}.get(l.get("action", ""), "•")
            items.append(f"{icon} {l.get('time', '')} [{l.get('user', '?')}]")
            items.append(f"  {l.get('action', '')}: {l.get('details', '')[:35]}")
        items.append("─" * 46)
        if total_pages > 1:
            items.append(f"  בלאט {page + 1}/{total_pages} (n/p)")
        items.append("0  → צוריק")
        box("📜 אַקטיוויטעט לאָג", items)
        ch = prompt("» ")
        if ch in ("0", "back", ""):
            return
        elif ch == "n" and page < total_pages - 1:
            page += 1
        elif ch == "p" and page > 0:
            page -= 1


# ─── LOGIN / SETUP ────────────────────────────────────
def first_setup():
    """First time setup - auto-create admin from existing token + ask for more."""
    clear()

    # Auto-create account from the hardcoded token
    token_name = None
    try:
        parts = DEFAULT_TOKEN.strip().split(".")
        if len(parts) == 3:
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            decoded = json.loads(base64.urlsafe_b64decode(payload))
            token_name = decoded.get("name") or decoded.get("email", "").split("@")[0]
    except Exception:
        pass

    if token_name and DEFAULT_TOKEN.strip() != "PASTE_TOKEN_HERE":
        create_account(token_name, DEFAULT_TOKEN.strip(), is_admin=True)
        print(f"  ✅ אַקאַונט '{token_name}' אויטאָמאַטיש באַשאַפן פון דעם אריגינעלן טאָקען (👑 אדמין)")
        login(token_name)
    else:
        box("ברוכים הבאים!", [
            "ערשטע מאָל? לאָמיר אויפשטעלן.",
            "─" * 46,
        ])
        name = prompt("דיין נאָמען: ")
        if not name:
            name = "Admin"
        token = prompt("JWT טאָקען: ")
        if not token:
            token = "PASTE_TOKEN_HERE"
        create_account(name, token, is_admin=True)
        login(name)
        print(f"\n  ✅ אַקאַונט '{name}' באַשאַפן (👑 אדמין)!")

    # Ask if more accounts to add now
    print()
    while True:
        add = prompt("צולייגן נאָך אַן אַקאַונט? (y/n): ").lower()
        if add not in ("y", "yes"):
            break
        name2 = prompt("נאָמען: ")
        if not name2 or name2 in ("exit", "back"):
            break
        token2 = prompt("JWT טאָקען: ")
        if not token2 or token2 in ("exit", "back"):
            break
        create_account(name2, token2, is_admin=False)
        print(f"  ✅ '{name2}' באַשאַפן!")

    return True


def login_screen():
    """Show login/account selection."""
    accounts = load_accounts()
    if not accounts:
        return first_setup()

    names = list(accounts.keys())

    if len(names) == 1:
        login(names[0])
        print(f"  👤 שלום, {names[0]}!")
        return True

    items = []
    for i, name in enumerate(names, 1):
        admin_tag = " 👑" if accounts[name].get("is_admin") else ""
        items.append(f"{i}. {name}{admin_tag}")
    items += ["─" * 46, "add → נייער אַקאַונט"]

    box("👤 וועלכע אַקאַונט?", items)
    ch = prompt("» ")

    if ch == "add":
        name = prompt("נאָמען: ")
        if not name:
            return False
        token = prompt("JWT טאָקען: ")
        if not token:
            return False
        create_account(name, token)
        login(name)
        print(f"  ✅ {name} באַשאַפן!")
        return True
    else:
        try:
            idx = int(ch) - 1
            if 0 <= idx < len(names):
                login(names[idx])
                print(f"  👤 שלום, {names[idx]}!")
                return True
        except ValueError:
            # Try by name
            if ch in names:
                login(ch)
                return True
    return False


# ─── MAIN ─────────────────────────────────────────────
CONFIG_FILE = os.path.join(APP_DIR, "config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def one_time_setup():
    """First-time setup wizard. Saves everything so it never asks again."""
    clear()
    box("🔧 סעטאַפּ (איין מאָל)", [
        "לאָמיר אויפשטעלן אַלעס.",
        "נאָכדעם וועט עס זיך קאָנעקטן אַליין.",
        "─" * 46,
    ])

    cfg = load_config()

    # 1. Server URL
    print(f"\n  📡 סערווער URL: {API_URL}")
    custom = prompt("Enter = באַהאַלטן / אָדער פּעיסט נייעם URL: ")
    cfg["api_url"] = custom.strip() if custom else API_URL
    cfg["audio_api"] = cfg["api_url"].replace("/graphql", "/api/audio-file")

    # 2. Token from hardcoded default
    token_name = None
    try:
        parts = DEFAULT_TOKEN.strip().split(".")
        payload = parts[1] + "=="
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        token_name = decoded.get("name") or decoded.get("email", "").split("@")[0]
    except Exception:
        pass

    if token_name and DEFAULT_TOKEN.strip() != "PASTE_TOKEN_HERE":
        cfg["default_token"] = DEFAULT_TOKEN.strip()
        cfg["default_user"] = token_name
        create_account(token_name, DEFAULT_TOKEN.strip(), is_admin=True)
        print(f"  ✅ אַקאַונט '{token_name}' באַשאַפן (👑 אדמין)")
    else:
        name = prompt("דיין נאָמען: ") or "Admin"
        token = prompt("JWT טאָקען: ") or "PASTE_TOKEN_HERE"
        cfg["default_token"] = token
        cfg["default_user"] = name
        create_account(name, token, is_admin=True)

    # 3. Firebase key
    print()
    print("  🔥 Firebase Admin — צו זען/מאַכן/מעקן אַקאַונטס")
    print("     דאַרפֿסט דעם Service Account Key JSON")
    print("     Firebase Console → Project Settings → Service Accounts")
    print("     → Generate New Private Key → קאָפּיר דעם אינהאַלט")
    print()
    fb_key = prompt("פּעיסט דעם JSON (אָדער Enter צו שקיפּן): ")
    if fb_key and fb_key.strip().startswith("{"):
        try:
            key_data = json.loads(fb_key)
            with open(FIREBASE_KEY_FILE, "w") as f:
                json.dump(key_data, f)
            cfg["firebase_setup"] = True
            cfg["firebase_project"] = key_data.get("project_id", FIREBASE_PROJECT)
            print("  ✅ Firebase קי געשפּאָרט!")
        except json.JSONDecodeError:
            print("  ⚠️ JSON נישט גילטיק — שקיפּט Firebase פֿאָרלויפֿיק")
            cfg["firebase_setup"] = False
    else:
        cfg["firebase_setup"] = False
        print("  ℹ️ קענסט שפּעטער צולייגן דורך admin → setup")

    # 4. Extra accounts
    print()
    while True:
        add = prompt("צולייגן נאָך אַן אַקאַונט? (y/n): ").lower()
        if add not in ("y", "yes"):
            break
        name2 = prompt("נאָמען: ")
        if not name2 or name2 in ("exit", "back"):
            break
        token2 = prompt("JWT טאָקען: ")
        if not token2 or token2 in ("exit", "back"):
            break
        create_account(name2, token2, is_admin=False)
        print(f"  ✅ '{name2}' באַשאַפן!")

    cfg["setup_done"] = True
    cfg["setup_date"] = now_str()
    save_config(cfg)

    print()
    print("  ═══════════════════════════════════════════")
    print("  ✅ סעטאַפּ פֿאַרטיק! פון יעצט אָן עפֿנסט")
    print("     דעם סקריפּט און אַלעס קאָנעקט אַליין.")
    print("  ═══════════════════════════════════════════")
    return True


def auto_connect():
    """Auto-connect to everything on startup. No questions."""
    global API_URL, AUDIO_API_BASE

    cfg = load_config()

    # Apply saved config
    if cfg.get("api_url"):
        API_URL = cfg["api_url"]
    if cfg.get("audio_api"):
        AUDIO_API_BASE = cfg["audio_api"]

    # Auto-login
    accounts = load_accounts()
    if not accounts:
        return False

    names = list(accounts.keys())
    # Find admin account, or use first
    admin_name = None
    for n in names:
        if accounts[n].get("is_admin"):
            admin_name = n
            break
    login(admin_name or names[0])

    return True


def show_startup_dashboard():
    """Show connection status dashboard on startup."""
    cfg = load_config()
    valid, minutes = check_token_expiry(get_token())

    items = [
        f"👤 {current_user['name']}" + (" 👑" if is_admin() else ""),
        "─" * 46,
        f"📡 סערווער:   {API_URL[:40]}",
    ]

    # Token status
    if valid:
        t_str = f"{minutes // 60}h {minutes % 60}m"
        sym = "⚠️" if minutes < 30 else "✅"
        items.append(f"🔑 טאָקען:    {sym} {t_str}")
    else:
        items.append("🔑 טאָקען:    ❌ אויסגעלאָפן!")

    # Firebase status
    if cfg.get("firebase_setup") and os.path.exists(FIREBASE_KEY_FILE):
        items.append("🔥 Firebase:  ✅ פאַרבונדן")
    else:
        items.append("🔥 Firebase:  ❌ נישט קאָנפיגורירט")

    # Accounts count
    accounts = load_accounts()
    items.append(f"👥 אַקאַונטס:  {len(accounts)} לאָקאַל")

    # Download stats
    total_files = 0
    total_size = 0
    if os.path.exists(DOWNLOAD_DIR):
        for root, dirs, files in os.walk(DOWNLOAD_DIR):
            for fn in files:
                if fn.endswith((".mp3", ".wav", ".m4a", ".flac")):
                    total_files += 1
                    total_size += os.path.getsize(os.path.join(root, fn))
    items.append(f"🎵 לידער:     {total_files} ({format_size(total_size)})")

    items.append("─" * 46)
    box("Jewish Music Downloader v3", items)


def rerun_setup():
    """Allow re-running setup from admin menu."""
    cfg = load_config()

    box("🔧 סעטאַפּ מחדש", [
        "1. 📡 בייט סערווער URL",
        "2. 🔥 צוליגן/בייטן Firebase קי",
        "3. 🔑 בייט דיין טאָקען",
        "4. 👥 צוליגן אַקאַונט",
        "─" * 46,
        "0  → צוריק",
    ])
    ch = prompt("» ")

    if ch == "1":
        new_url = prompt(f"נייער URL (יעצט: {cfg.get('api_url', API_URL)}): ")
        if new_url and new_url.startswith("http"):
            cfg["api_url"] = new_url.strip()
            cfg["audio_api"] = new_url.strip().replace("/graphql", "/api/audio-file")
            save_config(cfg)
            print("  ✅ URL אַפּדעיטעד!")
    elif ch == "2":
        fb_key = prompt("פּעיסט דעם Firebase JSON: ")
        if fb_key and fb_key.strip().startswith("{"):
            try:
                key_data = json.loads(fb_key)
                with open(FIREBASE_KEY_FILE, "w") as f:
                    json.dump(key_data, f)
                cfg["firebase_setup"] = True
                cfg["firebase_project"] = key_data.get("project_id", "")
                save_config(cfg)
                # Reset firebase app so it reinitializes
                global _firebase_app
                _firebase_app = None
                print("  ✅ Firebase קי אַפּדעיטעד!")
            except json.JSONDecodeError:
                print("  ❌ JSON נישט גילטיק")
    elif ch == "3":
        new_t = prompt("נייער JWT טאָקען: ")
        if new_t and current_user:
            accounts = load_accounts()
            accounts[current_user["name"]]["token"] = new_t.strip()
            save_accounts(accounts)
            current_user["token"] = new_t.strip()
            print("  ✅ טאָקען אַפּדעיטעד!")
    elif ch == "4":
        name = prompt("נאָמען: ")
        if name:
            token = prompt("JWT טאָקען: ")
            if token:
                admin = prompt("אדמין? (y/n): ").lower() == "y"
                create_account(name, token, admin)
                print(f"  ✅ {name} באַשאַפן!")


def main():
    global API_URL, AUDIO_API_BASE
    clear()

    cfg = load_config()

    # First time? Run setup wizard
    if not cfg.get("setup_done"):
        if not one_time_setup():
            return
        cfg = load_config()

    # Auto-connect everything
    if not auto_connect():
        print("  ❌ קיין אַקאַונטס. לויפֿט סעטאַפּ...")
        one_time_setup()
        if not auto_connect():
            return

    # Auto-init Firebase silently
    if cfg.get("firebase_setup") and os.path.exists(FIREBASE_KEY_FILE):
        try:
            init_firebase()
        except Exception:
            pass

    # Show dashboard
    show_startup_dashboard()

    # Load artists
    artists = fetch_artists()
    if not artists:
        print("  ⚠️ קיין זינגערס געלאָדן (טשעק אינטערנעט)")
        artists = []

    log_activity("login")

    while True:
        menu_items = [
            f"👤 {current_user['name']}" + (" 👑" if is_admin() else ""),
            "─" * 46,
            "🔍  s      →  זוכן (זינגער/אלבום/ליד)",
            "🔥  new    →  נייע רילעסעס",
            "⭐  fav    →  פאַוואָריטן",
            "📋  pl     →  פּלייליסטן",
            "▶️  play   →  שפּילער",
            "📜  hist   →  דאַונלאָוד היסטאָריע",
            "📊  stats  →  סטאַטיסטיק",
            "🔑  token  →  טאָקען",
            "📂  dir    →  פאָלדער",
            "🔄  clear  →  רעפרעש cache",
        ]
        if is_admin():
            menu_items.append("👑  admin  →  אדמין פּאַנעל")
        menu_items += ["🔀  switch →  ביטע אַקאַונט",
                       "🔧  setup  →  סעטאַפּ / סערווער קאָנפיג",
                       "🚪  exit   →  אַרויס"]

        box("הויפט מעניו", menu_items)
        ch = prompt("» ")
        if not ch:
            continue
        cmd = ch.lower()

        if cmd == "exit":
            log_activity("logout")
            print("\n  👋 זײַ געזונט!\n")
            break
        elif cmd in ("s", "search"):
            show_search_menu(artists)
        elif cmd == "new":
            show_new_releases()
        elif cmd in ("fav", "favorites"):
            show_favorites()
        elif cmd in ("pl", "playlist"):
            show_playlists()
        elif cmd in ("play", "player"):
            show_player()
        elif cmd in ("hist", "history"):
            show_history()
        elif cmd in ("stats", "stat"):
            show_stats()
        elif cmd == "token":
            show_token_menu()
        elif cmd == "admin" and is_admin():
            admin_panel()
        elif cmd in ("switch", "sw"):
            if login_screen():
                log_activity("login")
        elif cmd == "setup":
            rerun_setup()
        elif cmd == "dir":
            d = ensure_download_dir()
            files = os.listdir(d) if os.path.exists(d) else []
            if files:
                print(f"\n  📂 {d}:")
                for f in sorted(files):
                    icon = "📁" if os.path.isdir(os.path.join(d, f)) else "🎵"
                    print(f"     {icon} {f}")
            else:
                print("  📂 ליידיק")
        elif cmd == "clear":
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
                print("  🔄 cache אויסגעמעקט")
            else:
                print("  ℹ️ קיין cache")
        else:
            if not artists:
                print("  ❌ קיין זינגערס געלאָדן")
                continue
            results = sorted([a for a in artists if cmd in (a.get("enName") or "").lower()
                              or cmd in (a.get("heName") or "").lower()],
                             key=lambda x: x.get("enName", ""))
            if results:
                show_list_and_select(results, "זינגערס", lambda a: show_artist(a),
                                     lambda a: f"{a.get('enName', '?')}" + (f" ({a['heName']})" if a.get("heName") else ""))
            else:
                print(f"  ❌ '{ch}' — פּרובירט 's' פאר מער זוכונג")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  👋 זײַ געזונט!\n")
    except Exception:
        traceback.print_exc()
