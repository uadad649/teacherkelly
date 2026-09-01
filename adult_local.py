#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adult_local.py — 성인 영어(왕초보) 복습 시트
=============================================

애니메이션판(anime_local.py)과 같은 뼈대지만, 어른이 혼자 공부하는 데
맞췄다. 읽어주는 부모가 없고, 배우는 사람이 곧 쓰는 사람이다.

두 종류의 영상을 모두 받는다.
  1) 영어 영상 (팟캐스트·브이로그·TED 등)  → 영어 자막을 그대로 재료로 쓴다
  2) 한국어 영어강의 영상                  → 한국어 설명 속에 섞인
     영어 문장만 뽑아내 재료로 쓴다

흐름
  python adult_local.py auto <유튜브주소>
     → 자막받기부터 시트까지 한 번에. 이것만 쓰면 된다.

손으로 할 때
  1) python adult_local.py prep <유튜브주소>   → '붙여넣기.txt'
  2) 그 내용을 Claude 채팅창에 붙여넣는다
  3) 돌아온 JSON 을 '응답.json' 으로 저장
  4) python adult_local.py build              → HTML 을 만들고 연다

자막만 받고 싶을 때
  python adult_local.py srt <유튜브주소>

GitHub Actions 에서 돌릴 때는 main.py 가 입구다. (README.md 참고)

자막 우선순위
  영어 수동 → 영어 자동(ASR) → 한국어 수동 → 한국어 자동(ASR)
  → (다 없으면) 음성인식 Whisper

준비
  pip install yt-dlp youtube-transcript-api
  pip install anthropic           # 'auto' 를 API 키로 돌리려면
  pip install faster-whisper      # 자막이 아예 없는 영상까지 하려면(선택)
