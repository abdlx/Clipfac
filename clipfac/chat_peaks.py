"""Chat-peak moments for the moment picker: what the stream's own chat reacted to.

A Twitch VOD or a YouTube live replay comes with evidence no transcript
has: the chat. When something happens, chat floods — emotes, laughter, hype,
"CLIP IT" — and the flood marks the moment. This module fetches that chat as
timestamped messages, finds the peaks, and hands them to the picker the same
way ``twitch_signals`` hands it viewer clips: as heat per scoring window,
plus labels the model can read.

How it works (the approach every working chat-peak tool converged on, verified
against TwitchSnipBot, Clip-it, Hypeline, Clips Kitty and cutchain/watch in
November 2026 — the statistical detectors all work; the ML ones ship no
weights):

* Fetch: Twitch chat replay via Twitch's public web GraphQL endpoint (the
  plain ``VideoCommentsByOffsetOrCursor`` query, client-id scraped live from
  twitch.tv so it never rots, paging by offset — cursor paging trips Twitch's
  integrity check). YouTube live-chat replay via yt-dlp's ``live_chat``
  subtitle track. Kick deletes chat when a stream ends and has no replay
  endpoint, so Kick returns no chat signal and the job falls back to the
  transcript picker.
* Detect: each message is classified into reaction classes (hype, funny,
  surprise, scare, fail, clip-request). A 5-second burst is compared against
  the 45 seconds before it; a burst >= 3x the normal rate with >= 4 messages
  is a moment (a burst with no reaction in it needs 4x and >= 6). Counts are
  capped per user so one spammer cannot trigger a peak, and peaks are measured
  against the stream's own chat, so a small channel's burst counts like a big
  channel's.
* Lag: chat reacts *after* the moment (stream delay + typing time), so every
  peak is dated 6 seconds earlier — the clip starts before the moment, not
  after it. Peaks within 10 seconds merge into one moment.

Everything here is best effort. No chat, a network error, a rotated Twitch
endpoint: the job carries on without the signal, exactly as it did before.
The pure helpers (``parse_twitch_vod_id``, ``parse_youtube_id``,
``detect_peaks``, ``shift_peaks``, ``window_heat``) are standard-library only
and unit-tested.
"""
import json
import math
import os
import re
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

# --- Detector constants (Clips Kitty's chat_moments, verified by hand) ------- #
WINDOW = 5            # seconds a burst is counted over
BASELINE = 45         # seconds of chat before it that set the normal rate
SPIKE = 3.0           # a burst this many times the normal rate is a moment
MIN_BURST = 4         # ...and at least this many messages
# A burst with no reaction in it (no emote or word a class knows) is weaker
# evidence, so it has to stand out more.
PLAIN_SPIKE = 4.0
PLAIN_MIN_BURST = 6
MAX_EVENTS = 60
MERGE_SECONDS = 10    # bursts this close together are one moment
LAG_SECONDS = 6.0     # chat reacts after the moment; date peaks this much earlier
PRE_ROLL = 15.0       # a peak's span opens this far before the moment
POST_ROLL = 15.0      # ...and closes this far after it

