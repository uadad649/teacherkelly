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


# ── 본 영상 기록 ─────────────────────────────────────────────

def load_seen():
    if not SEEN.exists():
        return []
    try:
        d = json.loads(SEEN.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []


def seen_ids():
    ids = {w.get("id") for w in load_seen()}
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


def mark_seen(vid, title, result):
    """result 는 '완료' 또는 실패 사유. 어느 쪽이든 다시 고르지 않는다."""
    rows = [w for w in load_seen() if w.get("id") != vid]
    rows.append({"id": vid, "title": title,
                 "date": dt.date.today().isoformat(), "result": result})
    SEEN.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                    encoding="utf-8")


# ── 오늘 볼 후보 ─────────────────────────────────────────────

def candidates(limit=5):
    """아직 안 본 영상을 최신 순으로. 없으면 빈 목록."""
    refs = read_channels()
    if not refs:
        return []

    done = seen_ids()
    pool, dup = [], set()
    for ref in refs:
        cid = resolve_channel_id(ref)
        if not cid:
            continue
        for v in recent_videos(cid):
            if v["id"] in done or v["id"] in dup:
                continue
            dup.add(v["id"])
            v["channel"] = ref
            pool.append(v)

    pool.sort(key=lambda v: v["published"], reverse=True)
    return pool[:limit]
