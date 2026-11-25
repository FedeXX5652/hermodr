"""Reads the feeds, extracts the articles and marks what is on each front page."""
import calendar
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
import httpx
import trafilatura

FRONTPAGE_TOP = 30          # links counted from the top of a home page


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_url(u):
    """Same article with different tracking params = same url."""
    p = urlsplit(u)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", ""))


def text_hash(*parts):
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


class Fetcher:
    """httpx with a pause per site, so no outlet gets hammered."""

    def __init__(self, cfg, transport=None):
        self.delay = cfg.get("per_host_delay_s", 1.0)
        self.max_bytes = cfg.get("max_article_kb", 3000) * 1024
        self.last = {}
        self.client = httpx.Client(timeout=cfg.get("timeout_s", 20), follow_redirects=True, transport=transport,
                                   headers={"User-Agent": cfg.get("user_agent", "hermodr")})

    def get(self, url):
        host = urlsplit(url).netloc
        wait = self.last.get(host, 0) + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last[host] = time.monotonic()
        r = self.client.get(url)
        r.raise_for_status()
        if len(r.content) > self.max_bytes:
            raise ValueError(f"{len(r.content) // 1024} KB, over the limit")
        return r

    def close(self):
        self.client.close()


def _entry_time(e):
    for key in ("published_parsed", "updated_parsed"):
        if e.get(key):
            return datetime.fromtimestamp(calendar.timegm(e[key]), timezone.utc).replace(microsecond=0).isoformat()
    return now_iso()


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def read_feed(fetcher, url):
    """List of dicts with url, title, lead, published, image_url. Raises if the feed is broken."""
    r = fetcher.get(url)
    d = feedparser.parse(r.content)
    if d.bozo and not d.entries:
        raise ValueError(f"not a feed ({d.bozo_exception})")
    out = []
    for e in d.entries:
        link = e.get("link")
        if not link or not e.get("title"):
            continue
        img = next((m.get("url") for m in e.get("media_content", []) + e.get("media_thumbnail", []) if m.get("url")),
                   None) or next((l.get("href") for l in e.get("links", []) if l.get("rel") == "enclosure"
                                  and str(l.get("type", "")).startswith("image")), None)
        out.append({"url": clean_url(link), "title": _strip_html(e.title), "published": _entry_time(e),
                    "lead": _strip_html(e.get("summary", ""))[:2000], "image_url": img})
    return out


def extract_article(fetcher, url):
    """(text, image) of the article page. ('', None) if it can't be extracted: the note keeps the feed lead."""
    try:
        html = fetcher.get(url).text
    except Exception:
        return "", None
    j = trafilatura.extract(html, output_format="json", with_metadata=True, include_comments=False,
                            include_tables=False, url=url)
    if not j:
        return "", None
    d = json.loads(j)
    return d.get("text") or "", d.get("image")


def frontpage_links(fetcher, url):
    html = fetcher.get(url).text
    seen, out = set(), []
    for href in re.findall(r'<a\s[^>]*href="([^"#]+)"', html, re.I):
        u = clean_url(urljoin(url, href))
        if u not in seen and urlsplit(u).path.count("/") >= 2:      # skips home and section roots
            seen.add(u)
            out.append(u)
        if len(out) >= FRONTPAGE_TOP:
            break
    return set(out)


def collect(con, settings, transport=None, log=print):
    """Reads every feed and saves the new notes. Returns (new notes, list of problems)."""
    cfg = settings.general.get("collect", {})
    f, new, problems = Fetcher(cfg, transport), 0, []
    try:
        for s in settings.sources:
            front = set()
            if s.get("frontpage"):
                try:
                    front = frontpage_links(f, s["frontpage"])
                except Exception as e:
                    problems.append(f"{s['id']} front page: {e}")
            for fd in s["feeds"]:
                try:
                    entries = read_feed(f, fd["url"])
                    con.execute("INSERT INTO feed_status(url, last_ok, error) VALUES (?, ?, NULL) "
                                "ON CONFLICT(url) DO UPDATE SET last_ok = excluded.last_ok, error = NULL",
                                (fd["url"], now_iso()))
                except Exception as e:
                    problems.append(f"{s['id']} {fd['url']}: {e}")
                    con.execute("INSERT INTO feed_status(url, last_error, error) VALUES (?, ?, ?) "
                                "ON CONFLICT(url) DO UPDATE SET last_error = excluded.last_error, error = excluded.error",
                                (fd["url"], now_iso(), str(e)[:300]))
                    continue
                for e in entries:
                    if con.execute("SELECT 1 FROM notes WHERE url = ?", (e["url"],)).fetchone():
                        if e["url"] in front:
                            con.execute("UPDATE notes SET frontpage = 1 WHERE url = ?", (e["url"],))
                        continue
                    text, img = (extract_article(f, e["url"]) if cfg.get("fetch_articles", True) else ("", None))
                    con.execute("INSERT INTO notes(source, section, url, title, lead, text, image_url, published, "
                                "fetched, text_hash, frontpage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (s["id"], fd["section"], e["url"], e["title"], e["lead"], text,
                                 e["image_url"] or img, e["published"], now_iso(),
                                 text_hash(e["title"], e["lead"], text), int(e["url"] in front)))
                    new += 1
                con.commit()
                log(f"  {s['id']:<20} {fd['section']:<11} {len(entries):>3} in feed")
    finally:
        f.close()
    return new, problems


def check_sources(settings, transport=None, log=print):
    """Tests every feed and one article per feed. True if everything works."""
    f, ok = Fetcher(settings.general.get("collect", {}), transport), True
    try:
        for s in settings.sources:
            for fd in s["feeds"]:
                try:
                    entries = read_feed(f, fd["url"])
                except Exception as e:
                    log(f"\033[91mFAIL\033[0m {s['id']:<20} {fd['url']}\n     {e}")
                    ok = False
                    continue
                newest = max((e["published"] for e in entries), default="-")
                text = extract_article(f, entries[0]["url"])[0] if entries else ""
                status = "ok  " if entries and text else "WARN"
                log(f"{status} {s['id']:<20} {len(entries):>3} notes, newest {newest[:16]}, "
                    f"article text {len(text.split())} words  {fd['url']}")
            if s.get("frontpage"):
                try:
                    log(f"ok   {s['id']:<20} front page: {len(frontpage_links(f, s['frontpage']))} links")
                except Exception as e:
                    log(f"WARN {s['id']:<20} front page: {e}")
    finally:
        f.close()
    return ok