# --- Reaction classes (words / phrases / emote suffixes / patterns) --------- #
# A trimmed port of Clips Kitty's config/gaming.yaml chat_classes: the
# classes that actually mark a moment, in any language chat types in.
CHAT_CLASSES = {
    "hype": {
        "label": "hype",
        "words": ["POG", "POGGERS", "PogU", "PogChamp", "POGGIES", "PagMan",
                  "LETSGO", "LETSGOOO", "INSANE", "insane", "HOW", "HOLY",
                  "CLUTCH", "clutch", "W", "WW", "GIGACHAD", "EZ", "Clap",
                  "GG", "gg", "Kreygasm", "SeemsGood", "CurseLit", "PopNemo",
                  "WOW", "wow", "OMG", "omg", "no way", "let's go", "lets go",
                  "holy shit", "oh my god", "brutal", "increible",
                  "🔥", "😱", "🤯", "🐐", "👏", "💪", "🏆"],
        "suffixes": ["Pog", "POG", "Hype", "HYPE", "Clap", "GG", "Win", "WIN",
                     "W", "Cheer", "Fire", "Goat", "GOAT", "Wow", "WOW"],
        "patterns": [r"^6{3,}$", r"^(gg)+$"],
    },
    "funny": {
        "label": "laughing",
        "words": ["LUL", "LULW", "KEKW", "OMEGALUL", "ICANT", "KEK", "LOL",
                  "lol", "LMAO", "lmao", "LMFAO", "lmfao", "xD", "XD",
                  "pepeLaugh", "HAHAHA", "hahaha", "4Head", "Jebaited",
                  "EleGiggle", "mdr", "MDR", "ptdr", "😂", "🤣", "💀"],
        "suffixes": ["LUL", "LOL", "KEKW", "KEK", "Laugh", "LAUGH", "Lmao",
                     "LMAO", "Lol", "Haha", "HAHA"],
        "patterns": [r"^[wｗ]{2,}$", r"^(ja){2,}j?a?$", r"^(ha){2,}h?$",
                     r"^k{4,}$", r"^x+d+$", r"^(lo)+l$", r"^(ah){2,}$",
                     r"^(he){3,}$"],
    },
    "surprise": {
        "label": "surprised",
        "words": ["HUH", "WHAT", "wtf", "WTF", "?!",
                  "😮", "😯", "🫢"],
        "suffixes": ["Huh", "HUH", "What", "WHAT", "Shock", "Omg", "OMG"],
        "message_patterns": [r"^[?？!！]{2,}$", r"^hu+h+[?!]*$",
                             r"^wha+t+[?!]*$"],
    },
    "scare": {
        "label": "scared",
        "words": ["monkaS", "monkaW", "monkaGIGA", "D:", "WutFace", "AAAA",
                  "AAAAA", "JUMPSCARE", "jump scare", "😨", "😰", "😳"],
        "suffixes": ["Scared", "Fear", "Monka", "Spooky", "Scream"],
        "patterns": [r"^a{4,}h*$"],
    },
    "fail": {
        "label": "a fail",
        "words": ["F", "L", "LL", "Sadge", "BibleThump", "NotLikeThis",
                  "BigSad", "FeelsBadMan", "RIP", "rip", "NOOO", "THREW",
                  "threw", "copium", "Copium", "😭", "💔"],
        "suffixes": ["Sad", "SAD", "Cry", "CRY", "Rip", "RIP", "F", "L",
                     "Dead"],
        "patterns": [r"^no{3,}$"],
    },
    "clip": {
        "label": "viewers asking for a clip",
        "words": ["clip it", "CLIP IT", "clipit", "clip", "CLIP"],
        "suffixes": [],
        "patterns": [r"\bclip(s|it|ped|per|ping)?\b"],
    },
}

# A message made only of these is a greeting, not a reaction to a moment.
CHAT_IGNORE = {
    "hi", "hii", "hiii", "hello", "hey", "heyy", "heyyy", "yo", "sup",
    "gl", "hf", "gg", "good luck", "have fun", "first", "hype",
}

_TOKEN_RE = re.compile(r"[\w'’]+", re.UNICODE)
_CHANNEL_EMOTE_RE = re.compile(r"[a-z][a-z0-9]{1,24}([A-Z0-9][\w]*)")


def _matchers(classes):
    """Per class: exact whole-word tokens, phrases, emote suffixes, regexes."""
    out = {}
    for key, spec in (classes or {}).items():
        tokens, phrases = set(), []
        for w in spec.get("words") or []:
            w = str(w)
            if " " in w or not re.fullmatch(r"[\w+':]+", w) or not w.isascii():
                phrases.append(w.lower())
            else:
                tokens.add(w)
        suffixes = {str(x) for x in spec.get("suffixes") or []}
        patterns = [re.compile(str(p), re.IGNORECASE)
                    for p in spec.get("patterns") or []]
        whole = [re.compile(str(p), re.IGNORECASE)
                 for p in spec.get("message_patterns") or []]
        out[key] = (tokens, phrases, spec.get("label", key), suffixes,
                    patterns, whole)
    return out


def _classify(text, matchers):
    """The classes one message belongs to, with the words that put it there."""
    found = {}
    tokens = set(_TOKEN_RE.findall(text or ""))
    pieces = set((text or "").split()) | tokens
    low = (text or "").lower()
    emote_ends = {m.group(1): tok for tok in tokens
                  if (m := _CHANNEL_EMOTE_RE.fullmatch(tok))}
    message = (text or "").strip()
    for key, (words, phrases, _label, suffixes, patterns, whole) in matchers.items():
        hits = [w for w in words if w in tokens]
        hits += [p for p in phrases if p in low]
        hits += [tok for end, tok in emote_ends.items() if end in suffixes]
        hits += [p for p in pieces for rx in patterns if rx.search(p)][:3]
        hits += [message for rx in whole if rx.search(message)][:1]
        if hits:
            found[key] = hits
    return found