"""

import bisect
import html
import json
import os
import re
import sys
import subprocess
import webbrowser
import datetime as dt
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "결과"
PASTE = HERE / "붙여넣기.txt"
REPLY = HERE / "응답.json"
STATE = HERE / "진행중.json"
WORDS_DB = HERE / "words.json"
TEMPLATE = HERE / "sheet_template.html"

def _flag(name, default):
    """환경변수로 켜고 끄기. 없으면 기본값."""
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "y", "on")


# ── 어디서 도는가 ────────────────────────────────────────────
# GitHub Actions 같은 서버에는 화면도 브라우저도 메모장도 없다.
# 파일을 열려고 하면 조용히 아무 일도 안 일어나거나 멈춘다.
HEADLESS = _flag("ADULT_HEADLESS", os.environ.get("GITHUB_ACTIONS") == "true")

MIN_REPEAT = 2
ALLOW_ASR = True
# 음성인식은 모델을 내려받고 영상 길이만큼 CPU 를 쓴다.
# 서버에서는 기본으로 끈다 — 몇 분짜리 실행이 30분이 되는 걸 막는다.
ALLOW_WHISPER = _flag("ALLOW_WHISPER", not HEADLESS)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")  # 한국어 영상도 있으므로 영어 전용(.en) 모델을 쓰지 않는다

# 자막이 '있는데' 서버 오류로 못 받았을 때도 음성인식으로 대신할지.
# 기본은 False — 잠깐의 오류 때문에 더 부정확한 자막이 굳는 걸 막는다.
ALLOW_WHISPER_ON_FETCH_FAIL = _flag("ALLOW_WHISPER_ON_FETCH_FAIL", False)
N_REVIEW = 8

# 유튜브는 데이터센터 IP(=GitHub 서버)에서 오는 자막 요청을 자주 막는다.
# 프록시를 넣어 두면 그때 대신 나간다. 없으면 그냥 직접 나간다.
WEBSHARE_USER = os.environ.get("WEBSHARE_PROXY_USERNAME", "").strip()
WEBSHARE_PASS = os.environ.get("WEBSHARE_PROXY_PASSWORD", "").strip()
PROXY_URL = os.environ.get("YT_PROXY_URL", "").strip()

# ── 배우는 사람 ──────────────────────────────────────────────
# 수준이 올라가면 level 과 개수를 고쳐 쓰면 된다.
LEARNER = {
    "name": "영준",
    "level": ("왕초보. 영어 문장을 눈으로 읽을 수는 있지만 입에서 나오지 않는다. "
              "빠른 영어는 안 들리고, 짧은 문장만 겨우 들린다. "
              "문법을 더 배우는 단계가 아니라, 자주 쓰는 말을 "
              "통째로 외워 바로 뱉는 연습을 하는 단계다."),
    "goal": "여행·일상 대화에서 하고 싶은 말을 한 문장으로 뱉기",
    "n_expr": 8,       # 오늘의 표현
    "n_words": 10,     # 단어
    "n_shadow": 5,     # 쉐도잉 문장
    "n_dict": 4,       # 받아쓰기
}

try:
    from yt_dlp import YoutubeDL
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    sys.exit("[!] pip install yt-dlp youtube-transcript-api")

try:                                        # 1.x 는 최상위에서 내보낸다
    from youtube_transcript_api import TranscriptsDisabled, NoTranscriptFound
except ImportError:                         # 0.6.x
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound


# ─────────────────────────────────────────────────────────────
# youtube-transcript-api 버전 호환
#   0.6.x : YouTubeTranscriptApi.list_transcripts(id)   (정적)
#   1.x   : YouTubeTranscriptApi().list(id)             (인스턴스)
# ─────────────────────────────────────────────────────────────

def _proxy_config():
    """1.x 용 프록시 설정. 환경변수가 비어 있으면 None."""
    if not (WEBSHARE_USER and WEBSHARE_PASS) and not PROXY_URL:
        return None
    try:
        from youtube_transcript_api.proxies import (
            WebshareProxyConfig, GenericProxyConfig)
    except ImportError:
        return None
    if WEBSHARE_USER and WEBSHARE_PASS:
        return WebshareProxyConfig(proxy_username=WEBSHARE_USER,
                                   proxy_password=WEBSHARE_PASS)
    return GenericProxyConfig(http_url=PROXY_URL, https_url=PROXY_URL)


def _proxy_dict():
    """0.6.x 용 / yt-dlp 용 프록시 주소. 없으면 None."""
    if PROXY_URL:
        return PROXY_URL
    if WEBSHARE_USER and WEBSHARE_PASS:
        return f"http://{WEBSHARE_USER}-rotate:{WEBSHARE_PASS}@p.webshare.io:80"
    return None


def _listing(video_id):
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):      # 구버전
        p = _proxy_dict()
        if p:
            return YouTubeTranscriptApi.list_transcripts(
                video_id, proxies={"http": p, "https": p})
        return YouTubeTranscriptApi.list_transcripts(video_id)
    return YouTubeTranscriptApi(proxy_config=_proxy_config()).list(video_id)  # 1.x


def _fetch(transcript):
    """어느 버전이든 [{"text":..., "start":...}] 형태로 돌려준다."""
    data = transcript.fetch()
    if hasattr(data, "to_raw_data"):        # 1.x : FetchedTranscript
        data = data.to_raw_data()
    out = []
    for s in data:
        if isinstance(s, dict):
            out.append({"text": s.get("text", ""),
                        "start": float(s.get("start", 0) or 0),
                        "dur": float(s.get("duration", 0) or 0)})
        else:
            out.append({"text": getattr(s, "text", ""),
                        "start": float(getattr(s, "start", 0) or 0),
                        "dur": float(getattr(s, "duration", 0) or 0)})
    return out


# ══════════════════════════════════════════════ 공통

class PrepFailed(Exception):
    """이 영상으로는 시트를 못 만든다. 다른 영상을 보면 된다.

    자동 실행은 후보를 여러 개 놓고 도는데, 하나가 안 된다고
    그날 실행 전체가 멈추면 안 된다. 그래서 프로그램을 끝내는 대신
    이 예외를 던져 부르는 쪽이 판단하게 한다.
    """


# 자동 실행이 고른 영상이 배울 만한 것인지 자막 길이로 걸러 낸다.
# 둘 다 0 이면 검사하지 않는다. 사람이 직접 고른 영상은 검사하지 않는다.
#
#   너무 짧다 → 쇼츠. 한두 마디뿐이라 시트가 안 나온다.
#   너무 길다 → 라이브·설명회·홍보 영상. 강의가 아닌 데다
#               프롬프트가 지나치게 커져 응답 품질도 떨어진다.
# 10~20분짜리 보통 강의가 200~600줄쯤 된다.
MIN_TRANSCRIPT_LINES = int(os.environ.get("MIN_TRANSCRIPT_LINES", "0") or 0)
MAX_TRANSCRIPT_LINES = int(os.environ.get("MAX_TRANSCRIPT_LINES", "0") or 0)


def vid_of(u):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", u)
    return m.group(1) if m else (u if re.fullmatch(r"[A-Za-z0-9_-]{11}", u) else None)


def sec_of(tc):
    """'MM:SS' / 'H:MM:SS' → 초. 숫자가 없으면(예: '—') 0 을 돌려준다.

    Claude 가 시간을 모를 때 '—' 같은 글자를 넣어 보내는 일이 있다.
    그것 하나 때문에 시트 전체가 안 만들어지면 안 된다.
    """
    nums = [int(x) for x in re.findall(r"\d+", str(tc or ""))[:3]]
    if not nums:
        return 0
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def has_tc(tc):
    """영상으로 건너뛸 수 있는 시간인지."""
    return bool(re.search(r"\d+\s*:\s*\d+", str(tc or "")))


def mmss(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def esc(x):
    """HTML 에 그대로 넣어도 안전하게. & 나 < 하나에 시트가 깨지지 않도록."""
    return html.escape("" if x is None else str(x), quote=True)


def safe_name(s, n=50):
    """파일 이름으로 못 쓰는 글자를 지운다."""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:n].strip() or "video"


# ── 한국어 자막 속 영어 뽑아내기 ─────────────────────────────

def english_bits(chunks, min_words=2, limit=250):
    """한국어 강의 자막에 섞인 영어 문장을 이어 붙여 뽑는다.

    자막은 두 줄씩 끊겨 들어온다("I'm gonna take a sip" / "of coffee").
    줄 단위로 찾으면 문장이 토막 나므로, 전체를 한 줄로 이어 붙인 뒤
    영어 구간을 찾고 그 시작 위치의 시간을 되찾아 준다.
    """
    buf, offs, secs, pos = [], [], [], 0
    for c in chunks:
        t = re.sub(r"\s+", " ", str(c.get("text", ""))).strip()
        if not t:
            continue
        offs.append(pos)
        secs.append(float(c.get("start", 0) or 0))
        buf.append(t)
        pos += len(t) + 1
    if not buf:
        return []
    s = " ".join(buf)

    out, seen = [], set()
    pat = r"[A-Za-z][A-Za-z'’]*(?:[ ,.!?'’-]+[A-Za-z][A-Za-z'’]*)*"
    for m in re.finditer(pat, s):
        # 영어가 길게 이어지면 문장 단위로 다시 쪼갠다.
        # 통째로 두면 5분치 영어가 한 덩이가 되어 타임코드가 쓸모없어진다.
        for sent in re.finditer(r"[^.!?]+[.!?]?", m.group(0)):
            txt = sent.group(0).strip(" ,.!?-'’")
            if len(txt.split()) < min_words:
                continue
            key = txt.lower()
            if key in seen:
                continue
            seen.add(key)
            i = max(bisect.bisect_right(offs, m.start() + sent.start()) - 1, 0)
            out.append((secs[i], txt))
            if len(out) >= limit:
                return out
    return out


# ── SRT 만들기 ────────────────────────────────────────────────

def _srt_ts(sec):
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(chunks, path):
    """[{"text","start","dur"[,"end"]}] → .srt 파일. 겹치는 구간은 잘라 정리한다."""
    blocks, n = [], 0
    for i, c in enumerate(chunks):
        text = re.sub(r"\s+", " ", str(c.get("text", ""))).strip()
        if not text:
            continue
        start = float(c.get("start", 0) or 0)
        end = c.get("end")
        if end is None:
            dur = float(c.get("dur", 0) or 0)
            end = start + dur if dur > 0 else start + 2.5
        nxt = None
        for later in chunks[i + 1:]:
            if str(later.get("text", "")).strip():
                nxt = float(later.get("start", 0) or 0)
                break
        if nxt is not None and end > nxt:
            end = nxt
        if end <= start:
            end = start + 0.4
        n += 1
        blocks.append(f"{n}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return n


# ── 음성인식(Whisper) 폴백 ───────────────────────────────────

class _Hush:
    """yt-dlp 가 콘솔에 직접 찍는 오류 줄을 삼킨다."""
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def whisper_transcribe(video_id):
    """자막이 없을 때 오디오를 받아 전사한다. (chunks, 언어코드) 를 돌려준다."""
    import os
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    import logging
    import warnings
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", module="huggingface_hub.*")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("    [i] 음성인식을 쓰려면:  pip install faster-whisper")
        return None, None

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ytsub_"))
    try:
        print("    자막이 없어 음성인식으로 넘어갑니다 — 오디오 내려받는 중...")
        err = None
        for client in (None, "android"):
            opts = {"quiet": True, "no_warnings": True, "logger": _Hush(),
                    "format": "bestaudio/best",
                    "outtmpl": str(tmp / "audio.%(ext)s")}
            if _proxy_dict():
                opts["proxy"] = _proxy_dict()
            if client:
                opts["extractor_args"] = {"youtube": {"player_client": [client]}}
            try:
                with YoutubeDL(opts) as y:
                    y.download([f"https://www.youtube.com/watch?v={video_id}"])
                err = None
                break
            except Exception as e:
                err = e
        if err is not None:
            print(f"    오디오 내려받기 실패: {err}")
            return None, None

        files = [p for p in tmp.iterdir() if p.is_file()]
        if not files:
            print("    오디오 파일을 찾지 못했습니다.")
            return None, None
        audio = str(max(files, key=lambda p: p.stat().st_size))

        print(f"    음성 인식 중 (모델 {WHISPER_MODEL}) — 영상 길이만큼 걸릴 수 있습니다...")
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        # 언어를 못 박지 않는다. 한국어 강의 영상도 들어오기 때문이다.
        segments, info = model.transcribe(audio, vad_filter=True)

        chunks = []
        for seg in segments:
            t = (seg.text or "").strip()
            if t:
                chunks.append({"text": t, "start": float(seg.start),
                               "end": float(seg.end),
                               "dur": float(seg.end - seg.start)})
        lang = getattr(info, "language", None) or "en"
        return (chunks or None), lang
    finally:
        try:
            for p in tmp.iterdir():
                p.unlink()
            tmp.rmdir()
        except Exception:
            pass


def open_file(p):
    """OS 기본 프로그램으로 파일 열기. 기다리지 않는다."""
    if HEADLESS:            # 서버에는 열어 줄 화면이 없다
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        pass


# ══════════════════════════════════════════════ 자막 받기

EN_LANGS = ["en", "en-GB", "en-US"]
KO_LANGS = ["ko", "ko-KR"]

FETCH_TRIES = 3

SRC_KO = {"manual": "수동", "asr": "자동생성(ASR)", "whisper": "음성인식(Whisper)"}
LANG_KO = {"en": "영어", "ko": "한국어"}

SRC_NOTE = {
    "manual": "제작자가 올린 수동 자막입니다. 신뢰도가 높습니다.",
    "asr": ("유튜브 자동생성(ASR) 자막입니다. 대체로 정확하지만 "
            "고유명사·숫자·드문 낱말은 틀렸을 수 있습니다."),
    "whisper": ("유튜브에 자막이 없어 음성인식(Whisper)으로 직접 받아쓴 자막입니다. "
                "오류 가능성이 자동생성 자막보다 큽니다."),
}


def _try_fetch(finder, langs):
    """('ok', chunks) / ('absent', None) / ('failed', 오류) 중 하나.

    '없는 것'과 '있는데 못 받은 것'을 반드시 구별해야 한다.
    둘을 뭉뚱그리면, 잠깐 서버가 삐끗했을 뿐인데 멀쩡한 수동 자막을
    버리고 음성인식으로 내려가 버린다.
    """
    import time
    try:
        tr = finder(langs)
    except NoTranscriptFound:
        return "absent", None
    except Exception as e:
        return "failed", e

    last = None
    for i in range(FETCH_TRIES):
        try:
            return "ok", _fetch(tr)
        except Exception as e:
            last = e
            if i < FETCH_TRIES - 1:
                time.sleep(1.5 * (i + 1))
    return "failed", last


def get_transcript(video_id, allow_whisper=None):
    """(chunks, source, lang) 반환. 못 만들면 (None, None, None).

    영어를 먼저 찾고, 없으면 한국어를 찾는다.
    한국어 영어강의 영상은 자막이 한국어뿐이지만, 그 안에 배울 영어가 들어 있다.
    """
    if allow_whisper is None:
        allow_whisper = ALLOW_WHISPER

    listing = None
    try:
        listing = _listing(video_id)
    except (TranscriptsDisabled, NoTranscriptFound):
        pass
    except Exception as e:
        print(f"    자막 조회 실패: {type(e).__name__}: {e}")

    existed = False          # 쓸 만한 자막이 목록에 '있었는가'
    if listing is not None:
        attempts = [("manual", "en", listing.find_manually_created_transcript, EN_LANGS)]
        if ALLOW_ASR:
            attempts.append(("asr", "en", listing.find_generated_transcript, EN_LANGS))
        attempts.append(("manual", "ko", listing.find_manually_created_transcript, KO_LANGS))
        if ALLOW_ASR:
            attempts.append(("asr", "ko", listing.find_generated_transcript, KO_LANGS))

        for src, lang, finder, langs in attempts:
            state, val = _try_fetch(finder, langs)
            if state == "ok":
                return val, src, lang
            if state == "failed":
                existed = True
                print(f"    [!] {LANG_KO[lang]} {SRC_KO[src]} 자막이 있는데 "
                      f"받아오지 못했습니다: {type(val).__name__}")

    if existed:
        print("\n    유튜브 자막 서버가 잠깐 응답하지 않았습니다."
              "\n    1~2분 뒤에 다시 실행해 주세요."
              "\n    (음성인식으로 대신 만들면 낱말이 틀릴 수 있어 멈췄습니다."
              "\n     그래도 지금 만들려면 ALLOW_WHISPER_ON_FETCH_FAIL 을 True 로)")
        if not ALLOW_WHISPER_ON_FETCH_FAIL:
            return None, None, None

    if allow_whisper:
        ch, lang = whisper_transcribe(video_id)
        if ch:
            return ch, "whisper", ("ko" if str(lang).startswith("ko") else "en")
    return None, None, None


def _ydl_info(v, client=None):
    o = {"quiet": True, "skip_download": True,
         "no_warnings": True, "logger": _Hush()}
    if _proxy_dict():
        o["proxy"] = _proxy_dict()
    if client:
        o["extractor_args"] = {"youtube": {"player_client": [client]}}
    with YoutubeDL(o) as y:
        return y.extract_info(f"https://www.youtube.com/watch?v={v}", download=False)


def _oembed_title(v):
    import urllib.request, urllib.parse
    u = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={v}", "format": "json"})
    with urllib.request.urlopen(u, timeout=10) as r:
        return json.load(r).get("title") or ""


def video_info(v):
    """(제목, 길이) — 못 가져와도 멈추지 않는다."""
    for client in (None, "android"):
        try:
            i = _ydl_info(v, client)
            return i.get("title") or "", i.get("duration") or 0
        except Exception:
            continue
    try:                                  # 제목만이라도
        return _oembed_title(v), 0
    except Exception:
        print("    [i] 영상 정보를 못 읽었습니다. 자막만 받아봅니다.")
        return "", 0


def save_srt(chunks, title, v):
    OUT.mkdir(exist_ok=True)
    stem = f"{safe_name(title)}_{v}" if title.strip() else v
    path = OUT / f"{stem}.srt"
    n = write_srt(chunks, path)
    print(f"  자막 파일 → {path}  ({n}줄)")
    return path


# ══════════════════════════════════════════════ 1단계 · prep

MODE_NOTE = {
    "en": """<이 영상의 성격>
