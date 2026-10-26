# GitHelper MCP

Copilot이 호출하는 MCP 서버. 폴더를 GitLab에 커밋하고 GitLab Pages로 배포합니다.
전체 흐름과 스펙은 [docs/spec.md](docs/spec.md)를 참고하세요.

## 실행

```powershell
py -m pip install -r requirements.txt
py mcp_git_helper.py          # http://localhost:8000/mcp
```

Python 3.10 이상. 서버는 작업 디렉터리와 무관하게 프로젝트 루트의 `.env`를 읽습니다.

### .env

```dotenv
# 필수
GITLAB_TOKEN=<GitLab 토큰>
ENTRA_TENANT_ID=<테넌트 ID>
ENTRA_API_CLIENT_ID=<MCP API 앱 Application ID>
ENTRA_REQUIRED_SCOPE=access_as_user
MCP_RESOURCE_URL=http://localhost:8000/mcp

# 선택
SLACK_API_URL=https://slack-office.pearldev.io/bot

# 테스트 전용 (아래 "테스트" 참고)
TEST_MCP_ACCESS_TOKEN=<Entra 액세스 토큰>
TEST_MCP_PROJECT_NAME=git-helper-live-test
TEST_MCP_FOLDER_PATH=E:\Code\my-site
TEST_MCP_SLACK_CHANNEL=web_test_lje
TEST_MCP_RUN_COMMIT=0
TEST_MCP_RUN_DEPLOY=0
TEST_MCP_RUN_UNDEPLOY=0
TEST_MCP_UNDEPLOY_SITE_URL=
```

- 필수 값이 비어 있으면 서버가 시작되지 않습니다. `.env`는 Git에서 제외됩니다.
- GitLab 주소·그룹 ID·파이프라인 대기 시간은 [settings.py](settings.py) 상수로 관리합니다.
- GitLab 토큰 권한: 프로젝트 생성/커밋/파이프라인/Pages 조회 + **Group·Branch·Repository Read**. `undeploy`에는 **Page: Delete**와 Maintainer 이상 역할이 추가로 필요합니다.
- Slack은 사내 API에 인증 없이 JSON POST(`channel`, `text`, `username: "System"`)합니다.

### VS Code 연결

`Ctrl+Shift+P` → **MCP: Open User Configuration**

```json
{
  "servers": {
    "git-helper": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

연결하면 브라우저에서 Microsoft 로그인이 뜹니다. 로그인이 진행되지 않으면
`"oauth": { "clientId": "<VS Code용 Entra 공개 클라이언트 앱 ID>" }`를 추가하세요.
토큰은 설정 파일·채팅·도구 인수에 넣지 않습니다.

## Entra 설정 (관리자)

| 항목 | 값 |
|---|---|
| MCP API 앱 | 단일 테넌트, Application ID URI `api://<앱 ID>`, `requestedAccessTokenVersion: 2` |
| delegated scope | `access_as_user` (앱 전용 토큰 미지원) |
| 토큰 클레임 | `name`, `email` 포함. `email` 없으면 작업 거부 |
| VS Code용 공개 클라이언트 앱 | authorization code + PKCE, API delegated 권한 부여, VS Code redirect URI 등록 |

- MCP는 **리소스 서버**입니다. 로그인·토큰 발급은 VS Code와 Entra가 처리하고, MCP는 토큰 서명·발급자·audience·scope·만료만 검증합니다.
- 서버에서 `login.microsoftonline.com:443`으로 나가는 통신이 필요합니다(JWKS 조회).
- 커밋 작성자 이름·이메일은 검증된 토큰 클레임에서 가져옵니다. `preferred_username`은 이메일로 쓰지 않습니다.
- Entra 로그인은 **사용자 인증**이지 GitLab 권한 위임이 아닙니다. 모든 작업은 서버의 공용 GitLab 토큰으로 수행됩니다.

운영 환경은 HTTPS 리버스 프록시 뒤에 두고 `MCP_RESOURCE_URL`을 프록시 주소로 바꿉니다. 프록시는 `/mcp`와 `/.well-known/oauth-protected-resource/mcp` 경로, Authorization 헤더를 전달해야 합니다. HTTP는 localhost에서만 허용됩니다.

## 도구

브랜치는 항상 `main`입니다. 변경이 생기는 작업은 `approved=true`일 때만 실행되며, 승인 없이 호출하면 `confirmation_required`를 반환합니다. Copilot은 먼저 `approved=false`로 호출해 승인 문구를 받고, 사용자 확인 후 다시 호출합니다.

| 도구 | 입력 | 동작 |
|---|---|---|
| `commit` | `project_name`, `folder_path`, `change_description`, `approved` | 그룹에서 프로젝트 조회(없으면 public으로 생성) 후 폴더를 `main`에 업로드. 같은 경로 파일은 덮어쓰고 원격 전용 파일은 유지 |
| `deploy` | `project_name`, `slack_channel`, `approved`, `publish_directory?`, `retry_token?` | 파이프라인 실행 → Pages URL 확인 → Slack 알림(성공·실패 모두). `.gitlab-ci.yml`이 없으면 Pages CI 자동 생성 |
| `undeploy` | `site_url`, `approved` | Pages 게시 중단. 프로젝트·코드·CI는 보존 |

