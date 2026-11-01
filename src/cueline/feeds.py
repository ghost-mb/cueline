"""Watchlist inputs: resolve a URL into a watch, list its newest entries, and read one entry's full text.

Kinds: "youtube" (a channel), "youtube_playlist", "youtube_search" (a keyword, newest uploads first) — all listed with
yt-dlp because YouTube's RSS returns 404 from this machine — plus "rss" (blogs, newsletters, Substack) and "podcast"
(RSS with audio; show notes only until local speech-to-text lands).
No LLM calls here. Every HTTP fetch goes through `fetch()`, which only talks to public hosts.
"""
import html
import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from defusedxml import ElementTree as ET

from . import youtube

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Cueline/2"
LIST_LIMIT = 15        # entries looked at per check
MIN_ARTICLE_CHARS = 1500  # shorter feed bodies get the full page fetched


# ---------- safe fetch ----------
def _public_addrs(host: str, port: int) -> list:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Couldn't find {host}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:  # localhost, LAN, link-local...: never let a feed point Cueline at this machine
            raise ValueError("That link points to a private address, which Cueline won't fetch.")
    return infos


def _check_public(url: str) -> None:
    """Early, friendly check. The real guard is _safe_connect, which checks again at connect time."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http(s) links are supported.")
    _public_addrs(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))


def _safe_connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *args, **kwargs):
    """Connect only to addresses checked in this same lookup, so DNS can't swap in a private one (DNS rebinding).
    shortcut: an HTTP(S)_PROXY on a private address is refused too; allow-list proxies if someone needs one."""
    host, port = address[:2]
    err = None
    for family, type_, proto, _, sockaddr in _public_addrs(host, port):
        sock = socket.socket(family, type_, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            err = exc
            sock.close()
    raise err or OSError(f"Couldn't connect to {host}.")


class _SafeHTTP(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_connect  # set per instance: http.client sets it in __init__ too


class _SafeHTTPS(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_connect  # set per instance: http.client sets it in __init__ too


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTP, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPS, req, context=self._context)


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects, _SafeHTTPHandler, _SafeHTTPSHandler)


def fetch(url: str) -> tuple[bytes, str, str]:
    """GET a public URL. Returns (body, content_type, final_url)."""
    _check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en;q=0.9,*;q=0.5"})
    try:
        with _opener.open(req, timeout=TIMEOUT) as res:
            body = res.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("That page is larger than 5 MB.")
            return body, res.headers.get("Content-Type", ""), res.geturl()
    except urllib.error.HTTPError as exc:
        raise ValueError(f"The site answered {exc.code} {exc.reason}.") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Couldn't reach the site ({exc.reason}).") from exc


# ---------- resolve ----------
def is_youtube(url: str) -> bool:
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return host in ("youtu.be", "youtube.com") or host.endswith(".youtube.com")


YOUTUBE_KINDS = {"youtube", "youtube_playlist", "youtube_search"}


def resolve_search(query: str) -> dict:
    query = " ".join(query.split())[:120]
    if len(query) < 2:
        raise ValueError("Type a few words to search YouTube for.")
    # sp=CAI%3D sorts by upload date, so each check sees the newest matching videos
    feed_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=CAI%253D"
    return {"kind": "youtube_search", "url": f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            "feed_url": feed_url, "title": f"YouTube search: {query}", "meta": {"query": query}}


def resolve(url: str) -> dict:
    """Turn what the user pasted into {kind, url, feed_url, title, meta}."""
    url = url.strip()
    if not url:
        raise ValueError("Paste a channel, blog, newsletter or podcast link.")
    if "://" not in url:
        url = "https://" + url
    if is_youtube(url):
        return _resolve_youtube(url)
    body, ctype, final = fetch(url)
    if _looks_like_feed(body, ctype):
        return _feed_watch(url, final, body)
    feed_url = _discover_feed(body, final)
    if not feed_url:
        raise ValueError("No RSS feed found on that page. Try the site's /feed or /rss link.")
    body, _, final = fetch(feed_url)
    return _feed_watch(url, final, body)


def _resolve_youtube(url: str) -> dict:
    from yt_dlp import YoutubeDL

    parsed = urlparse(url)
    path = parsed.path
    playlist = parse_qs(parsed.query).get("list", [""])[0]
    if playlist and not playlist.startswith(("RD", "UL")):  # RD/UL = auto-generated mixes, not real playlists
        feed_url = f"https://www.youtube.com/playlist?list={playlist}"
        info = _yt_list(feed_url, 1)
        owner = info.get("uploader") or info.get("channel")
        return {"kind": "youtube_playlist", "url": url, "feed_url": feed_url,
                "title": (info.get("title") or "YouTube playlist") + (f" · {owner}" if owner else ""),
                "meta": {"playlist_id": playlist, "channel": owner}}
    if "/watch" in path or "youtu.be" in url or "/shorts/" in path:  # a video link: follow it to its channel
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        base = info.get("channel_url") or f"https://www.youtube.com/channel/{info['channel_id']}"
    else:
        base = re.sub(r"/(videos|shorts|streams|featured|playlists|about|community)/?$", "", url.split("?")[0].rstrip("/"))
    feed_url = base + "/videos"
    info = _yt_list(feed_url, 1)
    return {"kind": "youtube", "url": url, "feed_url": feed_url, "title": info.get("channel") or info.get("title") or base,
            "meta": {"channel_id": info.get("channel_id"), "handle": info.get("uploader_id")}}


def _yt_list(feed_url: str, limit: int) -> dict:
    from yt_dlp import YoutubeDL

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": limit, "skip_download": True}
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(feed_url, download=False)
    except Exception as exc:
        raise ValueError(f"Couldn't read that YouTube channel: {str(exc)[:200]}") from exc


def _looks_like_feed(body: bytes, ctype: str) -> bool:
    head = body[:600].lstrip().lower()
    return "xml" in ctype or head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def _discover_feed(body: bytes, base: str) -> str | None:
    text = body[:400_000].decode("utf-8", errors="replace")
    for tag in re.findall(r"<link\b[^>]*>", text, re.I):
        if re.search(r'type=["\']application/(rss|atom)\+xml', tag, re.I):
            m = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if m:
                return urljoin(base, html.unescape(m.group(1)))
    return None


def _feed_watch(url: str, feed_url: str, body: bytes) -> dict:
    root = _parse_xml(body)
    channel = root.find("channel")
    title = _text(channel.find("title")) if channel is not None else _text(_find(root, "title"))
    entries = _feed_entries(root)
    # Newsletters (e.g. Substack) attach audio to a few posts; a podcast is a feed where most items are audio.
    podcast = bool(entries) and sum(1 for e in entries if e["meta"].get("audio")) / len(entries) >= 0.6
    return {"kind": "podcast" if podcast else "rss", "url": url, "feed_url": feed_url,
            "title": (title or urlparse(feed_url).hostname or "Feed")[:200], "meta": {}}


# ---------- list entries ----------
def list_entries(watch: dict) -> list[dict]:
    """Newest first: [{guid, kind, title, url, author, published_at, content, meta}]. content may be partial."""
    if watch["kind"] in YOUTUBE_KINDS:
        info = _yt_list(watch["feed_url"], LIST_LIMIT)
        out = []
        for e in (info.get("entries") or [])[:LIST_LIMIT]:
            vid = e.get("id")
            if not vid or len(vid) != 11:
                continue
            out.append({"guid": vid, "kind": "youtube", "title": e.get("title") or "Untitled video",
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "author": e.get("channel") or e.get("uploader") or info.get("channel") or watch["title"],
                        "published_at": None, "content": "",
                        "meta": {"video_id": vid, "duration_s": e.get("duration"), "views": e.get("view_count"),
                                 "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}})
        return out
    body, _, _ = fetch(watch["feed_url"])
    entries = _feed_entries(_parse_xml(body))
    for e in entries:
        e["kind"] = "podcast" if watch["kind"] == "podcast" else "article"
        e["author"] = e["author"] or watch["title"]
    entries.sort(key=lambda e: e["published_at"] or "", reverse=True)
    return entries[:LIST_LIMIT]


ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def _parse_xml(body: bytes):
    try:
        return ET.fromstring(body)
    except Exception as exc:
        raise ValueError("That feed isn't valid RSS/Atom.") from exc


def _find(node, name):
    return node.find(name) if node.find(name) is not None else node.find(ATOM + name)


def _text(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def _date(value: str) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _feed_entries(root) -> list[dict]:
    out = []
    channel = root.find("channel")
    if channel is not None:  # RSS 2.0
        for it in channel.findall("item"):
            link = _text(it.find("link"))
            enc = it.find("enclosure")
            audio = enc.get("url") if enc is not None and (enc.get("type") or "").startswith("audio") else None
            body = _text(it.find(CONTENT)) or _text(it.find(ITUNES + "summary")) or _text(it.find("description"))
            out.append({"guid": _text(it.find("guid")) or link or _text(it.find("title")),
                        "title": html.unescape(_text(it.find("title"))) or "Untitled", "url": link or None,
                        "author": _text(it.find("{http://purl.org/dc/elements/1.1/}creator")) or _text(it.find("author")),
                        "published_at": _date(_text(it.find("pubDate"))), "content": html_to_text(body),
                        "meta": {"audio": audio, "duration": _text(it.find(ITUNES + "duration")) or None}})
    else:  # Atom
        for it in root.findall(ATOM + "entry"):
            link = next((l.get("href") for l in it.findall(ATOM + "link") if l.get("rel", "alternate") == "alternate"), None)
            body = _text(it.find(ATOM + "content")) or _text(it.find(ATOM + "summary"))
            author = it.find(ATOM + "author")
            out.append({"guid": _text(it.find(ATOM + "id")) or link or _text(it.find(ATOM + "title")),
                        "title": html.unescape(_text(it.find(ATOM + "title"))) or "Untitled", "url": link,
                        "author": _text(author.find(ATOM + "name")) if author is not None else "",
                        "published_at": _date(_text(it.find(ATOM + "published")) or _text(it.find(ATOM + "updated"))),
                        "content": html_to_text(body), "meta": {}})
    return [e for e in out if e["guid"]]


# ---------- full text ----------
def full_text(item: dict) -> tuple[str, dict]:
    """Return (content, extra_meta) for one entry: transcript, article text or show notes."""
    if item["kind"] == "youtube":
        v = youtube.extract(item["url"])
        return v["transcript_md"], {"channel": v["channel"], "duration_s": v["duration_s"],
                                    "upload_date": v["upload_date"], "description": (v["description"] or "")[:3000],
                                    "thumbnail": v["thumbnail"], "video_id": v["video_id"]}
    text = item.get("content") or ""
    if item["kind"] == "article" and len(text) < MIN_ARTICLE_CHARS and item.get("url"):
        try:
            body, ctype, _ = fetch(item["url"])
            if "html" in ctype or not ctype:
                page = article_text(body.decode("utf-8", errors="replace"))
                if len(page) > len(text):
                    text = page
        except ValueError:
            pass  # keep the feed's excerpt
    if not text.strip():
        raise ValueError("No readable text in this item.")
    return text, {}


class _TextParser(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "section", "article"}
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "figure", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.in_article, self.article = [], 0, 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("article", "main"):
            self.in_article += 1
        if tag in self.BLOCK:
            self._push("\n")
        if tag in ("h1", "h2", "h3"):
            self._push("## ")
        if tag == "li":
            self._push("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in ("article", "main") and self.in_article:
            self.in_article -= 1
        if tag in self.BLOCK:
            self._push("\n")

    def handle_data(self, data):
        if not self.skip:
            self._push(data)

    def _push(self, s):
        self.parts.append(s)
        if self.in_article:
            self.article.append(s)


def _clean(parts: list[str]) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parts))
    lines = [l.strip() for l in text.split("\n")]
    text = "\n".join(l for l in lines if l and l not in ("-", "##"))
    return re.sub(r"\n{2,}", "\n\n", text).strip()


def html_to_text(fragment: str) -> str:
    if "<" not in fragment:
        return html.unescape(fragment).strip()
    p = _TextParser()
    p.feed(fragment)
    return _clean(p.parts)


def article_text(page: str) -> str:
    """Main text of a web page: the <article>/<main> element when there is one, else the whole body."""
    p = _TextParser()
    p.feed(page)
    article = _clean(p.article)
    return article if len(article) > 400 else _clean(p.parts)


def preview(watch: dict, n: int = 3) -> list[dict]:
    """The newest few entries, for the Follow dialog (no AI, nothing stored)."""
    return [{"title": e["title"], "url": e["url"], "author": e["author"], "published_at": e["published_at"],
             "thumbnail": (e["meta"] or {}).get("thumbnail"), "duration_s": (e["meta"] or {}).get("duration_s"),
             "views": (e["meta"] or {}).get("views")} for e in list_entries(watch)[:n]]
