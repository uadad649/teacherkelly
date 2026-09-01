# 성인 영어 복습 시트

유튜브 주소 하나를 넣으면 자막을 받아 오고, Claude 가 그것으로
복습 시트(HTML)를 만들어 메일로 보내 준다.

- **내 PC** 에서는 `.bat` 파일을 눌러서 쓴다. (지금까지 쓰던 방식 그대로)
- **GitHub** 에서는 Actions 탭의 `Run workflow` 버튼으로 쓴다.

---

## 1. 저장소에 올릴 파일

이 폴더를 통째로 올리면 된다. `.github/workflows/` 경로가 정확해야
GitHub 이 워크플로를 알아본다.

```
main.py                        ← GitHub 이 실행하는 입구
adult_local.py                 ← 실제 내용물 (내 PC 와 공용)
sheet_template.html            ← 시트 모양
requirements.txt               ← 필요한 라이브러리
words.json                     ← 지금까지 배운 표현·단어 (복습에 쓴다)
.github/workflows/daily.yml    ← 이 경로 그대로여야 한다
결과/                          ← 만들어진 시트가 쌓이는 곳
```

`붙여넣기.txt`, `응답.json`, `진행중.json` 은 한 번 돌 때만 쓰고 버리는
중간 파일이라 `.gitignore` 로 빼 두었다. 올리지 않아도 된다.

---

## 2. Secrets 등록

저장소 **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 필수 | 무엇 |
|---|---|---|
| `ANTHROPIC_API_KEY` | **필수** | console.anthropic.com 에서 발급. `sk-ant-...` |
| `GMAIL_USER` | 메일 받으려면 | 보내는 지메일 주소 |
| `GMAIL_APP_PASSWORD` | 메일 받으려면 | 아래 설명 참고 |
| `MAIL_TO` | 선택 | 받을 주소. 없으면 `GMAIL_USER` 로 온다 |
| `WEBSHARE_PROXY_USERNAME` | 선택 | 자막이 계속 막힐 때만 (5번 참고) |
| `WEBSHARE_PROXY_PASSWORD` | 선택 | 〃 |

### 지메일 앱 비밀번호

평소 로그인 비밀번호가 **아니다**. 구글이 SMTP 용으로 따로 만들어 주는
16자리다.

1. 구글 계정 → 보안 → **2단계 인증**을 먼저 켠다 (안 켜면 메뉴가 안 보인다)
2. 같은 화면에서 **앱 비밀번호** → 이름 아무거나 → 만들기
3. 나온 16자리를 `GMAIL_APP_PASSWORD` 에 넣는다

화면에 `abcd efgh ijkl mnop` 처럼 띄어서 나오는데, 띄어쓰기째로 붙여넣어도
코드가 알아서 지운다.

> Claude 구독료와 API 키는 별개다. API 키는 쓴 만큼 따로 과금된다.
> 시트 한 장에 대략 몇 백 원 수준이고, 자막이 길수록 올라간다.
> 더 싸게 쓰려면 워크플로의 `시트 만들고 메일 보내기` 단계에
> `ANTHROPIC_MODEL: claude-sonnet-5` 를 넣으면 된다.

---

## 3. 실행

1. 저장소 **Actions** 탭
2. 왼쪽에서 **영어 시트 만들기** 고르기
3. 오른쪽 **Run workflow** → 유튜브 주소 붙여넣기 → 초록 버튼

2~4분쯤 걸린다. 끝나면

- 등록한 메일로 시트가 온다 (본문에서 읽고, 첨부 파일을 열면 버튼까지 동작)
- `결과/` 폴더에 HTML 과 자막(.srt) 이 커밋된다
- 실행 화면 맨 위에 무슨 영상을 어떤 자막으로 만들었는지 요약이 뜬다

`words.json` 도 함께 커밋된다. 다음 시트의 **"지난 시트 — 아직 입에 붙었나"**
칸이 여기에 기대고 있으니 지우지 말 것.

---

## 4. 매일 자동으로 돌리고 싶어지면

`.github/workflows/daily.yml` 안의 `schedule:` 세 줄 주석을 풀면 된다.
다만 그때는 **어떤 영상을 볼지** 정해 줘야 한다. 지금은 사람이 주소를
넣어야만 돌게 되어 있다.

가장 간단한 방법은 저장소에 `videos.txt` 를 만들어 주소를 한 줄씩 적어 두고,
`main.py` 의 `find_url()` 이 아직 안 쓴 줄을 하나 집어 오게 고치는 것이다.

---

## 5. 잘 안 될 때

### `자막을 못 받았습니다` 가 계속 뜬다

가장 흔한 원인은 **유튜브가 GitHub 서버 IP 를 막는 것**이다. 내 PC 에서는
잘 되던 영상이 GitHub 에서만 안 되면 거의 이 경우다. 유튜브는 데이터센터에서
오는 요청을 자동 자막 스크래핑으로 보고 자주 차단한다.

해결책은 프록시를 하나 끼우는 것이다. `youtube-transcript-api` 가 Webshare 를
공식 지원한다.

1. webshare.io 에서 **Residential** 프록시를 산다 (가장 싼 요금제면 충분)
2. 대시보드의 Proxy 사용자명·비밀번호를 `WEBSHARE_PROXY_USERNAME` /
   `WEBSHARE_PROXY_PASSWORD` Secret 으로 등록

Webshare 가 아닌 프록시를 쓴다면 `YT_PROXY_URL` 에
`http://아이디:비번@주소:포트` 형식으로 넣으면 된다.

돈을 들이고 싶지 않다면, 자막이 안 될 때만 내 PC 에서 `한번에.bat` 으로
돌리면 된다. 같은 코드가 그대로 동작한다.

### `ANTHROPIC_API_KEY 가 틀렸습니다`

Secret 이름의 철자와, 값 앞뒤에 공백이나 줄바꿈이 딸려 들어가지 않았는지 확인.

### 메일이 안 온다

2단계 인증을 켜지 않은 채 만든 비밀번호이거나, 평소 로그인 비밀번호를 넣은
경우다. 앱 비밀번호를 새로 만들어 다시 등록한다.

### 자막이 아예 없는 영상

음성인식(Whisper)으로 대신 만들 수 있지만 GitHub 에서는 기본으로 꺼 두었다.
모델을 내려받고 영상 길이만큼 CPU 를 써서 실행이 크게 느려지기 때문이다.
켜려면 `requirements.txt` 의 `faster-whisper` 주석을 풀고, 워크플로에
`ALLOW_WHISPER: "1"` 을 넣는다.

---

## 6. 내 PC 에서 쓰는 법 (그대로다)

```
한번에.bat              자막부터 시트까지 한 번에   (Claude Code 필요)
1단계_자막받기.bat      자막 → 붙여넣기.txt
2단계_시트만들기.bat    응답.json → 시트 HTML
자막만_받기.bat         .srt 파일만
```

`한번에.bat` 은 `ANTHROPIC_API_KEY` 환경변수가 있으면 API 로, 없으면
설치된 Claude Code 로 알아서 돌아간다.

> `.bat` 파일을 편집할 일이 생기면 줄바꿈이 **CRLF** 인지 확인할 것.
> LF 로 저장하면 cmd 가 명령어를 중간부터 잘라 읽어서 이상한 오류가 난다.
> VS Code 는 오른쪽 아래에 표시된다.
