#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — GitHub Actions 에서 도는 입구
=======================================

내 PC 에서는 .bat 파일이 adult_local.py 를 직접 부른다.
GitHub 서버에는 눌러 줄 사람도, 열어 볼 브라우저도 없다.
그래서 이 파일이 대신 처음부터 끝까지 자동으로 돈다.

  유튜브 주소 받기 → 자막 받기 → Claude 에게 시트 만들기 → 메일로 보내기

주소를 주는 방법 (위에서부터 먼저 본다)
  1) 명령줄 인자      python main.py <유튜브주소>
  2) 환경변수         VIDEO_URL=<유튜브주소> python main.py
  3) 아무것도 없으면  channels.txt 의 채널에서 아직 안 본 최신 영상을 고른다
                      (매일 새벽 스케줄 실행이 이 길로 온다)

필요한 Secrets
  CLAUDE_CODE_OAUTH_TOKEN — 시트를 만들 때 (필수)
                            내 PC 에서 'claude setup-token' 으로 만든다.
                            지금 쓰는 Claude 구독을 그대로 쓴다.
  ANTHROPIC_API_KEY     — API 키로 쓰고 싶을 때만 (선택, 따로 과금)
  GMAIL_USER            — 보내는 지메일 주소
  GMAIL_APP_PASSWORD    — 지메일 '앱 비밀번호' 16자리
  MAIL_TO               — 받을 주소. 없으면 GMAIL_USER 로 보낸다.

