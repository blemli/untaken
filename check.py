#!/usr/bin/env python3
"""the engine. transports fetch evidence, strategies judge it.
a verdict needs positive evidence for exactly one of taken/free/probably_taken/probably_free - anything else is unknown."""
import collections, os, pathlib, random, re, smtplib, socket, string, sys, requests, yaml

Evidence = collections.namedtuple("Evidence", "status text url")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
           "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en"}
NAME = re.compile(r"[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?")  # global rule, every site narrows it with chars, length (and valid)
CHARS, LENGTH = "a-z0-9_-", "1-40"
VERDICTS = ("taken", "free", "probably_taken", "probably_free")
STRATEGIES, TRANSPORTS = {}, {}
SITES = {k: v | {"group": f.stem[3:]} for f in sorted(pathlib.Path(__file__).parent.glob("sites/*.yaml"))
         for k, v in yaml.safe_load(f.read_text()).items()}
for s in SITES.values():  # a checker that wants an api key ($KEY in its headers) stays manual until the key is in the environment
    if lacking := [v for v in re.findall(r"\$(\w+)", str(s.get("headers", ""))) if not os.environ.get(v)]: s["manual"] = "needs " + lacking[0]


def register(registry, key=None): return lambda f: registry.setdefault(key or f.__name__, f)
def many(x): return x if isinstance(x, list) else [x]


def fill(x, name):
    """put the name into every {} of a string, dict or list"""
    if isinstance(x, str): return x.replace("{}", name)
    if isinstance(x, dict): return {k: fill(v, name) for k, v in x.items()}
    return [fill(v, name) for v in x] if isinstance(x, list) else x


# strategies: does the evidence show what we want? add one = add a function
@register(STRATEGIES)
def status(e, want): return e.status in many(want)  # the site answers with (one of) these codes


@register(STRATEGIES)
def contains(e, want): return all(w.lower() in e.text.lower() for w in many(want))  # every text is in the body


@register(STRATEGIES)
def lacks(e, want): return not any(w.lower() in e.text.lower() for w in many(want))  # none of the texts is in the body


@register(STRATEGIES)
def matches(e, want): return bool(re.search(want, e.text))  # regex finds something in the body


@register(STRATEGIES)
def lands(e, want): return bool(re.search(want, e.url))  # regex finds something in the final url (after redirects)


# transports: picked by the key a site uses (url / whois / smtp)
@register(TRANSPORTS, "url")
def http(s, name):
    reads = any(k in ("contains", "lacks", "matches") for v in VERDICTS for k in s.get(v, {}))  # no rule about the body = no download
    with requests.request(s.get("method", "GET"), fill(s["url"], name), headers=HEADERS | {k: os.path.expandvars(v) for k, v in s.get("headers", {}).items()}, timeout=(5, 10), stream=True,
                          json=fill(s.get("json"), name), data=fill(s.get("data"), name), allow_redirects=s.get("follow", True)) as r:
        return Evidence(r.status_code, r.raw.read(3_000_000, decode_content=True).decode("utf-8", "replace") if reads else "", r.url)


@register(TRANSPORTS)
def whois(s, name):
    with socket.create_connection((s["whois"], 43), timeout=8) as c:
        c.sendall(fill(s.get("query", "{}"), name).encode() + b"\r\n")
        return Evidence(0, b"".join(iter(lambda: c.recv(4096), b"")).decode(errors="replace"), "")


@register(TRANSPORTS)
def smtp(s, name):
    """ask the mail server if it would accept mail for the address - nothing is sent"""
    with smtplib.SMTP(s["smtp"], 25, timeout=8) as c:
        c.helo(), c.mail("")
        code, text = c.rcpt(fill(s["rcpt"], name))
        return Evidence(code, text.decode(errors="replace"), "")


def evidence(s, name): return TRANSPORTS[next(k for k in s if k in TRANSPORTS)](s, name)


def shaped(key, name): return re.sub(f"[{SITES[key].get('strip', '')}]+", "", name) if SITES[key].get("strip") else name  # strip: what the site simply drops


def why(key, name):
    """what the site does not allow about this name, in words for the purple tile ("" = fine)"""
    s, name = SITES[key], shaped(key, name)
    low, high = map(int, str(s.get("length", LENGTH)).split("-"))
    if bad := sorted(set(re.sub(f"[{s.get('chars', CHARS)}]", "", name))): return "no " + " ".join("space" if c == " " else c for c in bad)
    if not low <= len(name) <= high: return f"{low}-{high} chars"
    if not NAME.fullmatch(name): return "a-z 0-9 at the ends"
    return "" if re.fullmatch(s.get("valid", ".*"), name) else s.get("hint", "not this shape")


def judge(s, e, name):
    hits = [v for v in VERDICTS if v in s and all(STRATEGIES[k](e, fill(w, name)) for k, w in s[v].items())]
    return hits[0] if len(hits) == 1 else "unknown"


def probe(key, name):
    """one live check: taken | free | probably_taken | probably_free | unknown | invalid"""
    if why(key, name): return "invalid"
    name = shaped(key, name)
    for s in (SITES[key], SITES[key].get("fallback")):  # manual: no server can check this site, the user decides
        try:
            if s and not s.get("manual") and (v := judge(s, evidence(s, name), name)) != "unknown": return v
        except Exception: pass  # the fallback only speaks when the first source cannot
    return "unknown"


def nobody(key):
    """a random name nobody has, in a shape the site allows"""
    for n in (14, 10, 8, 6, 20, 5, 4):
        name = "".join(random.choices(string.ascii_lowercase, k=n - 2)) + "".join(random.choices(string.digits, k=2))
        if not why(key, name): return name


def healthy(key):
    """canary: the known name must be taken and a random one free, else the checker lies and must not be trusted"""
    return probe(key, str(SITES[key]["known"])).endswith("taken") and probe(key, nobody(key) or "").endswith("free")


if __name__ == "__main__":  # ./check.py github [name] -> show the evidence; ./check.py -> canary all sites
    if len(sys.argv) > 1:
        key, name = sys.argv[1], (sys.argv[2:] or [None])[0]
        for n in [name] if name else [str(SITES[key]["known"]), nobody(key)]:
            e = evidence(SITES[key], shaped(key, n)) if not why(key, n) and not SITES[key].get("manual") else Evidence("-", why(key, n), "")
            print(f"{n}: {probe(key, n)}  [{e.status}] {e.url}\n  {e.text[:300]!r}")
    else:
        import concurrent.futures
        live = [k for k in SITES if not SITES[k].get("manual")]
        with concurrent.futures.ThreadPoolExecutor(32) as pool:
            for key, ok in zip(live, pool.map(healthy, live)): print("ok  " if ok else "FAIL", key)