def _short(text):
    """A reaction-length message (an emote, "www", "no way"), not a discussion."""
    t = (text or "").strip()
    return len(t) <= 14 or len(t.split()) <= 3


def _only(text, ignore):
    """A message made only of ignored words (a greeting)."""
    words = {w.lower() for w in _TOKEN_RE.findall(text or "")}
    return bool(words) and words <= ignore


# --------------------------------------------------------------------------- #
# URL parsing
# --------------------------------------------------------------------------- #
_VOD_PATH = re.compile(r"^/(?:[^/]+/)?(?:videos?|v)/(\d+)/?$")


def parse_twitch_vod_id(url) -> Optional[str]:
    """The numeric VOD id of a ``twitch.tv/videos/<id>`` link, else None."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ("twitch.tv", "www.twitch.tv", "m.twitch.tv"):
        return None
    m = _VOD_PATH.match(parsed.path or "")
    return m.group(1) if m else None


def parse_youtube_id(url) -> Optional[str]:
    """The video id of a YouTube watch / shorts / live / youtu.be link."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        vid = (parsed.path or "").strip("/").split("/")[0]
        return vid or None
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com",
                "music.youtube.com"):
        q = parse_qs(parsed.query or "")
        if q.get("v"):
            return q["v"][0]
        m = re.match(r"^/(shorts|live|embed|v)/([\w-]{6,})", parsed.path or "")
        if m:
            return m.group(2)
    return None


