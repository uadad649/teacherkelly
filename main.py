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

def find_url():
    """명령줄 → 환경변수 순으로 유튜브 주소를 찾는다."""
    for cand in (sys.argv[1] if len(sys.argv) > 1 else "",
                 os.environ.get("VIDEO_URL", "")):
        cand = (cand or "").strip()
        if cand and core.vid_of(cand):
            return cand
        if cand:
            sys.exit(f"[!] 유튜브 주소로 보이지 않습니다: {cand}\n"
                     "    예) https://www.youtube.com/watch?v=xxxxxxxxxxx")
    sys.exit("[!] 유튜브 주소가 없습니다.\n"
             "    Actions 탭 → Run workflow 에서 주소를 넣어 주세요.")


# ── 메일 보내기 ──────────────────────────────────────────────

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
    except smtplib.SMTPAuthenticationError:
        sys.exit("[!] 지메일 로그인에 실패했습니다.\n"
                 "    GMAIL_APP_PASSWORD 는 평소 비밀번호가 아니라\n"
                 "    구글 계정 → 보안 → 2단계 인증 → 앱 비밀번호 에서\n"
                 "    새로 만든 16자리입니다.")
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


def main():
    url = find_url()
    path = core.auto(url)          # 자막 → Claude → HTML 까지 한 번에

    vi = {}
    if core.STATE.exists():
        import json
        vi = json.loads(core.STATE.read_text(encoding="utf-8"))
    title = vi.get("title") or ""

    mailed = send_mail(path, title, url)

    rel = path.relative_to(HERE).as_posix()
    step_summary([
        f"### {dt.date.today().isoformat()} 영어 시트",
        "",
        f"- **영상**: [{title or url}]({url})",
        f"- **자막**: {core.LANG_KO.get(vi.get('lang'), '?')} "
        f"{core.SRC_KO.get(vi.get('source'), '?')}",
        f"- **시트**: `{rel}`",
        f"- **메일**: {'보냈습니다' if mailed else '보내지 않았습니다 (설정 없음)'}",
    ])


if __name__ == "__main__":
    main()