영어로 진행되는 영상입니다. 자막 전체가 학습 재료입니다.
아래 "반복 등장 낱말" 목록은 자막에 두 번 이상 나온 낱말입니다.
단어는 되도록 이 안에서 고르세요. 여러 번 들은 말이 먼저 붙습니다.
</이 영상의 성격>""",

    "ko": """<이 영상의 성격>
한국어로 설명하는 영어 강의 영상입니다. 자막이 한국어입니다.
**한국어는 설명이고, 배울 것은 그 사이에 섞인 영어 문장입니다.**
아래 "영상에 나온 영어" 목록이 실제 학습 재료입니다.
한국어 자막은 그 영어를 언제·왜 쓰는지 알려주는 설명으로 읽으세요.

주의: 자동 자막이라 영어 부분이 잘못 받아적혀 있습니다.
("brush my tee" → "brush my teeth", "I'm gna" → "I'm gonna")
명백한 오류는 **바로잡아서 정확한 영어로** 시트에 쓰세요.
무슨 말인지 확신이 안 서는 토막은 아예 버리세요.
틀린 영어를 외우게 하는 것이 가장 큰 손해입니다.
</이 영상의 성격>""",
}

PROMPT_HEAD = """아래는 유튜브 영상의 자막입니다.
성인 영어 학습자가 영상을 본 뒤 복습할 시트를 만들어 주세요.