# --------------------------------------------------------------------------- #
# Detection (pure stdlib port of Clips Kitty's chat_signal)
# --------------------------------------------------------------------------- #
def detect_peaks(messages, duration, lag=LAG_SECONDS):
    """``[{"start", "end", "score", "label", "said"}]`` in VOD seconds.

    ``messages`` is ``[(offset_seconds, author, text)]``. A peak's ``start``
    is already moved earlier by ``lag`` (chat reacts after the moment), and
    ``start``/``end`` bracket the moment with a pre/post roll so the picker
    can cut from before it. ``score`` is the burst ratio (how many times the
    normal rate). Empty when there is too little chat to tell anything from.
    """
    n = int(duration) + 1
    if n < WINDOW * 4 or not messages:
        return []
    if len(messages) < duration / 60:
        return []
    matchers = _matchers(CHAT_CLASSES)
    ignored = {w.lower() for w in CHAT_IGNORE}
    rate = [0] * n
    short = [0] * n
    per_class = {k: [0] * n for k in matchers}
    words_at = {}
    for t, _who, text in messages:
        s = int(t)
        if not 0 <= s < n:
            continue
        if re.fullmatch(r"\d+", (text or "").strip()):
            continue  # a bare number is a poll vote, not a reaction
        found = _classify(text, matchers)
        if not found and _only(text, ignored):
            continue  # a hello isn't a reaction to anything
        rate[s] += 1
        if _short(text):
            short[s] += 1
        for key, hits in found.items():
            per_class[key][s] += 1
            bucket = words_at.setdefault(s, {})
            for h in hits:
                bucket[h] = bucket.get(h, 0) + 1

    def window_sum(x, width):
        """Messages in the ``width`` seconds ending at t (looking back only)."""
        c = [0]
        for v in x:
            c.append(c[-1] + v)
        return [c[i + 1] - c[max(i + 1 - width, 0)] for i in range(n)]

    burst = window_sum(rate, WINDOW)
    # The normal rate: messages per 5 s over the BASELINE seconds before that
    # window, floored so a near-silent chat doesn't turn three messages into
    # a spike.
    before = [0]
    for v in rate:
        before.append(before[-1] + v)
    ratio = []
    for i in range(n):
        hi = max(i + 1 - WINDOW, 0)
        lo = max(hi - BASELINE, 0)
        span = max(hi - lo, 1)
        normal = (before[hi] - before[lo]) / span * WINDOW
        ratio.append(burst[i] / max(normal, _floor(burst)))
    reacting = [0] * n
    for v in per_class.values():
        w = window_sum(v, WINDOW)
        for i in range(n):
            reacting[i] += w[i]
    share = [min(reacting[i] / max(burst[i], 1), 1.0) for i in range(n)]
    brief = [min(window_sum(short, WINDOW)[i] / max(burst[i], 1), 1.0)
             for i in range(n)]
    strength = []
    for i in range(n):
        s = min(max((ratio[i] - 1.0) / (2 * SPIKE - 1.0), 0.0), 1.0)
        strength.append(s * (0.6 + 0.4 * share[i]) * (0.5 + 0.5 * brief[i]))

    shift = max(0, int(round(lag)))
    # Events: where a burst starts, dated back by the lag, named by what
    # chat said.
    events = []
    hot = [(ratio[i] >= SPIKE and burst[i] >= MIN_BURST) for i in range(n)]
    s = 0
    while s < n:
        if not hot[s]:
            s += 1
            continue
        e = s
        while e + 1 < n and hot[e + 1]:
            e += 1
        counts, klass = {}, {}
        for sec in range(s, min(e + WINDOW, n)):
            for w, c in (words_at.get(sec) or {}).items():
                counts[w] = counts.get(w, 0) + c
            for key in per_class:
                klass[key] = klass.get(key, 0) + int(per_class[key][sec])
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        kind = max(klass, key=klass.get) if klass and max(klass.values()) > 0 else None
        if kind is None and (max(ratio[s:e + 1]) < PLAIN_SPIKE
                             or max(burst[s:e + 1]) < PLAIN_MIN_BURST
                             or max(brief[s:e + 1]) < 0.5):
            s = e + 1  # chat talking, not reacting: not a moment
            continue
        label = matchers[kind][2] if kind else "a burst of messages"
        said = ", ".join(f"{w} x{c}" for w, c in top)
        peak_t = max(0.0, float(s - shift))
        text = f"CHAT: {label}" + (f" ({said})" if said
                                    else f" ({int(max(burst[s:e + 1]))} messages in {WINDOW}s)")
        events.append((peak_t, text, float(max(ratio[s:e + 1])),
                       float(max(strength[s:e + 1]))))
        s = e + 1
    # Bursts a few seconds apart are one moment chat kept reacting to: the
    # first names it, the strongest's words describe it.
    merged = []
    for ev in sorted(events, key=lambda ev: ev[0]):
        if merged and ev[0] - merged[-1][3] <= MERGE_SECONDS:
            first = merged[-1]
            best = ev if ev[2] > first[2] else first
            merged[-1] = (first[0], best[1], max(first[2], ev[2]),
                          max(first[3], ev[3]), ev[0])
        else:
            merged.append((*ev, ev[0]))
    merged.sort(key=lambda ev: -ev[2])
    out = []
    for t, txt, ratio_v, _strength, _last in merged[:MAX_EVENTS]:
        label = txt[len("CHAT: "):].split(" (")[0]
        said = ""
        m = re.search(r"\((.*)\)$", txt)
        if m:
            said = m.group(1)
        out.append({"start": round(max(0.0, t - PRE_ROLL), 3),
                    "end": round(t + POST_ROLL, 3),
                    "peak": round(t, 3),
                    "score": round(ratio_v, 2),
                    "label": label,
                    "said": said})
    out.sort(key=lambda p: p["peak"])
    return out