메일 설정이 없으면 시트만 만들고 조용히 넘어간다.
저장소에 커밋되니 결과가 사라지지는 않는다.
"""

import os
import re
import smtplib
import ssl
import sys
import datetime as dt
from email.message import EmailMessage
from pathlib import Path

import adult_local as core

HERE = Path(__file__).resolve().parent


# ── 주소 찾기 ────────────────────────────────────────────────

def explicit_url():
    """사람이 직접 준 주소. 없으면 None (그때는 채널에서 고른다)."""
    for cand in (sys.argv[1] if len(sys.argv) > 1 else "",
                 os.environ.get("VIDEO_URL", "")):
        cand = (cand or "").strip()
        if not cand:
            continue
        if core.vid_of(cand):
            return cand
        sys.exit(f"[!] 유튜브 주소로 보이지 않습니다: {cand}\n"
                 "    예) https://www.youtube.com/watch?v=xxxxxxxxxxx")
    return None


# ── 메일 보내기 ──────────────────────────────────────────────

def _hint(addr):
    """주소를 알아볼 만큼만 보여 준다. 'ua1***@gmail.com' 처럼.

    통째로 찍으면 GitHub 이 Secret 과 같은 문자열이라며 *** 로 가려 버려
    무엇이 잘못됐는지 알 수 없게 된다. 그래서 일부러 변형해서 찍는다.
    """
    addr = (addr or "").strip()
    if "@" not in addr:
        return f"{addr[:3]}... (@ 가 없습니다 — 주소 전체를 넣어야 합니다)"
    name, dom = addr.split("@", 1)
    return f"{name[:3]}***@{dom}"


def _plain_summary(title, url, path):
    return (f"오늘의 영어 복습 시트입니다.\n\n"
            f"영상: {title}\n"
            f"{url}\n\n"
            f"아래에 시트가 이어집니다. 버튼을 눌러 표시하거나 받아쓰기를 하려면\n"
            f"첨부된 '{path.name}' 파일을 내려받아 열어 주세요.\n")


def send_mail(path, title, url):
    """완성된 시트를 메일로 보낸다. 설정이 없으면 조용히 건너뛴다."""
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = os.environ.get("MAIL_TO", "").strip() or user

    if not (user and pw and to):
        print("  [i] 메일 설정이 없어 보내지 않았습니다. "
              "(GMAIL_USER / GMAIL_APP_PASSWORD)")
        return False

    # 앱 비밀번호는 화면에 '어비 시디 이에프 지에이치' 처럼 띄어서 나온다.
    # 그대로 붙여넣는 일이 잦으므로 공백을 지워 준다.
    pw = re.sub(r"\s+", "", pw)

    html = path.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()

    msg = EmailMessage()
    msg["Subject"] = f"[영어 시트] {today} · {title or path.stem}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(_plain_summary(title, url, path))
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(html.encode("utf-8"), maintype="text", subtype="html",
                       filename=path.name)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                              context=ssl.create_default_context(),
                              timeout=60) as s:
            s.login(user, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        # '실패했다' 만으로는 무엇을 고쳐야 할지 알 수 없다.
        # 값 자체는 찍지 않고, 판단에 필요한 만큼만 보여 준다.
        # (GitHub 은 Secret 과 똑같은 문자열을 *** 로 가리므로 변형해서 찍는다)
        say = e.smtp_error.decode("utf-8", "replace") if isinstance(
            e.smtp_error, bytes) else str(e.smtp_error)
        sys.exit(
            "[!] 지메일 로그인에 실패했습니다.\n"
            f"    보내는 계정 : {_hint(user)}\n"
            f"    앱 비밀번호 : {len(pw)}자 "
            f"{'(정상)' if len(pw) == 16 else '(16자가 아닙니다 !!)'}\n"
            f"    구글 응답   : {say[:200]}\n"
            "\n"
            "    가장 흔한 원인 두 가지:\n"
            "    1) GMAIL_USER 의 계정과 앱 비밀번호를 만든 계정이 다르다.\n"
            "       둘은 반드시 같은 구글 계정이어야 합니다.\n"
            "    2) GMAIL_USER 에 @gmail.com 까지 넣지 않았다.")
    except (smtplib.SMTPException, OSError) as e:
        sys.exit(f"[!] 메일을 보내지 못했습니다: {type(e).__name__}: {e}")

    print(f"  메일 보냈습니다 → {to}")
    return True


# ── 실행 결과 요약 ───────────────────────────────────────────

def step_summary(lines):
    """Actions 실행 화면 맨 위에 보이는 요약. 로그를 뒤지지 않아도 되게."""
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if not dest:
        return
    try:
        with open(dest, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def deliver(path, url):
    """만들어진 시트를 메일로 보내고 실행 요약을 남긴다."""
    import json
    vi = {}
    if core.STATE.exists():
        vi = json.loads(core.STATE.read_text(encoding="utf-8"))
    title = vi.get("title") or ""

    mailed = send_mail(path, title, url)
    step_summary([
        f"### {dt.date.today().isoformat()} 영어 시트",
        "",
        f"- **영상**: [{title or url}]({url})",
        f"- **자막**: {core.LANG_KO.get(vi.get('lang'), '?')} "
        f"{core.SRC_KO.get(vi.get('source'), '?')}",
        f"- **시트**: `{path.relative_to(HERE).as_posix()}`",
        f"- **메일**: {'보냈습니다' if mailed else '보내지 않았습니다 (설정 없음)'}",
    ])


def run_auto_pick():
    """주소를 준 사람이 없을 때 — channels.txt 의 채널에서 골라 만든다."""
    import channels

    picks = channels.candidates()
    if not picks:
        if not channels.LIST.exists():
            msg = "channels.txt 가 없습니다. 볼 채널을 한 줄씩 적어 주세요."
        elif not channels.read_channels():
            msg = ("channels.txt 에 채널이 하나도 없습니다. "
                   "'#' 없는 줄에 채널을 적어 주세요.")
        else:
            msg = "채널의 최신 영상을 이미 다 봤습니다."
        print(f"  {msg} 오늘은 건너뜁니다.")
        step_summary([f"### {dt.date.today().isoformat()}", "", f"- {msg}"])
        return 0                      # 할 일이 없는 것은 실패가 아니다

    print(f"  아직 안 본 영상 {len(picks)}개를 찾았습니다. 최신 것부터 해봅니다.")
    skipped = []
    for v in picks:
        url = f"https://www.youtube.com/watch?v={v['id']}"
        print(f"\n  ── {v['title'][:60]}  ({v['channel']})")
        try:
            path = core.auto(url)
        except core.PrepFailed as e:
            print(f"     건너뜁니다 — {e}")
            channels.mark_seen(v["id"], v["title"], str(e))
            skipped.append(f"{v['title'][:40]} — {e}")
            continue
        channels.mark_seen(v["id"], v["title"], "완료")
        deliver(path, url)
        return 0

    print("  후보를 모두 시도했지만 시트를 만들지 못했습니다.")
    step_summary([f"### {dt.date.today().isoformat()} — 시트를 못 만들었습니다", ""]
                 + [f"- {s}" for s in skipped])
    return 1                          # 뭔가 잘못된 것이므로 눈에 띄게 실패시킨다


def main():
    url = explicit_url()
    if url is None:                   # 스케줄 실행
        sys.exit(run_auto_pick())
    deliver(core.auto(url), url)      # 사람이 주소를 준 실행


def _report_failure(msg):
    """실패 사유를 실행 화면 맨 위에 크게 남긴다.

    Actions 로그는 접어 놓은 단계를 펼쳐야 보이고 저장소 주인만 볼 수 있다.
    무엇 때문에 죽었는지는 한눈에 보여야 한다.
    """
    step_summary([
        f"### 실패 — {dt.date.today().isoformat()}",
        "",
        "```",
        str(msg).strip(),
        "```",
    ])


if __name__ == "__main__":
    try:
        main()
    except core.PrepFailed as e:
        _report_failure(e)
        sys.exit(f"[!] {e}")
    except SystemExit as e:
        if isinstance(e.code, str):        # sys.exit("사유") 로 죽은 경우
            _report_failure(e.code)
        raise
    except Exception as e:                 # 예상 못 한 오류도 사유를 남긴다
        import traceback
        _report_failure(traceback.format_exc()[-1500:])
        raise