<배우는 사람>
이름: {name}
수준: {level}
목표: {goal}
</배우는 사람>

가장 중요한 전제:
이 사람은 **어른**입니다. 아이가 아닙니다.
- "잘했어요!" 같은 어린이용 말투를 쓰지 마세요. 담백하게 쓰세요.
- 하지만 영어는 **왕초보**입니다. 긴 문장, 어려운 어휘, 문법 용어는 금물입니다.
- 시험 영어가 아니라 입으로 나오는 영어가 목표입니다.
- 이 사람은 혼자 공부합니다. 읽어줄 사람도, 답을 맞춰줄 사람도 없습니다.
  그래서 모든 항목에 **정답과 한국어 뜻**이 반드시 있어야 합니다.

<영상>
제목: {title}
길이: {duration}초
</영상>

<자막 출처>
{source_note}
</자막 출처>

{mode_note}

{material}

**당신은 영상 화면을 보지 못합니다. 자막 글자만 받았습니다.**
자막에 없는 것은 지어내지 마세요.

━━ 1. 오늘의 표현 {n_expr}개 ━━
이 시트의 심장입니다. 낱말 하나가 아니라 **덩어리 말**을 뽑으세요.
- 통째로 외워서 내일 그대로 쓸 수 있는 말. 7단어 이내.
- 왕초보가 실제로 쓸 상황이 있는 말만. 영상에만 나오는 특수한 말은 버리세요.
- when_ko: **언제 쓰는 말인지** 한 줄. 뜻보다 이것이 중요합니다.
  "아침에 늦잠 잤다고 말할 때" 처럼 상황을 적으세요.
