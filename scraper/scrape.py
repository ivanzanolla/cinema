"""Scarica la programmazione dei cinema e genera site/data.json.

Fonti:
  - The Space Silea          -> API JSON del sito
  - Cinergia Conegliano      -> 18tickets (HTML)
  - La petite Lumiere Belluno -> WordPress + Webtic (HTML)
  - Multisala Verdi Vittorio Veneto -> WordPress cineservice (admin-ajax)
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data.json"
IMDB_CACHE = ROOT / "data" / "imdb-cache.json"
PREVIOUS_URL = "https://ivanzanolla.github.io/cinema/data.json"

TZ = ZoneInfo("Europe/Rome")
TODAY = datetime.now(TZ).date()
DAYS_AHEAD = 10

TIMEOUT = 30
session = requests.Session()
session.headers.update({
    "User-Agent": "InSala/1.0 (aggregatore personale programmazione cinema; +https://github.com/ivanzanolla/cinema)",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
})
_req = session.request
session.request = lambda m, u, **kw: _req(m, u, **{"timeout": TIMEOUT, **kw})


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# intervallo minimo tra richieste allo stesso host (cortesia + rate limit dei siti)
MIN_INTERVAL = {"coneglianocinergia.18tickets.it": 2.5}
_last_hit = {}
_lock = threading.Lock()


def _throttle(url):
    host = urlparse(url).netloc
    gap = MIN_INTERVAL.get(host, 0.3)
    with _lock:
        wait = _last_hit.get(host, 0) + gap - time.monotonic()
        _last_hit[host] = max(time.monotonic(), _last_hit.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def request(method, url, **kw):
    for attempt in range(4):
        _throttle(url)
        try:
            r = session.request(method, url, **kw)
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            log(f"  retry {url}: {e}")
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r
        log(f"  HTTP {r.status_code} {url}")
        if r.status_code == 429 and attempt < 3:
            ra = r.headers.get("Retry-After", "")
            time.sleep(min(int(ra) if ra.isdigit() else 20 * (attempt + 1), 90))
            continue
        if r.status_code in (202, 403, 404, 429):
            raise RuntimeError(f"HTTP {r.status_code}: il sito non accetta richieste automatiche da qui")
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"failed {url}")


def get(url, **kw):
    return request("GET", url, **kw)


def soup(html):
    return BeautifulSoup(html, "html.parser")


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


# ---------------------------------------------------------------- helpers

MONTHS = {m: i for i, m in enumerate(
    ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
     "agosto", "settembre", "ottobre", "novembre", "dicembre"], 1)}


def parse_minutes(s):
    if not s:
        return None
    if isinstance(s, (int, float)):
        return int(s) or None
    s = s.lower()
    h = re.search(r"(\d+)\s*(?:h|or[ae])", s)
    m = re.search(r"(\d+)\s*(?:min|m\b|minut)", s)
    if h or m:
        return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0) or None
    n = re.search(r"\d+", s)
    return int(n.group()) if n else None


def split_people(s, limit=None):
    if not s:
        return []
    s = re.sub(r"\([^)]*\)", "", s)
    s = s.replace("...", "").replace("…", "")
    out = [p.strip(" .,") for p in re.split(r",|;|\be\b(?=\s+[A-Z])", s)]
    out = [p for p in out if p and len(p) > 1]
    return out[:limit] if limit else out


VOS_RE = re.compile(r"\(?\s*v\.?\s*o\.?\s*s\.?\s*\)?|\[\s*sub[^\]]*\]|\bsub\s+eng\b|\bversione originale\b", re.I)
NOISE_RE = re.compile(
    r"\b(riediz(ione)?|anteprima|preview|ed\.?\s*rest\.?|restaurat[oa]|4k|3d|2d)\b\.?", re.I)


def is_vos(title):
    return bool(VOS_RE.search(title or ""))


def clean_title(title):
    t = VOS_RE.sub(" ", title or "")
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    t = NOISE_RE.sub(" ", t)
    t = re.sub(r"\s+[-–]\s*$", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip(" -–:")
    return t


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def key_for(title):
    return norm(clean_title(title))


def show(cinema, d: date, hhmm, url, room=None, tags=None, sold_out=False):
    return {"cinema": cinema, "date": d.isoformat(), "time": hhmm, "url": url,
            "room": room or None, "tags": sorted(set(t for t in (tags or []) if t)),
            "soldOut": bool(sold_out)}


def window(d):
    return TODAY <= d <= TODAY + timedelta(days=45)


# ---------------------------------------------------------------- The Space

def scrape_thespace():
    cid = "space"
    base = "https://www.thespacecinema.it"
    cinema_id = "1009"
    hdr = {"Accept": "application/json", "Referer": f"{base}/cinema/silea/al-cinema"}
    dates = get(f"{base}/api/microservice/showings/showingDates?cinemaId={cinema_id}"
                f"&minEmbargoLevel=2&forNextWeek=true", headers=hdr).json()["result"]
    films = {}
    for d in dates:
        if not d.get("hasShowings"):
            continue
        day = d["showingDate"][:10]
        if date.fromisoformat(day) > TODAY + timedelta(days=DAYS_AHEAD):
            continue
        data = get(f"{base}/api/microservice/showings/cinemas/{cinema_id}/films?showingDate={day}T00:00:00"
                   f"&minEmbargoLevel=3&includesSession=true&includeSessionAttributes=true",
                   headers=hdr).json()["result"]
        for f in data:
            fid = f["filmId"]
            film = films.get(fid)
            if not film:
                film = films[fid] = {
                    "title": f.get("filmTitle"),
                    "originalTitle": f.get("originalTitle") or None,
                    "director": split_people(f.get("director")),
                    "cast": split_people(f.get("cast"), 8),
                    "runtime": f.get("runningTime") or None,
                    "genres": [g.capitalize() for g in f.get("genres") or []],
                    "poster": f.get("posterImageSrc"),
                    "plot": f.get("synopsisShort"),
                    "year": None,
                    "release": (f.get("releaseDate") or "")[:10] or None,
                    "rating": (f.get("certificate") or {}).get("name"),
                    "trailer": None,
                    "page": f.get("filmUrl"),
                    "shows": [],
                }
            for g in f.get("showingGroups") or []:
                for s in g.get("sessions") or []:
                    st = datetime.fromisoformat(s["startTime"])
                    tags = []
                    for a in s.get("attributes") or []:
                        n = (a.get("name") or "").strip()
                        if a.get("attributeType") == "Language":
                            if n and n.upper() != "ITALIANO":
                                tags.append("VOS" if "SOTT" in n.upper() or "ORIG" in n.upper() else n.title())
                        elif n and n.upper() not in ("2D",) and "LASER" not in n.upper():
                            tags.append(n)
                    film["shows"].append(show(
                        cid, st.date(), st.strftime("%H:%M"), urljoin(base, s.get("bookingUrl") or ""),
                        s.get("screenName"), tags, s.get("isSoldOut")))
    return list(films.values())


# ---------------------------------------------------------------- Cinergia (18tickets)

def scrape_cinergia():
    cid = "cinergia"
    base = "https://coneglianocinergia.18tickets.it"
    home = get(base + "/").text
    ids = set(re.findall(r"/film/(\d+)\?ref_date", home))
    days = sorted(set(re.findall(r"data-target=\\?[\"'](\d{4}-\d{2}-\d{2})", home)))
    for day in days:
        if date.fromisoformat(day) > TODAY + timedelta(days=DAYS_AHEAD) or day == TODAY.isoformat():
            continue
        frag = get(f"{base}/film/fetch_films?date={day}&technology=",
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Accept": "text/javascript, */*"}).text
        ids |= set(re.findall(r"/film/(\d+)\?ref_date", frag.replace("\\/", "/")))
    log(f"  cinergia: {len(ids)} film")

    def one(fid):
        url = f"{base}/film/{fid}"
        s = soup(get(url).text)
        opts = {}
        for p in s.select("p.movie__option"):
            lab = text(p.find("strong")).rstrip(":").strip().lower()
            val = text(p).replace(text(p.find("strong")), "", 1).strip()
            if len(val) > len(opts.get(lab, "")):
                opts[lab] = val
        title = text(s.select_one(".page-heading")) or (s.find("meta", property="og:title") or {}).get("content")
        poster = (s.find("meta", property="og:image") or {}).get("content")
        trailer = s.select_one("a.trailer-sample")
        plot = text(s.select_one("#plotPanel .card-body")) or (s.find("meta", property="og:description") or {}).get("content")
        vos = is_vos(title)
        shows = []
        for a in s.select("a.film-projection"):
            m = re.search(r"(\d{2})/(\d{2})/(\d{4}).*?(\d{1,2}):(\d{2})", a.get("data-date-extended", ""))
            if not m:
                continue
            d = date(int(m[3]), int(m[2]), int(m[1]))
            tags = ["VOS"] if vos else []
            notes = a.get("data-notes") or ""
            if "3d" in notes.lower():
                tags.append("3D")
            shows.append(show(cid, d, f"{int(m[4]):02d}:{m[5]}",
                              f"{url}/{a['data-id']}#theater-init", a.get("data-theater-name"), tags))
        yr = re.search(r"\d{4}", opts.get("anno", ""))
        return {
            "title": title,
            "originalTitle": None,
            "director": split_people(opts.get("regia")),
            "cast": split_people(opts.get("con"), 8),
            "runtime": parse_minutes(text(s.select_one(".movie__time"))),
            "genres": [g.strip() for g in opts.get("genere", "").split(",") if g.strip()],
            "poster": poster,
            "plot": plot,
            "year": int(yr.group()) if yr else None,
            "rating": opts.get("età") or opts.get("eta"),
            "trailer": trailer.get("href") if trailer else None,
            "page": url,
            "shows": shows,
        }

    return [f for f in map(one, sorted(ids)) if f["shows"]]


# ---------------------------------------------------------------- Lumiere (Webtic)

def it_date(label):
    m = re.search(r"(\d{1,2})\s+([a-zà]+)(?:\s+(\d{4}))?", label.lower())
    if not m or m[2] not in MONTHS:
        return None
    month, day = MONTHS[m[2]], int(m[1])
    year = int(m[3]) if m[3] else TODAY.year
    d = date(year, month, day)
    if not m[3] and d < TODAY - timedelta(days=60):
        d = date(year + 1, month, day)
    return d


def scrape_lumiere():
    cid = "lumiere"
    base = "https://cinemalumiere.it"
    urls = set()
    for page in ("/", "/programmazione/"):
        s = soup(get(base + page).text)
        for a in s.select(".wt-film-card > a, a.wt-link-scheda, a.wt-future-link"):
            if a.get("href", "").startswith(base):
                urls.add(a["href"])
    log(f"  lumiere: {len(urls)} film")

    def one(url):
        s = soup(get(url).text)
        meta = {}
        for it in s.select(".wt-metadata-item"):
            st = it.find("strong")
            if st:
                meta[text(st).rstrip(":").lower()] = text(it).replace(text(st), "", 1).strip()
        orig = None
        for st in s.find_all("strong"):
            if "titolo originale" in text(st).lower():
                orig = text(st.parent).replace(text(st), "", 1).strip() or None
                break
        plot_el = s.select_one(".wt-plot-section")
        plot = text(plot_el)
        plot = re.sub(r"^Trama\s*", "", plot)
        title = text(s.select_one("h1.wt-movie-title")) or text(s.find("h1"))
        poster = s.select_one("img.wt-locandina-img")
        yt = s.find("iframe", src=re.compile("youtube")) or s.find("a", href=re.compile(r"youtu\.?be"))
        trailer = (yt.get("src") or yt.get("href")) if yt else None
        if trailer and "/embed/" in trailer:
            trailer = "https://www.youtube.com/watch?v=" + trailer.split("/embed/")[1].split("?")[0]
        vos = is_vos(title) or "original" in meta.get("versione", "").lower()
        shows = []
        for g in s.select(".wt-day-group"):
            d = it_date(text(g.find("h4")))
            if not d:
                continue
            for a in g.select("a.wt-showtime-item"):
                tags = [text(t) for t in a.select(".wt-tag")]
                tags = [t for t in tags if t.upper() not in ("4K", "2D")]
                if vos:
                    tags.append("VOS")
                shows.append(show(cid, d, text(a.select_one(".wt-time")), a.get("href"),
                                  text(a.select_one(".wt-room-badge")).title() or None, tags,
                                  "sold" in " ".join(a.get("class", [])).lower()))
        yr = re.search(r"\d{4}", meta.get("anno", ""))
        return {
            "title": title,
            "originalTitle": orig,
            "director": split_people(meta.get("regia")),
            "cast": split_people(meta.get("cast"), 8),
            "runtime": parse_minutes(meta.get("durata")),
            "genres": [g.strip() for g in meta.get("genere", "").split(",") if g.strip()],
            "poster": poster.get("src") if poster else None,
            "plot": plot or None,
            "year": int(yr.group()) if yr else None,
            "rating": None,
            "trailer": trailer,
            "page": url,
            "shows": shows,
        }

    with ThreadPoolExecutor(4) as ex:
        return [f for f in ex.map(one, sorted(urls)) if f["shows"]]


# ---------------------------------------------------------------- Verdi (cineservice)

def scrape_verdi():
    cid = "verdi"
    base = "https://multisalaverdi.it"
    films = {}
    for i in range(DAYS_AHEAD + 1):
        d = TODAY + timedelta(days=i)
        r = request("POST", f"{base}/wp-admin/admin-ajax.php",
                    data={"action": "cs_ajax_projections_by_day", "date": d.strftime("%d/%m/%Y")},
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": base + "/"})
        html = r.json().get("data") or ""
        for row in soup(html).select(".projections-film-table-row"):
            a = row.select_one(".projections-film-table-title a")
            if not a:
                continue
            url = a["href"]
            f = films.setdefault(url, {"title": text(a), "page": url, "shows": [],
                                       "director": split_people(re.sub(r"^di\s+", "", text(row.select_one(".director")))),
                                       "runtime": parse_minutes(text(row.select_one(".duration"))),
                                       "genres": [g.strip() for g in text(row.select_one(".genres")).split(",") if g.strip()],
                                       "poster": None})
            for b in row.select("button.projections-film-timetable-hour-button"):
                f["poster"] = f["poster"] or (b.get("data-poster") or "").replace("http://", "https://") or None
                tag = (b.get("data-tag") or "").strip()
                tags = [tag] if tag else []
                if is_vos(f["title"]) or "v.o" in tag.lower():
                    tags.append("VOS")
                shows_url = b.get("data-purchase-link") or url
                f["shows"].append(show(cid, d, b.get("data-projection"), shows_url, None, tags))
    log(f"  verdi: {len(films)} film")

    def details(f):
        try:
            s = soup(get(f["page"]).text)
            c = s.select_one(".single-film-content-cast")
            f["cast"] = split_people(re.sub(r"^Con\s+", "", text(c)), 8) if c else []
            f["plot"] = text(s.select_one(".single-film-content-plot")) or None
            tb = s.select_one("[data-trailer-code]")
            f["trailer"] = ("https://www.youtube.com/" + tb["data-trailer-code"]) if tb else None
            dr = s.select_one(".single-film-content-director")
            if dr and not f["director"]:
                f["director"] = split_people(re.sub(r"^Di\s+", "", text(dr)))
            img = s.select_one(".single-film-image, img.wp-post-image")
            if not f["poster"] and img:
                f["poster"] = img.get("src")
        except Exception as e:  # noqa: BLE001
            log(f"  verdi details {f['page']}: {e}")
        f.setdefault("cast", [])
        f.setdefault("plot", None)
        f.setdefault("trailer", None)
        f.update({"originalTitle": None, "year": None, "rating": None})
        return f

    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(details, films.values()))


# ---------------------------------------------------------------- IMDb

class Imdb:
    def __init__(self):
        self.cache = {}
        if IMDB_CACHE.exists():
            try:
                self.cache = json.loads(IMDB_CACHE.read_text())
            except Exception:  # noqa: BLE001
                pass

    def save(self):
        IMDB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        IMDB_CACHE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0, sort_keys=True))

    def suggest(self, q):
        qn = norm(q)
        if not qn:
            return []
        ck = "q:" + qn
        if ck in self.cache:
            return self.cache[ck]
        try:
            r = session.get(f"https://v3.sg.media-imdb.com/suggestion/x/{quote(qn)}.json",
                            headers={"Accept": "application/json"})
            items = r.json().get("d", []) if r.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            log(f"  imdb {q}: {e}")
            items = None
        if items is None:
            return []
        slim = [{"id": i.get("id"), "l": i.get("l"), "y": i.get("y"), "q": i.get("qid"), "s": i.get("s")}
                for i in items]
        self.cache[ck] = slim
        return slim

    def person(self, name):
        for i in self.suggest(name):
            if (i["id"] or "").startswith("nm") and norm(i["l"]) == norm(name):
                return f"https://www.imdb.com/name/{i['id']}/"
        return f"https://www.imdb.com/find/?q={quote(name)}&s=nm"

    def title(self, titles, year, director):
        for t in [x for x in titles if x]:
            for i in self.suggest(t):
                if not (i["id"] or "").startswith("tt") or i["q"] not in ("movie", "tvMovie", "video", None):
                    continue
                same = norm(i["l"]) == norm(t)
                y_ok = (year is None and (i["y"] or 0) >= TODAY.year - 2) or (
                    year is not None and i["y"] and abs(i["y"] - year) <= 1)
                dir_ok = director and any(norm(d) in norm(i.get("s") or "") for d in director)
                if same and (y_ok or dir_ok):
                    return f"https://www.imdb.com/title/{i['id']}/"
        t = next((x for x in titles if x), "")
        return f"https://www.imdb.com/find/?q={quote(t)}&s=tt&ttype=ft"


# ---------------------------------------------------------------- merge

CINEMAS = [
    {"id": "space", "name": "The Space", "place": "Silea", "url": "https://www.thespacecinema.it/cinema/silea/al-cinema", "fn": scrape_thespace},
    {"id": "cinergia", "name": "Cinergia", "place": "Conegliano", "url": "https://coneglianocinergia.18tickets.it", "fn": scrape_cinergia},
    {"id": "verdi", "name": "Multisala Verdi", "place": "Vittorio Veneto", "url": "https://multisalaverdi.it", "fn": scrape_verdi},
    {"id": "lumiere", "name": "Lumière", "place": "Belluno", "url": "https://cinemalumiere.it", "fn": scrape_lumiere},
]
# priorità delle fonti per i metadati (la prima non vuota vince)
PRIORITY = ["space", "cinergia", "lumiere", "verdi"]


def previous_data():
    try:
        r = session.get(PREVIOUS_URL)
        if r.status_code == 200:
            return r.json()
    except Exception as e:  # noqa: BLE001
        log(f"previous data: {e}")
    return None


def main():
    results, status = {}, {}
    for c in CINEMAS:
        log(f"== {c['name']}")
        t0 = time.time()
        try:
            films = c["fn"]()
            n = sum(len(f["shows"]) for f in films)
            if not n:
                raise RuntimeError("nessuno spettacolo trovato")
            results[c["id"]] = films
            status[c["id"]] = {"ok": True, "films": len(films), "shows": n}
            log(f"  ok: {len(films)} film, {n} spettacoli ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            status[c["id"]] = {"ok": False, "error": str(e)[:200]}

    # fallback: per i cinema falliti riuso i dati dell'ultimo aggiornamento riuscito
    failed = [cid for cid, s in status.items() if not s["ok"]]
    if failed:
        prev = previous_data()
        if prev:
            for cid in failed:
                films = []
                for f in prev.get("films", []):
                    sh = [s for s in f["shows"] if s["cinema"] == cid and s["date"] >= TODAY.isoformat()]
                    if sh:
                        films.append({**{k: f.get(k) for k in ("title", "originalTitle", "runtime", "genres",
                                                                "poster", "plot", "year", "rating", "trailer")},
                                      "director": [d["name"] for d in f.get("director", [])],
                                      "cast": [a["name"] for a in f.get("cast", [])],
                                      "page": (f.get("pages") or {}).get(cid), "shows": sh})
                if films:
                    results[cid] = films
                    status[cid]["stale"] = True
                    status[cid]["staleSince"] = prev.get("generated")

    merged = {}
    for cid in sorted(results, key=PRIORITY.index):
        for f in results[cid]:
            k = key_for(f["title"])
            if not k:
                continue
            m = merged.setdefault(k, {"key": k, "titles": [], "pages": {}, "shows": []})
            m["titles"].append(f["title"])
            m["pages"][cid] = f.get("page")
            m["shows"] += [s for s in f["shows"] if s.get("time") and window(date.fromisoformat(s["date"]))]
            for fld in ("originalTitle", "runtime", "poster", "plot", "year", "rating", "trailer", "release"):
                if not m.get(fld) and f.get(fld):
                    m[fld] = f[fld]
            for fld in ("director", "cast", "genres"):
                if not m.get(fld) and f.get(fld):
                    m[fld] = f[fld]

    imdb = Imdb()
    films = []
    for m in merged.values():
        if not m["shows"]:
            continue
        seen, shows = set(), []
        for s in sorted(m["shows"], key=lambda s: (s["date"], s["time"], s["cinema"])):
            sk = (s["cinema"], s["date"], s["time"], s.get("room"))
            if sk not in seen:
                seen.add(sk)
                shows.append(s)
        title = clean_title(max(m["titles"], key=lambda t: (not t.isupper(), -len(t))))
        director = m.get("director") or []
        orig = m.get("originalTitle")
        films.append({
            "id": m["key"].replace(" ", "-"),
            "title": title,
            "originalTitle": orig if orig and norm(orig) != norm(title) else None,
            "year": m.get("year"),
            "runtime": m.get("runtime"),
            "genres": m.get("genres") or [],
            "rating": m.get("rating"),
            "poster": m.get("poster"),
            "plot": m.get("plot"),
            "trailer": m.get("trailer"),
            "imdb": imdb.title([orig, title], m.get("year"), director),
            "director": [{"name": d, "imdb": imdb.person(d)} for d in director],
            "cast": [{"name": a, "imdb": imdb.person(a)} for a in (m.get("cast") or [])[:6]],
            "pages": m["pages"],
            "shows": shows,
        })
    imdb.save()
    films.sort(key=lambda f: (f["shows"][0]["date"], f["title"]))

    out = {
        "generated": datetime.now(TZ).isoformat(timespec="minutes"),
        "cinemas": [{k: v for k, v in c.items() if k != "fn"} | {"status": status.get(c["id"])} for c in CINEMAS],
        "films": films,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    log(f"\n{len(films)} film -> {OUT}")
    for c in out["cinemas"]:
        log(f"  {c['name']}: {c['status']}")
    if all(not s["ok"] for s in status.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
