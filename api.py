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
        if "bing.com" in h and "apiclick" in p.path and "url" in qs:
            u = qs["url"][0]; continue
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


import html as HT, xml.etree.ElementTree as ET
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PTM = {"jan":1,"fev":2,"mar":3,"abr":4,"mai":5,"jun":6,"jul":7,"ago":8,"set":9,"out":10,"nov":11,"dez":12}
ENM = {m:i+1 for i,m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
def rssdate(s):
    if not s: return None
    m = re.search(r"(\d{1,2})\s+([A-Za-zçÇ]{3})[a-z.]*\s+(\d{4})\s+(\d\d):(\d\d)(?::(\d\d))?", s)
    if not m: return None
    mo = PTM.get(m.group(2).lower()) or ENM.get(m.group(2).lower())
    if not mo: return None
    return datetime(int(m.group(3)), mo, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=timezone.utc).isoformat()
def get(url, t=8):
    k = "G:" + url; c = CACHE.get(k)
    if c and time.time() - c[0] < 300: return c[1]
    with urlopen(Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}), timeout=t) as r: b = r.read().decode("utf-8", "replace")
    CACHE[k] = (time.time(), b); return b
def rss_items(xml):
    out = []
    for it in ET.fromstring(xml).iter("item"):
        g = lambda n: (it.findtext(n) or "").strip()
        src = it.find("source")
        desc = re.sub("<[^>]+>", " ", HT.unescape(g("description")))
        out.append({"title": g("title"), "url": g("link"), "content": re.sub(r"\s+", " ", desc).strip(),
                    "publishedDate": rssdate(g("pubDate")), "source": src.text if src is not None else None})
    return out
def days(d): return int((d - datetime(1970,1,1,tzinfo=timezone.utc)).days)
def bing_web(q, rng, lo, hi, page):
    f = {"day":'ex1:"ez1"',"hour":'ex1:"ez1"',"week":'ex1:"ez2"',"month":'ex1:"ez3"'}.get(rng)
    now = datetime.now(timezone.utc)
    if rng == "year": f = 'ex1:"ez5_%d_%d"' % (days(now - timedelta(days=365)), days(now))
    if rng == "custom": f = 'ex1:"ez5_%d_%d"' % (days(lo or now - timedelta(days=3650)), days((hi or now + timedelta(days=1)) - timedelta(days=1)))
    p = {"format": "rss", "q": q, "setlang": "pt-BR", "cc": "BR", "first": (page-1)*10+1}
    if f: p["filters"] = f
    rs = rss_items(get("https://www.bing.com/search?" + urlencode(p)))
    for r in rs: r["engine"] = "bing"
    return rs
def bing_news(q, rng, page):
    p = {"q": q, "format": "rss", "setlang": "pt-BR", "cc": "BR", "first": (page-1)*10+1}
    iv = {"hour":"4","day":"7","week":"8","month":"9"}.get(rng)
    if iv: p["qft"] = 'interval="%s"' % iv
    rs = rss_items(get("https://www.bing.com/news/search?" + urlencode(p)))
    for r in rs: r["engine"] = "bing news"
    return rs
def gnews(q, rng, lo, hi, page):
    w = {"hour":" when:1h","day":" when:1d","week":" when:7d","month":" when:30d","year":" when:365d"}.get(rng, "")
    if rng == "custom":
        if lo: w += " after:" + lo.date().isoformat()
        if hi: w += " before:" + hi.date().isoformat()
    rs = rss_items(get("https://news.google.com/rss/search?" + urlencode({"q": q + w, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})))
    rs = rs[(page-1)*20: page*20]
    for r in rs:
        r["engine"] = "google news"
        if r["source"] and r["title"].endswith(" - " + r["source"]): r["title"] = r["title"][: -len(r["source"]) - 3]
    return rs