- example_en: 그 표현을 넣은 완전한 문장 하나. 원어민이 실제로 쓰는 문장으로.
  "/" 로 여러 개 늘어놓지 마세요.
- 한글 발음 표기 금지. (발음 요령은 3번 쉐도잉에서만 씁니다.)

━━ 2. 단어 {n_words}개 ━━
- 1번 표현에 이미 들어간 낱말은 빼세요. 같은 것을 두 번 싣지 않습니다.
- 고유명사·숫자·상표명 금지.
- 손에 잡히는 말, 자주 쓰는 말 우선. 추상어(system, aspect)는 뒤로.
- pos 는 "명사" "동사" "형용사" "부사" 중 하나로만.
- 예문은 6단어 이내.

━━ 3. 쉐도잉 문장 {n_shadow}개 ━━
소리 내어 따라 말할 문장입니다.
- 영상에 실제로 나온 문장. 3~8단어의 짧은 것.
- tip_ko: 왕초보가 놓치는 소리 요령 한 줄.
  연음, 약해지는 소리, 강세 위치.
  **여기서는** 한글로 소리를 적어 설명해도 됩니다("워러"처럼).
  듣기 요령이니까요. 다만 단어를 그렇게 외우게 하지는 마세요.

━━ 4. 받아쓰기 {n_dict}개 ━━
영상의 그 지점을 다시 듣고 받아쓸 구간입니다.
- 한 문장, 5~10단어.
- hint_ko: 무슨 상황인지 한 줄. **정답을 알려주면 안 됩니다.**
- answer_en: 그 구간의 정확한 영어 문장.

━━ 5. 말하기 미션 3개 ━━
난이도가 올라가는 사다리. 1단은 반드시 성공하게 하세요.
1단 "그대로 말하기": 오늘 표현 하나를 그대로. 5단어 이내.
2단 "바꿔 말하기": 1단 문장에서 낱말 하나만 바꿔서.
3단 "내 말로 말하기": 오늘 표현으로 자기 이야기 한 마디.
  **예시 문장은 반드시 문법에 맞아야 합니다.** 혼자 보고 외울 사람입니다.
  "I love play." 는 안 됩니다. "I love playing." 이 맞습니다.

한국어 요약은 3문장. 이 영상이 무슨 내용이고 무엇을 건질 수 있는지.

━━ 출력 형식 ━━
timecode 는 **반드시** 자막에 있는 "분:초" 숫자로 (예: "01:12").
'—' 나 빈 칸을 넣지 마세요. 시간을 모르겠으면 그 항목을 아예 빼세요.

**JSON만 출력하세요.** 앞뒤 설명, 인사말, 마크다운 백틱 모두 없이
아래 구조 그대로만:

{{
 "summary_ko":"...", "topic_ko":"한 단어 주제",
 "expressions":[{{"en":"...","ko":"...","when_ko":"언제 쓰는 말인지",
                 "timecode":"01:12","example_en":"...","example_ko":"..."}}],
 "words":[{{"en":"...","ko":"...","pos":"명사","timecode":"01:12",
            "example_en":"...","example_ko":"..."}}],
 "shadow":[{{"timecode":"01:12","en":"...","ko":"...","tip_ko":"소리 요령"}}],
 "dictation":[{{"timecode":"01:12","hint_ko":"무슨 상황인지","answer_en":"..."}}],
 "missions":[{{"level":1,"label":"그대로 말하기","prompt_ko":"...","example_en":"..."}}]
}}