def _floor(burst):
    """Median burst, floored at 1: a near-silent chat must not turn three
    messages into a spike."""
    s = sorted(burst)
    mid = s[len(s) // 2] if s else 0
    return max(float(mid), 1.0)


def shift_peaks(peaks, start_seconds, duration):
    """Move VOD-time peaks into the time of the file being clipped.

    When the user clipped only a stretch of the source, the local file starts
    there. Peaks are kept when their peak falls inside the file, and clamped
    to it.
    """
    out = []
    start = float(start_seconds or 0)
    for p in peaks or []:
        try:
            s = float(p["start"]) - start
            e = float(p["end"]) - start
            peak = float(p.get("peak", (s + e) / 2)) - start
        except (KeyError, TypeError, ValueError):
            continue
        if peak < 0 or peak > duration:
            continue
        out.append({"start": round(max(0.0, s), 3),
                    "end": round(min(duration, e), 3),
                    "peak": round(max(0.0, peak), 3),
                    "score": float(p.get("score") or 0),
                    "label": str(p.get("label") or ""),
                    "said": str(p.get("said") or "")})
    return out


def window_heat(windows, peaks):
    """``{window_id: {"clips": n, "views": v, "spans": [[s, e], ...],
    "labels": [...]}}`` — the same shape ``twitch_signals.window_heat``
    returns, so the picker can merge chat heat with viewer-clip heat and
    promote the hottest windows the same way. A peak belongs to the window
    holding its peak time, so one peak is never counted twice. ``views`` is
    the summed peak score x100, so hotter peaks outrank more peaks.
    """
    heat = {}
    for p in peaks or []:
        mid = float(p.get("peak", (p["start"] + p["end"]) / 2))
        for w in windows:
            if w["start"] <= mid < w["end"] or (mid == w["end"] and w is windows[-1]):
                h = heat.setdefault(w["id"], {"clips": 0, "views": 0,
                                              "spans": [], "labels": []})
                h["clips"] += 1
                h["views"] += int(float(p.get("score") or 0) * 100)
                h["spans"].append([p["start"], p["end"]])
                if p.get("label"):
                    h["labels"].append(p["label"])
                break
    for h in heat.values():
        h["spans"].sort()
    return heat


# --------------------------------------------------------------------------- #
# Chat fetching
# --------------------------------------------------------------------------- #
_GQL_URL = "https://gql.twitch.tv/gql"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_TWITCH_TIME_BUDGET_S = 180.0
_TWITCH_MAX_PAGES = 800
_COMMENTS_QUERY = """
query($videoID: ID!, $offset: Int) {
  video(id: $videoID) {
    comments(contentOffsetSeconds: $offset) {
      edges { node { contentOffsetSeconds commenter { id } message { fragments { text } } } }
      pageInfo { hasNextPage }
    }
  }
}"""


def _scrape_twitch_client_id(timeout=20.0):
    """The Twitch web client-id, scraped live from twitch.tv so it never
    rots (hardcoded ids do)."""
    req = urllib.request.Request("https://www.twitch.tv/",
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'clientId\s*[=:]\s*"([a-z0-9]{20,40})"', html)
    if not m:
        raise RuntimeError("could not find Twitch web client id")
    return m.group(1)


def fetch_twitch_chat(vod_id, duration, timeout=20.0):
    """``[(offset_seconds, author, text)]`` for a Twitch VOD's chat replay.

    Paged by ``contentOffsetSeconds``, NOT cursors: cursor pagination trips
    Twitch's client-integrity check, while offset queries — "give me the chat
    page at second X" — pass. Each page spans until its last message; stepping
    to last+1 walks the whole VOD. Overlaps are deduped; deleted/anonymous
    accounts are skipped (also drops most bot noise). Empty on any failure.
    """
    if not vod_id:
        return []
    try:
        client_id = _scrape_twitch_client_id(timeout=timeout)
    except Exception as e:
        print(f"   ℹ️ Twitch chat: client-id unavailable ({type(e).__name__}: {e}).")
        return []
    seen = set()
    offset = 0.0
    deadline = time.monotonic() + _TWITCH_TIME_BUDGET_S
    try:
        for _ in range(_TWITCH_MAX_PAGES):
            body = json.dumps(
                {"query": _COMMENTS_QUERY,
                 "variables": {"videoID": str(vod_id), "offset": int(offset)}}
            ).encode()
            req = urllib.request.Request(
                _GQL_URL, data=body,
                headers={"Client-ID": client_id,
                         "Content-Type": "application/json",
                         "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            video = (data.get("data") or {}).get("video")
            if not video or not video.get("comments"):
                break
            edges = video["comments"]["edges"]
            if not edges:
                break
            last = 0.0
            for e in edges:
                node = e["node"]
                commenter = node.get("commenter") or {}
                last = max(last, float(node["contentOffsetSeconds"]))
                if commenter.get("id"):
                    fragments = ((node.get("message") or {}).get("fragments") or [])
                    text = "".join(str(f.get("text") or "") for f in fragments)
                    seen.add((float(node["contentOffsetSeconds"]),
                              commenter["id"], text))
            if last >= duration or last < offset + 1:
                break
            offset = last + 1
            if time.monotonic() > deadline:
                print("      (Twitch chat fetch time budget hit — using the part fetched)")
                break
    except Exception as e:  # best effort: never fail a job over a hint
        print(f"   ℹ️ Twitch chat unavailable ({type(e).__name__}: {e}).")
        return []
    return sorted(seen)


def fetch_youtube_chat(video_id, url, duration, timeout=120.0):
    """``[(offset_seconds, author, text)]`` for a YouTube live stream's chat
    replay, via yt-dlp's ``live_chat`` subtitle track. Regular uploads have
    no such track -> empty. Empty on any failure.

    Bounded by ``timeout`` seconds: a long stream's live-chat file can be
    tens of MB, and a slow download must not stall the whole job — the chat
    signal is best effort, so a timeout just means the job proceeds without
    it (the same as a stream with no chat at all).
    """
    if not video_id or not url:
        return []
    try:
        import yt_dlp
    except ImportError:
        return []
    import tempfile
    import threading
    from pathlib import Path

    result = {"messages": []}

    def _fetch():
        try:
            with tempfile.TemporaryDirectory() as td:
                # nocheckcertificate + the saved cookies match main.py's
                # download path (this VM's network intercepts TLS; YouTube
                # needs the cookies for age-gated / members-only replays).
                cookies_path = "/app/cookies.txt"
                if not os.path.exists(cookies_path):
                    cookies_path = None
                opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                        "writesubtitles": True, "subtitleslangs": ["live_chat"],
                        "noplaylist": True, "nocheckcertificate": True,
                        "socket_timeout": 30, "retries": 3,
                        "cookiefile": cookies_path,
                        "outtmpl": str(Path(td) / "chat.%(ext)s")}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if "live_chat" not in (info.get("subtitles") or {}):
                    return
                files = list(Path(td).glob("*.live_chat.json"))
                if not files:
                    return
                out = []
                with open(files[0], encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            action = json.loads(line)
                            replay = action["replayChatItemAction"]
                            offset = float(replay.get("videoOffsetTimeMsec", 0)) / 1000.0
                            item = replay["actions"][0]["addChatItemAction"]["item"]
                            renderer = item.get("liveChatTextMessageRenderer")
                            if not renderer:
                                continue
                            author = renderer.get("authorExternalChannelId", "")
                            if not author:
                                continue
                            parts = []
                            for run in (renderer.get("message") or {}).get("runs") or []:
                                if "text" in run:
                                    parts.append(str(run["text"]))
                                elif "emoji" in run:
                                    e = run["emoji"]
                                    parts.append(e.get("shortcuts", [e.get("emojiId", "")])[0])
                            out.append((offset, author, "".join(parts)))
                        except (KeyError, IndexError, ValueError, json.JSONDecodeError):
                            continue
                result["messages"] = out
        except Exception as e:  # best effort: never fail a job over a hint
            print(f"   ℹ️ YouTube chat unavailable ({type(e).__name__}: {e}).")

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=float(timeout or 120))
    if t.is_alive():
        print(f"   ℹ️ YouTube chat fetch exceeded {timeout:.0f}s; continuing without it.")
        return []
    return result["messages"]


def chat_peaks_for_job(url, duration):
    """Chat peaks for the file this job clips, in its own seconds.

    Twitch VODs fetch chat replay; YouTube live replays fetch live chat;
    Kick (and everything else) has no post-stream chat to read and returns
    []. Reads ``SOURCE_START_SECONDS`` when the job clips a stretch of the
    source, like ``main.cap_source_duration`` does.
    """
    vod_id = parse_twitch_vod_id(url)
    if vod_id:
        messages = fetch_twitch_chat(vod_id, float(duration or 0) or math.inf)
        source = "Twitch"
    else:
        yt_id = parse_youtube_id(url)
        if not yt_id:
            return []
        messages = fetch_youtube_chat(yt_id, url, float(duration or 0) or math.inf)
        source = "YouTube"
    if not messages:
        return []
    peaks = detect_peaks(messages, float(duration or 0) or
                         (max((m[0] for m in messages), default=0) + 60))
    if not peaks:
        return []
    start = 0.0
    if os.environ.get("MAX_SOURCE_MINUTES", "").strip():
        try:
            start = float(os.environ.get("SOURCE_START_SECONDS", "0") or 0)
        except ValueError:
            start = 0.0
    shifted = shift_peaks(peaks, start, float(duration or 0) or math.inf)
    print(f"   💬 {source} chat: {len(messages)} message(s), "
          f"{len(shifted)} peak(s) inside this source.")
    return shifted