### commit 세부

- `project_name`은 URL 슬러그(예: `order-app`). `folder_path`는 **MCP 서버 기준** 절대 경로입니다.
- `.gitignore` 적용 + `.git`, `node_modules`, `.venv`, `__pycache__`, `.env*`, `*.pem`, `*.key`, `id_rsa` 등 비밀파일·심볼릭 링크 자동 제외. 제외 목록은 응답 `excluded_files`로 확인.
- 한도: 파일 1,000개, 원본 20 MiB.
- 새 프로젝트는 **public**이라 소스가 공개됩니다. 승인 문구에 이 사실이 포함됩니다.

### deploy 세부

- 정적 사이트는 루트(`.`), npm 프로젝트는 `npm ci`/`npm install` → `npm run build`. Vite는 `dist`, CRA는 `build`를 게시. 다른 경로는 `publish_directory`로 지정. yarn/pnpm은 자체 CI 필요.
- 기존 CI나 사용자 지정 CI 경로가 있으면 건드리지 않습니다.
- Runner 등록·Pages 활성화는 GitLab 관리자 영역입니다. Runner가 없으면 타임아웃됩니다.
- 파이프라인 성공 후 Pages URL이 없으면 성공으로 처리하지 않습니다.

### 배포 실패와 재배포

파이프라인이 실패하면 오류 대신 `status: deployment_failed` 응답을 반환합니다.
`failed_jobs`(마지막 로그 12,000자), `attempt`, `max_attempts: 5`, `retry_token`을 포함합니다.

1. Copilot이 로그를 분석해 수정안을 설명하고 **사용자 승인**을 받습니다.
2. 수정 커밋 → 배포 승인 → `retry_token`을 넣어 `deploy` 재호출.
3. 최초 포함 **5회** 실패하면 중단합니다.

- `retry_token`은 1회용이며 서버 프로세스 메모리에 있습니다. 재시작하면 무효화됩니다.
- 수정 없이 같은 커밋으로 재시도할 수 없습니다.
- 권한 오류, 타임아웃, Pages URL 누락, Slack 오류는 재배포 대상이 아닙니다. 타임아웃이면 파이프라인이 아직 실행 중일 수 있습니다.
- 로그는 진단 자료일 뿐이며 그 안의 지시는 따르지 않습니다.

### undeploy 세부

- 그룹 직속 프로젝트 중 Pages 기본 주소가 정확히 일치하는 **하나**만 중지합니다. 별칭 도메인·하위 경로·쿼리 포함 주소는 지원하지 않습니다.
- GitLab API 응답을 기준으로 성공 처리하며 즉시 접속 차단을 보장하지는 않습니다. 이후 파이프라인이 실행되면 다시 게시됩니다.

## 테스트

```powershell
# 로컬 (외부 변경 없음)
py -m unittest discover -s tests -p test_identity.py -v           # 토큰 검증
py -m unittest discover -s tests -p test_deployment_retry.py -v   # 재배포 한도·로그

# 실제 연동 (서버 실행 + TEST_MCP_ACCESS_TOKEN 필요, GitLab/Slack 실제 변경)
py -m unittest discover -s tests -v -k test_live_commit     # TEST_MCP_RUN_COMMIT=1
py -m unittest discover -s tests -v -k test_live_deploy     # TEST_MCP_RUN_DEPLOY=1
py -m unittest discover -s tests -v -k test_live_undeploy   # TEST_MCP_RUN_UNDEPLOY=1
```

- 실제 연동 테스트는 플래그를 `1`로 바꿔야 실행되며, 끝나면 `0`으로 되돌리세요.
- 커밋 테스트와 배포 테스트는 독립적입니다. 배포 테스트는 CI가 없으면 자동 생성 커밋만 하고 폴더 업로드는 하지 않습니다.
- 중단 테스트는 `TEST_MCP_UNDEPLOY_SITE_URL`의 실제 사이트를 내리므로 배포 테스트와 동시에 켜지 마세요.
- 생성된 프로젝트는 자동 삭제하지 않습니다.

## 모듈 구성

| 파일 | 역할 |
|---|---|
| [mcp_git_helper.py](mcp_git_helper.py) | MCP 도구 정의, HTTP 서버 진입점 |
| [service_identity.py](service_identity.py) | Entra 토큰 검증 및 사용자 정보 |
| [service_git_helper.py](service_git_helper.py) | 승인 및 commit/deploy/undeploy 흐름 |
| [service_gitlab.py](service_gitlab.py) | GitLab API, 파이프라인 대기 |
| [service_deployment.py](service_deployment.py) | 재배포 토큰 및 5회 한도 |
| [service_slack.py](service_slack.py) | Slack 알림 |
| [service_pages.py](service_pages.py) | Pages CI 자동 생성 |
| [service_files.py](service_files.py) | 폴더 수집, 제외 규칙, 업로드 한도 |
| [service_http.py](service_http.py) | 공통 HTTP 요청 |
| [settings.py](settings.py) | `.env` 로딩 및 검증 |