<자막>
{transcript}
</자막>"""


def build_material(chunks, lang):
    """영상 성격에 맞는 '재료' 블록을 만든다."""
    if lang == "en":
        text = " ".join(c["text"] for c in chunks).lower()
        rep = sorted(w for w, n in Counter(
            re.findall(r"[a-z][a-z'-]{2,}", text)).items() if n >= MIN_REPEAT)
        return ("<반복 등장 낱말>\n" + ", ".join(rep[:400]) + "\n</반복 등장 낱말>")

    bits = english_bits(chunks)
    if not bits:
        return ("<영상에 나온 영어>\n"
                "(자막에서 영어 문장을 찾지 못했습니다. 한국어 자막만 보고 "
                "이 영상의 주제에 맞는 표현을 만들어 주세요. "
                "이때 timecode 는 그 내용을 설명하는 대목으로 다세요.)\n"
                "</영상에 나온 영어>")
    lines = "\n".join(f"{mmss(s)}  {t}" for s, t in bits)
    return f"<영상에 나온 영어>\n{lines}\n</영상에 나온 영어>"


def srt_only(url):
    v = vid_of(url)
    if not v:
        sys.exit("[!] URL에서 영상 ID를 못 찾았습니다.")
    print(f"\n  영상 확인 중... {v}")
    title, _ = video_info(v)
    chunks, src, lang = get_transcript(v)
    if not chunks:
        sys.exit("[!] 자막을 만들지 못했습니다. 다른 영상을 골라주세요.")
    print(f"  {title}")
    print(f"  자막 {LANG_KO[lang]} {SRC_KO[src]} · {len(chunks)}줄")
    save_srt(chunks, title, v)
    print()


def prep(url, interactive=True):
    """자막을 받아 붙여넣을 글을 만든다. 만들어진 프롬프트 문자열을 돌려준다."""
    v = vid_of(url)
    if not v:
        raise PrepFailed("URL에서 영상 ID를 못 찾았습니다.")

    print(f"\n  영상 확인 중... {v}")
    title, dur = video_info(v)

    chunks, src, lang = get_transcript(v)
    if not chunks:
        raise PrepFailed("자막을 못 받았습니다. 다른 영상을 골라주세요.")
    if MIN_TRANSCRIPT_LINES and len(chunks) < MIN_TRANSCRIPT_LINES:
        raise PrepFailed(f"자막이 {len(chunks)}줄뿐이라 건너뜁니다. "
                         f"(쇼츠이거나 너무 짧은 영상)")
    if MAX_TRANSCRIPT_LINES and len(chunks) > MAX_TRANSCRIPT_LINES:
        raise PrepFailed(f"자막이 {len(chunks)}줄이나 되어 건너뜁니다. "
                         f"(라이브·설명회처럼 강의가 아닌 영상)")
    print(f"  {title}")
    print(f"  자막 {LANG_KO[lang]} {SRC_KO[src]} · {len(chunks)}줄")
    if lang == "ko":
        n = len(english_bits(chunks))
        print(f"  한국어 강의 영상으로 봅니다 — 자막에서 영어 {n}덩이를 찾았습니다.")
    save_srt(chunks, title, v)

    lines = []
    for c in chunks:
        t = c["text"].replace("\n", " ").strip()
        if t:
            lines.append(f"{mmss(c['start'])}  {t}")

    prompt = PROMPT_HEAD.format(
        **LEARNER, title=title, duration=dur,
        source_note=SRC_NOTE[src], mode_note=MODE_NOTE[lang],
        material=build_material(chunks, lang),
        transcript="\n".join(lines))
    PASTE.write_text(prompt, encoding="utf-8")

    STATE.write_text(json.dumps(
        {"id": v, "title": title, "duration": dur, "source": src, "lang": lang},
        ensure_ascii=False), encoding="utf-8")

    # 지난 영상의 응답이 남아 있으면 지운다.
    # 그대로 두면 2단계가 '새 영상 + 지난 내용' 시트를 조용히 만들어 버린다.
    if REPLY.exists():
        REPLY.unlink()
        if interactive:
            print(f"\n  지난 '{REPLY.name}' 은 지웠습니다. 새로 저장해 주세요.")

    if interactive:
        print(f"\n  '{PASTE.name}' 만들었습니다.")
        print("  ─────────────────────────────────────────")
        print("   1. 열린 메모장에서 Ctrl+A → Ctrl+C")
        print("   2. Claude 채팅창에 붙여넣기")
        print("   3. 돌아온 JSON 을 '응답.json' 으로 저장")
        print("   4. 2단계_시트만들기.bat 실행")
        print("  ─────────────────────────────────────────\n")
        open_file(PASTE)
    return prompt


# ══════════════════════════════════════════════ 한 번에 · auto

CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

ASK_SYSTEM = ("당신은 성인 영어 학습 시트를 만드는 도구입니다. "
              "요청받은 JSON 객체 하나만 출력합니다. "
              "인사말, 설명, 마크다운 백틱을 붙이지 않습니다.")


def ask_claude_api(prompt):
    """Anthropic API 로 시트 JSON 을 받는다. GitHub Actions 는 이 길로 간다.

    스트리밍으로 받는다. 시트가 길어 한 번에 받으면 연결이 끊길 수 있다.
    """
    try:
        import anthropic
    except ImportError:
        sys.exit("[!] pip install anthropic")

    print(f"\n  Claude({CLAUDE_MODEL}) 에게 시트를 만들어 달라고 하는 중...")
    print("  (영상 길이에 따라 30초 ~ 2분쯤 걸립니다.)")
    client = anthropic.Anthropic()          # ANTHROPIC_API_KEY 를 읽는다
    try:
        with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=ASK_SYSTEM,
                messages=[{"role": "user", "content": prompt}]) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        sys.exit("[!] ANTHROPIC_API_KEY 가 틀렸습니다. Secrets 를 확인하세요.")
    except anthropic.RateLimitError:
        sys.exit("[!] 요청이 몰렸습니다. 몇 분 뒤에 다시 실행해 주세요.")
    except anthropic.APIStatusError as e:
        sys.exit(f"[!] Claude API 오류({e.status_code}): {str(e)[:300]}")
    except anthropic.APIConnectionError as e:
        sys.exit(f"[!] Claude API 에 연결하지 못했습니다: {e}")

    if msg.stop_reason == "refusal":
        sys.exit("[!] Claude 가 이 요청을 거절했습니다. 다른 영상을 골라주세요.")
    if msg.stop_reason == "max_tokens":
        print("  [i] 응답이 길이 제한에 닿았습니다. 시트가 잘렸을 수 있습니다.")

    out = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not out:
        sys.exit("[!] Claude 가 빈 응답을 돌려주었습니다. 다시 실행해 주세요.")
    u = msg.usage
    print(f"  응답 받았습니다. "
          f"(입력 {u.input_tokens:,} · 출력 {u.output_tokens:,} 토큰)")
    return out


def ask_claude(prompt):
    """시트 JSON 을 받아온다. 가진 인증 수단에 따라 길이 갈린다.

      1) ANTHROPIC_API_KEY  → API 로 직접. 쓴 만큼 따로 과금된다.
      2) 그 외              → Claude Code CLI 로.
         내 PC 에서는 로그인해 둔 구독으로,
         서버에서는 CLAUDE_CODE_OAUTH_TOKEN 으로 같은 구독을 쓴다.

    구독만 있고 API 키가 없어도 되도록 2번을 남겨 두었다.
    Pro 구독에 API 사용권은 들어 있지 않다 — 둘은 별개 요금이다.
    """
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return ask_claude_api(prompt)
    if HEADLESS and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        sys.exit("[!] 인증 정보가 없습니다. 둘 중 하나를 Secrets 에 넣어 주세요.\n"
                 "    CLAUDE_CODE_OAUTH_TOKEN — 지금 쓰는 Claude 구독으로.\n"
                 "        내 PC 에서 'claude setup-token' 을 실행하면 나옵니다.\n"
                 "    ANTHROPIC_API_KEY — 콘솔에서 발급받은 키로. (따로 과금)")
    return ask_claude_cli(prompt)


def ask_claude_cli(prompt):
    """Claude Code CLI 를 불러 시트 JSON 을 받는다.

    API 키가 아니라 지금 쓰는 구독으로 동작한다.

    두 가지를 반드시 지킨다.
      1) --tools "" 로 도구를 전부 끈다. 시트 만들기는 글짓기일 뿐이다.
      2) 빈 임시 폴더에서 돌린다. 작업 폴더를 여기로 두면 안 된다.
    출력은 바이트로 받아 UTF-8 로 푼다. 파이프를 거치면 콘솔
    코드페이지 때문에 한글이 깨진다.
    """
    import tempfile
    print("\n  Claude 에게 시트를 만들어 달라고 하는 중...")
    print("  (영상 길이에 따라 30초 ~ 2분쯤 걸립니다. 기다려 주세요.)")
    safe_dir = tempfile.mkdtemp(prefix="adult_ask_")
    try:
        p = subprocess.run(["claude", "-p", "--tools", ""],
                           input=prompt.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=safe_dir, timeout=900)
    except FileNotFoundError:
        sys.exit("[!] 'claude' 명령을 찾지 못했습니다.\n"
                 "    내 PC 라면 Claude Code 가 설치되어 있어야 합니다.\n"
                 "    (1단계/2단계 를 따로 쓰는 방법은 그대로 됩니다.)")
    except subprocess.TimeoutExpired:
        sys.exit("[!] Claude 응답이 15분을 넘겨 중단했습니다. 다시 실행해 주세요.")
    finally:
        try:
            Path(safe_dir).rmdir()
        except Exception:
            pass

    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        sys.exit(f"[!] Claude 호출이 실패했습니다.\n    {err[:300]}")

    out = p.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        sys.exit("[!] Claude 가 빈 응답을 돌려주었습니다. 다시 실행해 주세요.")
    print(f"  응답 받았습니다. ({len(out):,}자)")
    return out


def auto(url):
    """자막 받기부터 시트 만들기까지 한 번에. 만들어진 HTML 경로를 돌려준다."""
    prompt = prep(url, interactive=False)
    reply = ask_claude(prompt)
    REPLY.write_text(reply, encoding="utf-8")
    return build()


# ══════════════════════════════════════════════ 2단계 · build

def load_reply():
    if not REPLY.exists():
        sys.exit(f"[!] {REPLY.name} 이 없습니다.\n"
                 "    Claude 응답을 이 이름으로 저장해 주세요.")
    raw = REPLY.read_text(encoding="utf-8").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    # 앞뒤에 설명이 붙어 있어도 첫 { 부터 마지막 } 까지만 살린다
    a, b = raw.find("{"), raw.rfind("}")
    if a == -1 or b == -1:
        sys.exit("[!] JSON 을 못 찾았습니다. 파일 안에 { 로 시작하는 내용이 있는지 확인하세요.")
    try:
        return json.loads(raw[a:b + 1])
    except json.JSONDecodeError as e:
        sys.exit(f"[!] JSON 형식 오류: {e}\n"
                 "    응답이 중간에 잘렸을 수 있습니다. 채팅에서 '이어서'라고 해보세요.")


def review_items(n, skip_vid=None):
    """지난 영상에서 배운 표현·단어를 최근 것부터."""
    if not WORDS_DB.exists():
        return []
    try:
        led = json.loads(WORDS_DB.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out, seen = [], set()
    for d in sorted({w.get("date", "") for w in led}, reverse=True):
        for w in led:
            if w.get("date") != d or w.get("vid") == skip_vid:
                continue
            key = (w.get("en") or "").lower()
            if not key or key in seen:
                continue
            out.append(w); seen.add(key)
            if len(out) >= n:
                return out
    return out


def build():
    if not STATE.exists():
        sys.exit("[!] 먼저 1단계를 실행하세요.")
    if not TEMPLATE.exists():
        sys.exit(f"[!] {TEMPLATE.name} 이 같은 폴더에 있어야 합니다.")

    vi = json.loads(STATE.read_text(encoding="utf-8"))
    sh = load_reply()
    v, today = vi["id"], dt.date.today().isoformat()

    for key in ("summary_ko", "topic_ko", "expressions", "words", "missions"):
        if key not in sh:
            sys.exit(f"[!] 응답에 '{key}' 가 없습니다. 다시 받아주세요.")

    def tc_tag(tc, cls, extra=""):
        """시간이면 영상 링크, 아니면 그냥 글자."""
        if not has_tc(tc):
            return f'<span class="{cls}">{esc(tc) or "--:--"}</span>'
        return (f'<a class="{cls}" href="https://youtu.be/{v}?t={sec_of(tc)}" '
                f'target="_blank" rel="noopener"{extra}>{esc(tc)}</a>')

    def g(d, k):
        return esc(d.get(k, ""))

    ex = "\n".join(f"""<li class="x">
  {tc_tag(e.get('timecode'), "xtc", ' title="영상에서 듣기"')}
  <b>{g(e, 'en')}</b>
  <span class="ko">{g(e, 'ko')}</span>
  <span class="when">{g(e, 'when_ko')}</span>
  <em>{g(e, 'example_en')}<i>{g(e, 'example_ko')}</i></em>
  <div class="mark"><button>입에서 나온다</button><button>알긴 안다</button><button>처음</button></div>
