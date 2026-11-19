"""Programmazione The Space Silea dall'API del sito. Solo libreria standard.

Il sito respinge le richieste dai server di GitHub, quindi questo script gira
sul Mac (launchd, vedi local/) e salva data/thespace.json nel repo; il
workflow lo unisce agli altri cinema.

Uso: python3 scraper/thespace.py [percorso_output]
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Rome")
except Exception:  # noqa: BLE001
    TZ = None

BASE = "https://www.thespacecinema.it"
CINEMA_ID = "1009"  # Silea
DAYS_AHEAD = 10
UA = "InSala/1.0 (aggregatore personale programmazione cinema; +https://github.com/ivanzanolla/cinema)"


def fetch_json(path, retries=2):
    req = urllib.request.Request(BASE + path, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "it-IT,it;q=0.9",
        "Referer": f"{BASE}/cinema/silea/al-cinema",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After", "")
                wait = min(int(ra) if ra.isdigit() else 15 * (attempt + 1), 90)
                print(f"  HTTP {e.code} su {path}: riprovo tra {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def split_people(s, limit=None):
    import re
    if not s:
        return []
    s = re.sub(r"\([^)]*\)", "", s)
    out = [p.strip(" .,") for p in s.split(",")]
    out = [p for p in out if len(p) > 1]
    return out[:limit] if limit else out


def scrape(today=None):
    today = today or (datetime.now(TZ).date() if TZ else date.today())
    dates = fetch_json(f"/api/microservice/showings/showingDates?cinemaId={CINEMA_ID}"
                       f"&minEmbargoLevel=2&forNextWeek=true")["result"]
    films = {}
    errors = []
    for d in dates:
        if not d.get("hasShowings"):
            continue
        day = d["showingDate"][:10]
        if date.fromisoformat(day) > today + timedelta(days=DAYS_AHEAD):
            continue
        time.sleep(1.5)
        try:
            data = fetch_json(f"/api/microservice/showings/cinemas/{CINEMA_ID}/films?showingDate={day}T00:00:00"
                              f"&minEmbargoLevel=3&includesSession=true&includeSessionAttributes=true")["result"]
        except urllib.error.HTTPError as e:
            tent = " (data provvisoria)" if d.get("isTentativeScheduleDate") else ""
            print(f"  {day}{tent}: HTTP {e.code}, giorno saltato", file=sys.stderr)
            errors.append(f"{day}: HTTP {e.code}")
            if e.code == 403:
                break  # bloccati: inutile insistere sugli altri giorni
            continue
        for f in data:
            film = films.get(f["filmId"])
            if not film:
                film = films[f["filmId"]] = {
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
                        elif n and n.upper() != "2D" and "LASER" not in n.upper():
                            tags.append(n)
                    film["shows"].append({
                        "cinema": "space", "date": st.date().isoformat(), "time": st.strftime("%H:%M"),
                        "url": urljoin(BASE, s.get("bookingUrl") or ""), "room": s.get("screenName") or None,
                        "tags": sorted(set(tags)), "soldOut": bool(s.get("isSoldOut")),
                    })
    if errors:
        print("  giorni non scaricati: " + ", ".join(errors), file=sys.stderr)
    return list(films.values())


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "data/thespace.json"
    films = scrape()
    n = sum(len(f["shows"]) for f in films)
    if not n:
        sys.exit("The Space: nessuno spettacolo trovato")
    payload = {"generated": datetime.now(TZ).isoformat(timespec="minutes") if TZ else datetime.now().isoformat(),
               "films": films}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"The Space: {len(films)} film, {n} spettacoli -> {out}")
