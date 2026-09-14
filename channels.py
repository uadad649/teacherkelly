#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
channels.py — 매일 볼 영상을 채널에서 골라 온다
================================================

자동 실행에는 주소를 넣어 줄 사람이 없다. 그래서 'channels.txt' 에 적어 둔
유튜브 채널들의 최신 영상 중 아직 안 본 것을 골라 온다.

**yt-dlp 로 채널을 긁지 않고 유튜브 RSS 피드를 쓴다.**
유튜브는 데이터센터(=GitHub 서버)에서 오는 스크래핑 요청을 자주 막지만,
RSS 피드는 원래 공개용으로 열어 둔 주소라 훨씬 덜 막힌다. API 키도 필요 없다.

  https://www.youtube.com/feeds/videos.xml?channel_id=UC...

한 번 고른 영상은 '본영상.json' 에 적어 두고 다시 고르지 않는다.
자막이 없어 실패한 영상도 적어 둔다 — 그러지 않으면 매일 같은 영상에
걸려 넘어져 자동 실행이 영영 앞으로 못 나간다.
"""

import json
import os
import re
import urllib.request
import urllib.error
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

import adult_local as core

HERE = Path(__file__).resolve().parent
LIST = HERE / "channels.txt"
SEEN = HERE / "본영상.json"

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


def _get(url, timeout=20):
    """프록시 설정이 있으면 그것을 태워서 가져온다."""
    p = core._proxy_dict()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": p, "https": p} if p else {}))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as f:
        return f.read().decode("utf-8", "replace")


# ── channels.txt 읽기 ────────────────────────────────────────

def read_channels():
    """한 줄에 채널 하나. '#' 뒤는 설명으로 보고 무시한다."""
    if not LIST.exists():
        return []
    out = []
    for line in LIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def resolve_channel_id(ref):
    """'@handle' / 채널 주소 / 'UC...' 무엇이든 채널 ID 로 바꾼다."""
    ref = ref.strip()
    m = re.search(r"(UC[A-Za-z0-9_-]{22})", ref)
    if m:                                   # 이미 채널 ID 다
        return m.group(1)

    if ref.startswith("@"):
        url = f"https://www.youtube.com/{ref}"
    elif ref.startswith("http"):
        url = ref
    else:
        url = f"https://www.youtube.com/@{ref}"

    try:
        html = _get(url)
    except (urllib.error.URLError, OSError) as e:
        print(f"    [i] 채널을 못 읽었습니다: {ref} ({type(e).__name__})")
        return None
    m = re.search(r'"(?:channelId|externalId)"\s*:\s*"(UC[A-Za-z0-9_-]{22})"', html)
    if not m:
        print(f"    [i] 채널 ID 를 못 찾았습니다: {ref}")
        return None
    return m.group(1)


def recent_videos(channel_id):
    """RSS 피드에서 최신 영상 목록. [{id, title, published}]"""
    try:
        xml = _get(FEED.format(channel_id))
    except (urllib.error.URLError, OSError) as e:
        print(f"    [i] 피드를 못 읽었습니다: {channel_id} ({type(e).__name__})")
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        print(f"    [i] 피드 형식이 이상합니다: {channel_id}")
        return []

    out = []
    for e in root.findall("a:entry", NS):        # 피드 전체가 아니라 항목별로
        vid = e.findtext("yt:videoId", None, NS)
        if not vid:
            continue
        out.append({"id": vid,
                    "title": (e.findtext("a:title", "", NS) or "").strip(),
                    "published": e.findtext("a:published", "", NS) or ""})
    return out


# ── 정규 영상만 골라내기 ─────────────────────────────────────
#
# RSS 는 쇼츠와 정규 영상을 섞어서 준다. 길이도 알려주지 않는다.
# 그래서 쇼츠가 후보 자리를 다 차지하는 날이면 자막을 받아 보고 나서야
# "5줄뿐이라 건너뜁니다" 로 버리게 된다. 하루에 다섯 번 그러고 끝난 날도 있었다.
#
# 채널의 '동영상' 탭에는 **쇼츠가 아예 들어 있지 않고** 길이도 붙어 있다.
# 한 채널에 요청 한 번, 1초면 받는다. 그래서 이것을 정규 영상 명단으로 쓴다.

def longform(channel_id, n=40):
    """채널 '동영상' 탭 → {영상ID: 길이초}. 못 읽으면 None.

    None 은 '거를 수 없다'는 뜻이지 '정규 영상이 없다'는 뜻이 아니다.
    그때는 예전처럼 RSS 만 보고 간다 — 느릴 뿐 못 돌지는 않는다.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None

    class _Hush:
        def debug(self, m): pass
        def info(self, m): pass
        def warning(self, m): pass
        def error(self, m): pass

    o = {"quiet": True, "no_warnings": True, "logger": _Hush(),
         "extract_flat": "in_playlist", "playlistend": n}
    if core._proxy_dict():
        o["proxy"] = core._proxy_dict()
    try:
        with YoutubeDL(o) as y:
            info = y.extract_info(
                f"https://www.youtube.com/channel/{channel_id}/videos",
                download=False)
    except Exception as e:
        print(f"    [i] 동영상 탭을 못 읽어 RSS 만 봅니다: {type(e).__name__}")
        return None

    out, paywalled = {}, 0
    for rank, e in enumerate((info or {}).get("entries") or []):
        if not e or not e.get("id"):
            continue
        # 멤버십 전용은 자막을 못 받는다. 이 채널의 '원어민 브이로그' 가 그렇다.
        # 여기서 안 거르면 후보로 올라와 자막을 받아 보고서야 실패한다.
        avail = e.get("availability")
        if avail not in (None, "public"):
            paywalled += 1
            continue
        # 탭은 최신순으로 온다. rank 는 RSS 에 없는(오래된) 영상끼리의 순서다.
        out[e["id"]] = {"sec": int(e.get("duration") or 0),
                        "title": (e.get("title") or "").strip(),
                        "rank": rank}
    if paywalled:
        print(f"    [i] 멤버십 전용 {paywalled}편은 후보에서 뺐습니다.")
    return out or None