</li>""" for e in sh["expressions"])

    ws = "\n".join(f"""<li class="w">
  {tc_tag(w.get('timecode'), "wtc", ' title="영상에서 듣기"')}
  <b>{g(w, 'en')}</b><span class="pos">{g(w, 'pos')}</span>
  <span class="ko">{g(w, 'ko')}</span>
  <em>{g(w, 'example_en')}<i>{g(w, 'example_ko')}</i></em>
  <div class="mark"><button>안다</button><button>가물</button><button>처음</button></div>
</li>""" for w in sh["words"])

    sd = "\n".join(f"""<li class="s">
  {tc_tag(s.get('timecode'), "stc")}
  <div class="sbody"><p class="en">{g(s, 'en')}</p><p class="ko">{g(s, 'ko')}</p>
    <p class="tip">{g(s, 'tip_ko')}</p></div>
  <div class="reps" data-n="0"><button class="rep">따라 말했다</button><span class="cnt">0</span></div>
</li>""" for s in sh.get("shadow", []))
    shadow_block = (f'<h2>쉐도잉 — 소리 내서 따라 말하기</h2>'
                    f'<ul class="shadow">{sd}</ul>') if sd else ""

    dc = "\n".join(f"""<li class="d">
  {tc_tag(d.get('timecode'), "dtc")}
  <div class="dbody"><p class="hint">{g(d, 'hint_ko')}</p>
    <textarea rows="2" placeholder="들리는 대로 적어보세요"></textarea>
    <details><summary>정답 보기</summary><p class="ans">{g(d, 'answer_en')}</p></details></div>