def bing_images(q, rng, lo, page):
    now = datetime.now(timezone.utc)
    mins = {"hour":60,"day":1440,"week":10080,"month":43200,"year":525600}.get(rng)
    if rng == "custom" and lo: mins = max(60, int((now - lo).total_seconds() // 60))
    p = {"q": q, "first": (page-1)*35, "count": 35, "setlang": "pt-BR", "cc": "BR", "safeSearch": "Off"}
    if mins: p["qft"] = "+filterui:age-lt%d" % mins
    UAS = [UA, "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
           "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"]
    tries = [("async", p, UAS[0]), ("search", dict(p, mkt="pt-BR", form="HDRSC2"), UAS[1]),
             ("async", {k: v for k, v in p.items() if k not in ("cc", "setlang")}, UAS[2]), ("search", {k: v for k, v in p.items() if k != "cc"}, UAS[2])]
    h = ""
    for path, pp, ua in tries:
        url = "https://www.bing.com/images/%s?%s" % (path, urlencode(pp))
        try:
            c = CACHE.get("G:" + url)
            if c: h = c[1]
            else:
                with urlopen(Request(url, headers={"User-Agent": ua, "Accept-Language": "pt-BR,pt;q=0.9"}), timeout=6) as r: h = r.read().decode("utf-8", "replace")
        except Exception as e: print("bing img err", e, flush=True); continue
        if 'murl' in h:
            CACHE["G:" + url] = (time.time(), h); break
        print("bing images empty", path, len(h), flush=True)
    out = []
    for m in re.finditer(r'\bm="([^"]+)"', h):
        try: j = json.loads(HT.unescape(m.group(1)))
        except Exception: continue
        if not j.get("murl"): continue
        out.append({"title": j.get("t") or "", "url": j.get("purl") or j.get("murl"), "img_src": j["murl"],
                    "thumbnail_src": j.get("turl"), "engine": "bing images", "content": j.get("desc") or ""})
    return out
POOL = ThreadPoolExecutor(16)
def extra(tab, q, rng, lo, hi, page):
    if tab == "images": fs = [lambda: bing_images(q, rng, lo, page)]
    elif tab == "news": fs = [lambda: gnews(q, rng, lo, hi, page), lambda: bing_news(q, rng, page)]
    elif tab == "all": fs = [lambda: bing_web(q, rng, lo, hi, page)] + ([lambda: gnews(q, rng, lo, hi, page)] if rng in ("hour","day") else [])
    else: fs = []
    futs = [POOL.submit(f) for f in fs]; out = []
    for f in futs:
        try: out += f.result(timeout=10)
        except Exception as e: print("provider error", e, flush=True)
    return out

def search(a):
    q = (a.get("q") or "").strip()
    tab = a.get("tab","all"); cat = CATS.get(tab,"general")
    rng = a.get("range","month"); page = max(1, int(a.get("page") or 1))
    lang = a.get("lang") or "pt-BR"
    now = datetime.now(timezone.utc); lo = hi = None; tr = None; qq = q
    if rng == "hour": tr = "day"; lo = now - timedelta(hours=1)
    elif rng in ("day","week","month","year"):
        tr = rng; lo = now - {"day":timedelta(days=1, hours=2),"week":timedelta(days=7, hours=6),"month":timedelta(days=31),"year":timedelta(days=366)}[rng]
    elif rng == "custom":
        f, t = a.get("from"), a.get("to")
        if f: lo = datetime.fromisoformat(f).replace(tzinfo=timezone.utc); qq += " after:" + f
        if t:
            hi = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) + timedelta(days=1)
            qq += " before:" + (hi.date().isoformat())
        span = now - (lo or now - timedelta(days=3650))
        tr = "day" if span <= timedelta(days=1) and not hi else "week" if span <= timedelta(days=7) and not hi else "month" if span <= timedelta(days=31) and not hi else "year" if span <= timedelta(days=366) and not hi else None
    fx = POOL.submit(extra, tab, q, rng, lo, hi, page)
    try:
        d = sx(qq, cat, tr, page, lang, a.get("engines"))
        if not d.get("results") and qq != q: d = sx(q, cat, tr, page, lang)
    except Exception as e:
        print("searxng error", e, flush=True); d = {"results": [], "unresponsive_engines": [["searxng", str(e)]]}
    ex = fx.result()
    # interleave: extra providers first (dated & filtered), then searxng
    d["results"] = ex + d.get("results", [])
    out, seen, ads = [], set(), 0
    for r in d.get("results", []):
        if is_ad(r): ads += 1; continue
        url = unwrap(r.get("url") or "")
        tk = re.sub(r"\W+", "", (r.get("title") or "").lower())[:60]
        if not url.startswith("http") or url in seen or (tk and tab != "images" and tk in seen): continue
        seen.add(url); seen.add(tk)
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
        if p.path == "/dbg":
            u = a.get("u", "")
            if not u.startswith("https://www.bing.com/"): return self.send(400, {})
            with urlopen(Request(u, headers={"User-Agent": a.get("ua") or UA, "Accept-Language": "pt-BR,pt;q=0.9"}), timeout=8) as r: h = r.read().decode("utf-8", "replace")
            b = h.encode(); self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(b); return
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