# ── 본 영상 기록 ─────────────────────────────────────────────

def load_seen():
    if not SEEN.exists():
        return []
    try:
        d = json.loads(SEEN.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []


# 잠깐 실패한 영상을 몇 번까지 다시 집어 볼지.
# 유튜브 자막 서버가 삐끗한 날 후보를 영영 태워 버리지 않기 위한 것이다.
MAX_TRIES = 3


def seen_ids():
    """다시 고르지 않을 영상들.

    '쇼츠라서' 처럼 내일 다시 해도 똑같을 일은 영영 제외한다.
    '자막 서버가 응답을 안 해서' 처럼 잠깐의 일이면 몇 번 더 기회를 준다.
    그러지 않으면 유튜브가 한 번 막은 날 그날 후보가 통째로 사라진다.
    """
    ids = set()
    for w in load_seen():
        if not w.get("id"):
            continue
        # permanent 키가 없는 예전 기록은 전부 '영영'으로 본다. 지금까지와 같다.
        if w.get("permanent", True) or w.get("tries", 0) >= MAX_TRIES:
            ids.add(w["id"])
    # words.json 에 이미 들어간 영상도 본 것으로 친다.
    # (수동으로 만든 시트를 자동 실행이 또 만들지 않게)
    if core.WORDS_DB.exists():
        try:
            for w in json.loads(core.WORDS_DB.read_text(encoding="utf-8")):
                ids.add(w.get("vid"))
        except json.JSONDecodeError:
            pass
    ids.discard(None)
    return ids


def mark_seen(vid, title, result, permanent=True):
    """result 는 '완료' 또는 실패 사유.

    permanent=False 는 '오늘은 안 됐지만 내일은 될 수도 있다'는 뜻이다.
    그때는 시도 횟수를 세어 두고 MAX_TRIES 번까지 다시 집어 본다.
    """
    old = next((w for w in load_seen() if w.get("id") == vid), {})
    rows = [w for w in load_seen() if w.get("id") != vid]
    row = {"id": vid, "title": title,
           "date": dt.date.today().isoformat(), "result": result}
    if not permanent:
        row["permanent"] = False
        row["tries"] = int(old.get("tries", 0)) + 1
    rows.append(row)
    SEEN.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                    encoding="utf-8")


# ── 오늘 볼 후보 ─────────────────────────────────────────────

# 이보다 긴 것은 몰아보기·라이브·설명회로 본다. 강의가 아니라서 시트가 안 나온다.
# 이 채널의 정상 강의는 2~13분이고, 20분이 넘어가면 전부 1시간짜리 모음집이었다.
# 자막을 받아 보기 전에 여기서 걸러야 헛걸음을 안 한다. 0 이면 안 거른다.
MAX_SECONDS = int(os.environ.get("MAX_VIDEO_SECONDS", "1800") or 0)


def candidates(limit=12):
    """오늘 만들어 볼 영상 후보. 아직 안 본 것 중 최신 순.

    쇼츠는 여기까지 오지 않는다 — 채널 '동영상' 탭에 없는 것은 후보로 치지 않는다.
    예전에는 쇼츠가 후보 다섯 자리를 다 차지해서, 자막을 다섯 번 받아 보고
    전부 버린 채 하루가 끝나는 날이 있었다.

    RSS 는 날짜를 주고(여러 채널을 섞어 최신순으로 세우려면 날짜가 있어야 한다),
    '동영상' 탭은 쇼츠를 걸러 주고 길이를 준다. 둘을 겹쳐서 쓴다.
    """
    refs = read_channels()
    if not refs:
        return []

    done = seen_ids()
    fresh, older, dup = [], [], set()

    for ref in refs:
        cid = resolve_channel_id(ref)
        if not cid:
            continue
        longs = longform(cid)               # {id: {sec,title,rank}} 또는 None
        feed = recent_videos(cid)

        def take(vid, title, published, rank):
            """후보 한 편을 담는다. 담을 수 없으면 이유를 남기고 거른다."""
            if vid in done or vid in dup:
                return
            info = (longs or {}).get(vid)
            if longs is not None and info is None:
                return                      # 동영상 탭에 없다 = 쇼츠
            sec = info["sec"] if info else 0
            if MAX_SECONDS and sec > MAX_SECONDS:
                print(f"    [i] {sec // 60}분이라 건너뜁니다 (모음집·라이브): {title[:36]}")
                return
            dup.add(vid)
            (fresh if published else older).append(
                {"id": vid, "title": title, "published": published,
                 "channel": ref, "sec": sec, "rank": rank})

        for v in feed:                      # 1) 최근에 올라온 것 (날짜 있음)
            take(v["id"], v["title"], v["published"], 0)

        for vid, info in (longs or {}).items():   # 2) 그보다 오래된 것 (예비)
            take(vid, info["title"], "", info["rank"])

    # 새 영상이 없는 날에도 아직 안 본 예전 영상으로 이어 간다.
    fresh.sort(key=lambda v: v["published"], reverse=True)
    older.sort(key=lambda v: v["rank"])
    return (fresh + older)[:limit]