</li>""" for d in sh.get("dictation", []))
    dict_block = (f'<h2>받아쓰기 — 그 지점을 다시 듣고</h2>'
                  f'<ol class="dict">{dc}</ol>') if dc else ""

    rv = "".join(
        f"""<li><a href="https://youtu.be/{esc(w.get('vid'))}?t={sec_of(w.get('timecode'))}" """
        f"""target="_blank" rel="noopener"><b>{esc(w.get('en'))}</b>"""
        f"""<span>{esc(w.get('ko'))}</span></a></li>"""
        for w in review_items(N_REVIEW, skip_vid=v))
    review_block = (f'<h2>지난 시트 — 아직 입에 붙었나</h2>'
                    f'<ul class="rev">{rv}</ul>') if rv else ""

    ms = "".join(f"""<li class="m"><span class="lv">{g(m, 'level')}</span>
  <div><b>{g(m, 'label')}</b><p>{g(m, 'prompt_ko')}</p><code>{g(m, 'example_en')}</code></div>
  <button class="done" aria-pressed="false">했다</button></li>""" for m in sh["missions"])

    lang = vi.get("lang", "en")
    h = TEMPLATE.read_text(encoding="utf-8")
    for k, val in {
        "{{DATE}}": today, "{{TOPIC}}": esc(sh["topic_ko"]),
        "{{SRC_CLASS}}": vi["source"],
        "{{SRC_LABEL}}": {"manual": "수동자막", "asr": "자동자막",
                          "whisper": "음성인식"}.get(vi["source"], vi["source"]),
        "{{LANG_LABEL}}": {"en": "영어 영상", "ko": "한국어 강의"}.get(lang, lang),
        "{{TITLE}}": esc(vi["title"]), "{{VIDEO_ID}}": v,
        "{{SUMMARY}}": esc(sh["summary_ko"]),
        "{{EXPRESSIONS}}": ex, "{{EXPR_COUNT}}": str(len(sh["expressions"])),
        "{{WORDS}}": ws, "{{WORD_COUNT}}": str(len(sh["words"])),
        "{{SHADOW}}": shadow_block, "{{DICTATION}}": dict_block,
        "{{REVIEW}}": review_block, "{{MISSIONS}}": ms,
    }.items():
        h = h.replace(k, val)

    OUT.mkdir(exist_ok=True)
    path = OUT / f"{today}_{LEARNER['name']}_{v}.html"
    path.write_text(h, encoding="utf-8")

    led = json.loads(WORDS_DB.read_text(encoding="utf-8")) if WORDS_DB.exists() else []
    # 같은 영상으로 다시 만들면 덧붙이지 않고 그 영상 몫을 갈아끼운다.
    led = [w for w in led if w.get("vid") != v]
    led += [{**e, "kind": "expr", "date": today, "vid": v} for e in sh["expressions"]]
    led += [{**w, "kind": "word", "date": today, "vid": v} for w in sh["words"]]
    WORDS_DB.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n  완성 → {path}")
    print(f"  표현 {len(sh['expressions'])}개 · 단어 {len(sh['words'])}개 · "
          f"쉐도잉 {len(sh.get('shadow', []))}개 · 받아쓰기 {len(sh.get('dictation', []))}개\n")
    if not HEADLESS:
        webbrowser.open(path.resolve().as_uri())
    return path


def main():
    try:
        _run(sys.argv[1:])
    except PrepFailed as e:          # 사람이 직접 부른 자리에서는 그냥 멈춘다
        sys.exit(f"[!] {e}")


def _run(a):
    if not a:
        print(__doc__); return
    if a[0] == "prep":
        if len(a) < 2:
            sys.exit("사용법: python adult_local.py prep <유튜브주소>")
        prep(a[1])
    elif a[0] == "srt":
        if len(a) < 2:
            sys.exit("사용법: python adult_local.py srt <유튜브주소>")
        srt_only(a[1])
    elif a[0] == "auto":
        if len(a) < 2:
            sys.exit("사용법: python adult_local.py auto <유튜브주소>")
        auto(a[1])
    elif a[0] == "build":
        build()
    else:
        prep(a[0])          # 주소만 준 경우도 prep 으로


if __name__ == "__main__":
    main()
