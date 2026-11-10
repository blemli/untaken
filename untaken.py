#!/usr/bin/env python3
import concurrent.futures as futures, hashlib, itertools, logging, os, pathlib, re, sqlite3, threading, time
import flask, flask_limiter, flask_limiter.util as util, geoip2fast, jinja2.utils, werkzeug.middleware.proxy_fix as proxy_fix, yaml
import check, logli

HOUR, HERE = 3600, pathlib.Path(__file__).parent
app = flask.Flask(__name__)
app.wsgi_app = proxy_fix.ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # kamal-proxy in front: limit and locate the real client
logli.setup("untaken")
logging.getLogger("urllib3").setLevel(logging.CRITICAL)  # its warnings quote urls, urls contain names, and names must never reach a log
limiter = flask_limiter.Limiter(key_func=util.get_remote_address, app=app, storage_uri="memory://")
pool, watch = futures.ThreadPoolExecutor(64), futures.ThreadPoolExecutor(16)
geo = geoip2fast.GeoIP2Fast()
PRESETS = yaml.safe_load((HERE / "presets.yaml").read_text())
ICONS = set(re.findall(r'<symbol id="([^"]+)"', (HERE / "icons.svg").read_text()))
SPRITE = "/icons.svg?" + hashlib.md5((HERE / "icons.svg").read_bytes()).hexdigest()[:8]  # browsers keep the sprite for a year, new icons get a new address
LIVE = [k for k, s in check.SITES.items() if not s.get("manual")]
DB = os.environ.get("DB", "untaken.db")
health, ready, memo = {}, threading.Event(), {}  # memo: (site, name) -> (verdict, expires). the only place a searched name ever lives here: in ram, for an hour
logging.info("boot")


def canary():
    """hourly: every checker must still tell its known name from a random one. a red one answers unknown and is retried every 10 minutes."""
    for tick in itertools.count():
        keys = [k for k in LIVE if tick % 6 == 0 or not health[k]]
        for key, ok in zip(keys, watch.map(lambda k: check.healthy(k) or check.healthy(k), keys)):
            if not ok and tick % 6 == 0: logging.warning("canary red: %s", key)  # the same line every hour: logli's detector sees a check that stays red
            if ok and health.get(key) is False: logging.info("canary green again: %s", key)
            health[key] = ok
        if tick % 6 == 0: logging.info("canary: %d/%d green", sum(health.values()), len(health))
        ready.set()
        time.sleep(600)


def forget():
    """every minute: drop what is older than its hour"""
    while True:
        time.sleep(60)
        for k in [k for k, (v, expires) in list(memo.items()) if expires < time.time()]: memo.pop(k, None)


def verdict(key, name):
    """certain answers are cached for an hour, unknown is asked again after a minute"""
    if reason := check.why(key, name): return "invalid " + reason  # the purple tile says what is not allowed
    if key not in LIVE: return "unknown"
    v, expires = memo.get((key, name), ("", 0))
    if expires < time.time():
        ready.wait(20)  # just booted: let the first canary finish
        v = check.probe(key, name) if health.get(key) else "unknown"
        memo[key, name] = v, time.time() + (60 if v == "unknown" else HOUR)
    return v


def db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists likes(country, site, n, primary key(country, site))")
    return c


def country():
    """from the ip, else from the browser language (de-CH)"""
    code = geo.lookup(flask.request.remote_addr or "").country_code or ""
    lang = re.search(r"-([A-Z]{2})\b", flask.request.headers.get("Accept-Language", ""))
    return code if re.fullmatch("[A-Z]{2}", code) else lang.group(1) if lang else ""


def learn(where, on):
    """count which sites a region keeps switched on. no names, no ips."""
    with db() as c:
        c.executemany("insert into likes values(?,?,1) on conflict(country, site) do update set n=n+1", [(where, s) for s in {"*"} | on & set(check.SITES)])


def likes(where):
    """site -> (searches from this country that wanted it, searches from anywhere). * = all searches"""
    with db() as c: return {k: (here, all) for k, here, all in c.execute("select site, sum(n * (country = ?)), sum(n) from likes group by site", (where,))}


@app.route("/")
@limiter.limit("70/minute, 1000/hour")
def index():
    where = country()
    n = likes(where)
    order = sorted(check.SITES, key=lambda k: (check.SITES[k]["group"] == "tld", *(-x for x in n.get(k, (0, 0)))))  # what this country wants first, then the world, domains last
    sites = [{"key": k, "name": s.get("name", k), "group": s["group"], "link": s.get("link") or s["url"], "signup": s.get("signup"),
              "icon": k in ICONS, "manual": s.get("manual")} for k, s in ((k, check.SITES[k]) for k in order)]
    presets = dict(sorted(PRESETS.items(), key=lambda p: where not in p[1].get("region", [])))  # the local one first
    popular = [k for k in order if n.get("*", (0, 0))[0] >= 20 and n.get(k, (0, 0))[0] * 2 >= n["*"][0]]  # what half of this country's searches (min 20) care about
    data = {"sites": sites, "presets": presets, "country": where, "popular": popular, "sprite": SPRITE}
    return (HERE / "untaken.html").read_text().replace("/*DATA*/null", jinja2.utils.htmlsafe_json_dumps(data))


@app.route("/check", methods=["POST"])  # post: a name in the url would end up in access logs
@limiter.limit("20/minute, 200/hour")
def search():
    q = flask.request.get_json(force=True, silent=True)
    q = q if isinstance(q, dict) else {}
    name, (on, skip) = str(q.get("name", "")).strip().lower()[:64], ({k for k in q.get(f, []) if isinstance(k, str)} if isinstance(q.get(f, []), list) else set() for f in ("on", "skip"))
    if not q.get("more"): learn(country(), on)  # a list of variants counts as one search

    def stream():
        jobs = {pool.submit(verdict, key, name): key for key in sorted(set(check.SITES) - skip, key=lambda k: k not in on)}  # the wanted ones first, skip = already free for another variant
        try:
            for job in futures.as_completed(jobs, timeout=25): yield f"data: {jobs[job]} {'unknown' if job.exception() else job.result()}\n\n"
        except futures.TimeoutError: pass  # stragglers: the page turns them yellow
    return flask.Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/canary")
@limiter.limit("70/minute, 1000/hour")
def red(): return {"red": [k for k, ok in health.items() if not ok], "green": sum(health.values()), "manual": len(check.SITES) - len(LIVE)}


@app.route("/icons.svg")
def icons(): return flask.send_from_directory(app.root_path, "icons.svg", max_age=31536000)


@app.route("/favicon.svg")
@app.route("/untaken.svg")
def favicon(): return flask.send_from_directory(app.root_path, "untaken.svg")


@app.route("/up")
def up(): return "OK", 200


for job in canary, forget: threading.Thread(target=job, daemon=True).start()
if __name__ == "__main__":
    app.run(port=8080, debug=True, use_reloader=False)
