#!/usr/bin/env python3
"""brand icons -> one sprite. fetches what is missing from simple-icons (CC0), keeps what is there (draw your own <symbol> if a brand has none)."""
import pathlib, re, requests, check

OUT = pathlib.Path(__file__).parent / "icons.svg"
CDN = "https://cdn.jsdelivr.net/npm/simple-icons@{}/icons/{}.svg"
VERSIONS = "latest", "13.21.0", "11.15.0", "9.21.0", "7.21.0"  # some brands had their icon removed - older releases still have it


def fetch(slug):
    for v in VERSIONS:
        try: r = requests.get(CDN.format(v, slug), timeout=10)
        except requests.RequestException: continue
        if r.ok: return "".join(re.findall(r"<path[^>]*/>", r.text))


if __name__ == "__main__":
    have = {k: v for k, v in re.findall(r'<symbol id="([^"]+)"[^>]*>(.*?)</symbol>', OUT.read_text() if OUT.exists() else "") if k in check.SITES}
    for key, site in check.SITES.items():
        if key not in have and not site.get("name", "").startswith(".") and (paths := fetch(site.get("icon", key))): have[key] = paths
    OUT.write_text('<svg xmlns="http://www.w3.org/2000/svg">\n' + "".join(f'<symbol id="{k}" viewBox="0 0 24 24">{v}</symbol>\n' for k, v in sorted(have.items())) + "</svg>\n")
    print(len(have), "icons, none for:", *[k for k in check.SITES if k not in have])
