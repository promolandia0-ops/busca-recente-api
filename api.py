import json, os, re, time, base64, threading
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs, unquote
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor

SX = "http://127.0.0.1:%s/search" % os.environ.get("SEARXNG_PORT", "8080")
AD_DOMAINS = ["googleadservices.com","doubleclick.net","googlesyndication.com","adservice.google","bing.com/aclk","bing.com/aclick",
 "duckduckgo.com/y.js","taboola.com","outbrain.com","criteo.com","adnxs.com","amazon-adsystem.com","adsrvr.org","clickbank",
 "/aclk?","/pagead/","utm_medium=cpc","gclid=","msclkid="]
CATS = {"all":"general","images":"images","videos":"videos","news":"news"}
CACHE = {}

def unwrap(u):
    for _ in range(3):
        try: p = urlparse(u)
        except Exception: return u
        qs = parse_qs(p.query); h = p.netloc.lower()
        if "google." in h and p.path == "/url":
            u = (qs.get("q") or qs.get("url") or [u])[0]; continue
        if "duckduckgo.com" in h and p.path.startswith("/l/") and "uddg" in qs:
            u = qs["uddg"][0]; continue
        if "bing.com" in h and p.path.startswith("/ck/") and "u" in qs:
            v = qs["u"][0]
            if v.startswith("a1"):
                try: u = base64.urlsafe_b64decode(v[2:] + "=" * (-len(v[2:]) % 4)).decode(); continue
                except Exception: pass
        break
    return u

def is_ad(r):
    blob = " ".join(str(r.get(k,"")) for k in ("url","img_src","title","engine")).lower()
    if any(d in blob for d in AD_DOMAINS): return True
    if r.get("is_ad") or r.get("sponsored") or "sponsored" in str(r.get("category","")).lower(): return True
    t = str(r.get("title","")).strip().lower()
    return t.startswith(("anúncio","patrocinado","sponsored","ad ·","ad·"))

def pdate(s):
    if not s: return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z","+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None

def sx(q, cat, tr, page, lang, eng=None):
    p = {"q": q, "format": "json", "categories": cat, "pageno": page, "language": lang, "safesearch": 0}
    if tr: p["time_range"] = tr
    if eng: p["engines"] = eng
    key = urlencode(p)
    c = CACHE.get(key)
    if c and time.time() - c[0] < 300: return c[1]
    req = Request(SX + "?" + key, headers={"User-Agent": "busca", "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"})
    with urlopen(req, timeout=15) as r: d = json.load(r)
    if len(CACHE) > 300: CACHE.clear()
    CACHE[key] = (time.time(), d); return d

def search(a):
    q = (a.get("q") or "").strip()
    tab = a.get("tab","all"); cat = CATS.get(tab,"general")
    rng = a.get("range","month"); page = max(1, int(a.get("page") or 1))
    lang = a.get("lang") or "pt-BR"
    now = datetime.now(timezone.utc); lo = hi = None; tr = None; qq = q
    if rng == "hour": tr = "day"; lo = now - timedelta(hours=1)
    elif rng in ("day","week","month","year"): tr = rng
    elif rng == "custom":
        f, t = a.get("from"), a.get("to")
        if f: lo = datetime.fromisoformat(f).replace(tzinfo=timezone.utc); qq += " after:" + f
        if t:
            hi = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) + timedelta(days=1)
            qq += " before:" + (hi.date().isoformat())
        span = now - (lo or now - timedelta(days=3650))
        tr = "day" if span <= timedelta(days=1) and not hi else "week" if span <= timedelta(days=7) and not hi else "month" if span <= timedelta(days=31) and not hi else "year" if span <= timedelta(days=366) and not hi else None
    d = sx(qq, cat, tr, page, lang, a.get("engines"))
    if not d.get("results") and qq != q:
        d = sx(q, cat, tr, page, lang)  # fallback without operators
    out, seen, ads = [], set(), 0
    for r in d.get("results", []):
        if is_ad(r): ads += 1; continue
        url = unwrap(r.get("url") or "")
        if not url.startswith("http") or url in seen: continue
        seen.add(url)
        pd = pdate(r.get("publishedDate"))
        if pd and ((lo and pd < lo) or (hi and pd >= hi)): continue
        if not pd and rng == "hour" and cat != "images": continue
        out.append({"title": r.get("title",""), "url": url, "content": (r.get("content") or "")[:400],
            "date": pd.isoformat() if pd else None, "engine": r.get("engine"), "engines": r.get("engines"),
            "img": unwrap(r.get("img_src") or "") or None, "thumb": r.get("thumbnail_src") or r.get("thumbnail") or None,
            "duration": r.get("length") or r.get("duration"), "source": r.get("source") or urlparse(url).netloc,
            "w": r.get("img_format") or r.get("resolution")})
    if a.get("sort") == "date":
        out.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"q": q, "tab": tab, "range": rng, "page": page, "count": len(out), "ads_removed": ads,
            "results": out, "unresponsive": d.get("unresponsive_engines", []), "engines_used": sorted({e for r in d.get("results",[]) for e in (r.get("engines") or [])})}

class H(BaseHTTPRequestHandler):
    def send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = urlparse(self.path); a = {k: v[0] for k, v in parse_qs(p.query).items()}
        if p.path in ("/health", "/"):
            ok = True
            try: urlopen("http://127.0.0.1:%s/healthz" % os.environ.get("SEARXNG_PORT","8080"), timeout=5)
            except Exception: ok = False
            return self.send(200, {"ok": True, "searxng": ok, "time": datetime.now(timezone.utc).isoformat()})
        if p.path == "/config":
            c = json.load(urlopen("http://127.0.0.1:%s/config" % os.environ.get("SEARXNG_PORT","8080"), timeout=10))
            return self.send(200, [[e["name"], e["enabled"], e["categories"]] for e in c.get("engines", [])])
        if p.path == "/api/search":
            if not a.get("q"): return self.send(400, {"error": "q obrigatório"})
            t = time.time()
            try: r = search(a); r["ms"] = int((time.time()-t)*1000); return self.send(200, r)
            except Exception as e: return self.send(502, {"error": str(e)})
        self.send(404, {"error": "not found"})
    def log_message(self, *a): pass

ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), H).serve_forever()
