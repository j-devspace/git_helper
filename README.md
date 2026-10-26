# GitHelper MCP: 구현 및 사용 방법

- 전체 흐름(시퀀스 다이어그램·참여자): [spec.md](spec.md)

## 회사 계정 인증 HTTP 서버 / Entra-authenticated HTTP server

[mcp_git_helper.py](../mcp_git_helper.py)가 유일한 진입점이며 Entra ID 인증이
**필수**인 HTTP 서버입니다. 토큰 검증·사용자 정보 추출은
[service_identity.py](../service_identity.py)가 담당합니다.
이전 인증 없는 stdio/HTTP 실행 방식은 더 이상 지원하지 않습니다.

관리자가 준비할 항목:

- 단일 테넌트 MCP API 앱, 기본 Application ID URI `api://<API 앱의 Application ID>`.
- API 앱의 `api.requestedAccessTokenVersion`을 `2`로 설정합니다.
- delegated scope(예: `access_as_user`)를 노출합니다. 앱 전용 client credentials 토큰은 지원하지 않습니다.
- API 액세스 토큰에 `name`과 `email`이 포함되도록 클레임을 구성합니다.
  `email`은 항상 있는 값이 아닙니다. 없으면 작업을 거부하며 `preferred_username`/UPN을
  이메일로 대신 사용하거나 Microsoft Graph를 자동 호출하지 않습니다.
- VS Code가 로그인에 사용할 **Entra 공개 클라이언트 앱**을 준비합니다.
  MCP API 앱과 클라이언트 앱은 역할이 다르며, `oauth.clientId`에는 클라이언트 앱 ID를 넣습니다.
  클라이언트 앱에 API delegated 권한을 부여하고 필요한 사용자/관리자 동의를 구성합니다.
  API의 Authorized client applications 사전 승인이 필요하면 실제 클라이언트 앱 ID와 scope를 지정합니다.
- 클라이언트 앱은 authorization code + PKCE를 지원하도록 구성하고, VS Code가 사용하는
  반환 주소를 해당 앱의 모바일/데스크톱 플랫폼에 등록합니다.
  VS Code의 실제 안내·인증 로그에 표시되는 주소를 기준으로 등록하며 임의의 앱 ID나 주소를 사용하지 마세요.
  사전 승인만으로 클라이언트 앱의 redirect URI 설정이 대체되지는 않습니다.
- MCP는 **리소스 서버**입니다. 로그인은 VS Code와 Entra가 처리하며,
  MCP는 안내문(PRM)을 제공하고 요청에 포함된 Entra 토큰만 검증합니다.
  MCP의 `/authorize`, `/register`, `/token`, `/auth/consent`, `/auth/callback`은 제거했습니다.
  MCP용 Web 콜백이나 `ENTRA_CLIENT_SECRET`은 필요하지 않습니다.
  이전 중계 방식의 비밀값이 `.env`에 남아 있어도 읽지 않으며, 더 이상 사용하지 않으면
  해당 키를 삭제하고 Entra에서도 불필요한 비밀값을 폐기하세요.

프로젝트 루트 `.env`에 아래 설정값을 추가합니다. 빈 값이나 미설정 시 서버 시작을 거부합니다.
기존 GitLab 토큰 설정은 유지합니다.

```dotenv
ENTRA_TENANT_ID=<디렉터리 Tenant ID UUID>
ENTRA_API_CLIENT_ID=<MCP API 앱 Application ID UUID>
ENTRA_REQUIRED_SCOPE=access_as_user
MCP_RESOURCE_URL=http://localhost:8000/mcp
```

`ENTRA_REQUIRED_SCOPE`는 짧은 이름이며 실제 요청 scope는
`api://<ENTRA_API_CLIENT_ID>/access_as_user`입니다.
다른 Application ID URI 형식은 현재 지원하지 않습니다.
설명 예시의 `mcp.access`를 사용하려면 Entra에 그 scope를 노출하고
`ENTRA_REQUIRED_SCOPE=mcp.access`로 맞추세요. MCP 접속 URL과 Entra Application ID URI는
별개입니다. 이 구현의 scope 접두사는 `http://localhost:8000/mcp`가 아닌 `api://<앱 ID>`입니다.
서버는 Entra v2 액세스 토큰의 RS256 서명·발급자·API audience·테넌트·만료·
활성 시작 시간·delegated scope·사용자 식별자를 검증합니다.
Entra 액세스 토큰은 VS Code가 받아 MCP에 전달합니다.
MCP는 이를 검증만 하며 토큰 교환·발급·갱신을 수행하지 않습니다.
ID 토큰이나 Microsoft Graph용 액세스 토큰으로 대체하지 않습니다.
사용자 식별/재배포 표 소유권은 변경 가능한 이름·이메일이 아닌 `tid:oid`를 사용합니다.
서명 키는 지정된 테넌트의 Microsoft HTTPS JWKS에서 가져와 캐시하며 키 변경 시 다시 조회합니다.
따라서 **서버에서 `login.microsoftonline.com:443`으로 나가는 통신**이 추가로 필요합니다.
VS Code PC에서도 Microsoft 로그인/동의 엔드포인트에 접근할 수 있어야 합니다.

설치 및 로컬 실행:

```powershell
py -m pip install -r requirements.txt
py mcp_git_helper.py
```

VS Code MCP 설정 예시 (토큰 자체를 설정 파일에 저장하지 않음):

```json
{
  "servers": {
    "test-git-helpers": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "oauth": {
        "clientId": "<Entra에 등록한 VS Code용 공개 클라이언트 앱 ID>"
      }
    }
  }
}
```

직접 인증 방식에서는 Entra가 이 MCP를 위한 DCR을 제공하지 않으므로 로그인 클라이언트 ID를
VS Code에 지정해야 합니다. 서버의 API 앱 ID를 안내문에 넣는 것만으로 이를 대신할 수 없습니다.
VS Code의 `oauth.clientId` 지원이 있는 버전을 사용하세요. 클라이언트 ID는 공개 식별자이며
클라이언트 비밀값이나 토큰을 설정 파일에 넣지 않습니다.
이전 중계 방식을 사용했다면 서버를 재시작하고 VS Code의 해당 서버 인증 정보를
지운 뒤 재연결하세요. 이전 MCP 자체 발급 토큰은 더 이상 사용할 수 없습니다.

인증 흐름은 다음과 같습니다.

1. VS Code가 토큰 없이 `/mcp`를 호출하면 401과
   `WWW-Authenticate: Bearer resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"`
   안내를 받습니다.
2. VS Code가 PRM을 조회합니다. `resource`는 MCP URL,
   `authorization_servers`는 `https://login.microsoftonline.com/<tenant-id>/v2.0`,
   `scopes_supported`는 `api://<API 앱 ID>/<scope>`입니다.
3. VS Code가 Entra 메타데이터를 조회하고 브라우저에서 Microsoft 로그인을 시작합니다.
   로그인·동의·PKCE 코드 교환은 VS Code와 Entra 사이에서 이루어집니다.
4. VS Code가 Entra 액세스 토큰을 `Authorization: Bearer <access token>`으로 MCP에 보냅니다.
5. `EntraTokenVerifier`가 서명·발급자·audience·만료·테넌트·scope를 확인한 후 도구를 실행합니다.

MCP에는 별도 로그인 승인 화면, 로그인 쿠키, 인증 코드 저장소나 자체 토큰 저장소가 없습니다.
사용자 비밀번호는 MCP로 오지 않습니다. 토큰 갱신·재로그인은 VS Code와 Entra가 담당하며,
서버 재시작만으로 유효한 Entra 토큰이 무효화되지는 않습니다.
토큰을 채팅, `commit`/`deploy` 도구 인수, 로그에 넣지 마세요.
이름·이메일 입력 항목은 인증 서버의 도구 스키마에서 제거됐으며 검증된 클레임을
GitLab 작성자 정보로 전달합니다. 계정 이름/이메일 누락 시 임의 값으로 진행하지 않습니다.
승인 절차, main 고정, 사이트 주소 중단, 5회 한도는 유지합니다.

인증 서버는 localhost:8000에 바인딩합니다. Linux 운영 환경은
`python mcp_git_helper.py`로 실행하고 사내 HTTPS 리버스 프록시를 앞에 둡니다.
`MCP_RESOURCE_URL`은 프록시의 실제 HTTPS `/mcp` 주소로 변경해야 하며,
프록시는 Authorization 헤더와 `/.well-known/oauth-protected-resource/mcp`,
`/mcp` 경로를 전달해야 합니다.
프록시에서도 Authorization 헤더나 토큰을 로그로 기록하지 마세요.
기본 실행은 Uvicorn 접근 로그를 끕니다.
HTTP는 로컬 루프백 테스트 주소에만 허용됩니다. TLS를 끄거나 인증 없는 서버를
대신 실행하는 식으로 설정 오류를 우회하지 마세요.

Entra 로그인은 GitLab 사용자별 권한 위임이 아닙니다. 현재 API scope를 받은
테넌트 사용자는 서버의 공용 GitLab 토큰 권한으로 설정된 그룹에서 작업할 수 있습니다.
앱 할당·동의 범위를 제한하고, 사용자별 프로젝트 권한이 필요하면 별도 인가 정책이 필요합니다.
로컬 폴더 입력도 여전히 서버 기준이며 PC 파일 원격 업로드는 아직 구현되지 않았습니다.

`preferred_username`은 로그인 식별자이며 실제 이메일이라는 보장이 없습니다.
기존 정책대로 작성자 이메일에는 검증된 `email` 클레임만 사용하고 누락 시 작업을 거부합니다.
별도의 `whoami` 도구는 추가하지 않았으며 기존 commit/deploy/undeploy 도구를 유지합니다.

EN: This is a resource server only. VS Code signs in directly with Entra using a
registered public client and PKCE, then sends the Entra access token to MCP.
The server advertises Entra in protected resource metadata and validates tokens.
No login broker, client secret, callback or locally issued token is required.
Author email comes only from the validated email claim, not preferred_username.

```powershell
py -m unittest discover -s tests -p test_identity.py -v
```

테스트는 임시 RSA 키/JWKS와 HTTP 인증 미들웨어로 서명 검증, 잘못된 토큰 거부,
인증 필수 응답, 자동 작성자 전달, 이름·이메일 누락 시 변경 차단을 검사합니다.
실제 Entra 로그인·GitLab 쓰기는 수행하지 않습니다.

저장소 루트의 `mcp_git_helper.py`는 Copilot이 `commit`, `deploy`, `undeploy` 도구를 각각 호출하는 인증 필수 HTTP MCP 서버입니다. `service_git_helper.py`는 각 서비스를 조합하여 승인과 작업 흐름을 처리합니다. `commit`은 프로젝트명과 로컬 폴더 경로를 입력받아 GitLab 프로젝트 조회/생성과 폴더 업로드를 수행합니다. `deploy`는 프로젝트명으로 조회해 파이프라인 실행, Pages URL 조회 및 사내 Slack API 채널 알림(성공·실패 모두)을 수행합니다.

## 모듈 구성 / Module layout

| 파일 / File | 역할 / Responsibility |
|---|---|
| [mcp_git_helper.py](../mcp_git_helper.py) | MCP 도구 정의 / MCP tool definitions |
| [service_identity.py](../service_identity.py) | Entra 토큰 검증 및 사용자 정보 / Token verification and identity |
| [service_git_helper.py](../service_git_helper.py) | 승인 및 commit/deploy/undeploy 흐름 / Approval and workflow orchestration |
| [service_gitlab.py](../service_gitlab.py) | GitLab API, 프로젝트·저장소 조회, 파이프라인 대기 / GitLab API and pipeline polling |
| [service_deployment.py](../service_deployment.py) | 단일 사용 재배포 표 및 5회 한도 / Single-use retry continuations and five-attempt limit |
| [service_slack.py](../service_slack.py) | 사내 Slack API 전송 및 응답 검증 / Office Slack notifications and response validation |
| [service_pages.py](../service_pages.py) | Pages CI 생성 및 기존 CI 보존 / Pages CI generation and preservation |
| [service_files.py](../service_files.py) | 폴더 수집, 제외 규칙, 업로드 한도 / Folder collection, exclusions and upload limits |
| [service_http.py](../service_http.py) | 공통 HTTP 요청 및 IntegrationError / Shared HTTP transport and integration errors |
| [settings.py](../settings.py) | 루트 .env 로딩 및 검증 / Root .env loading and validation |

EN: This separation preserves MCP inputs, outputs, approval rules and live test commands. It does not change deployment or cache behavior.
KO: 파일 분리는 기존 MCP 입력·출력, 승인 규칙 및 실제 연동 테스트 명령을 유지하며 배포나 캐시 동작은 변경하지 않습니다.

## 실행 준비

1. Python 3.10 이상을 준비합니다.
2. `py -m pip install -r requirements.txt`로 의존성을 설치합니다.
3. `.env`에 GitLab 토큰(`GITLAB_TOKEN`)을 설정합니다. GitLab 주소, namespace ID, 파이프라인 대기 시간과 조회 간격은 [settings.py](../settings.py)의 `GITLAB_URL`, `GITLAB_NAMESPACE_ID`, `PIPELINE_TIMEOUT_SECONDS`, `PIPELINE_POLL_INTERVAL_SECONDS` 상수로 관리하며 `.env`나 프로세스 환경변수에서 읽지 않습니다. 서버는 작업 디렉터리에 관계없이 프로젝트 루트의 `.env`를 읽습니다. `.env`는 Git에서 제외되며 토큰을 저장소에 커밋하지 마세요.
4. 위 Entra API·공개 클라이언트 앱을 설정하고 `py mcp_git_helper.py`로 실행합니다. VS Code에는 MCP URL과 `oauth.clientId`를 등록합니다.
5. 실제 연동 테스트를 활성화하면 GitLab/Slack을 변경하므로 테스트 실행 전 플래그를 확인하세요. 일반 MCP 테스트는 프로세스 내 HTTP 앱을 사용하고 실제 연동 테스트는 실행 중인 인증 HTTP 서버에 연결합니다.

GitLab 토큰에는 프로젝트 생성/커밋/파이프라인/Pages 조회 권한이 필요합니다. Slack 알림은 인증 토큰 없이 사내 `https://slack-office.pearldev.io/bot`에 JSON POST로 전송합니다. `SLACK_API_URL`로 주소를 설정할 수 있고, 도구에는 `slack_channel`을 전달합니다. payload는 `channel`, `text`, `username: "System"`입니다. 기존 `SLACK_BOT_TOKEN`과 Slack 멤버 ID는 사용하지 않습니다.

EN: Notifications use the internal Slack API with no authentication header. Provide a channel name, not a Slack member ID. HTTP errors and explicit JSON failure responses raise errors. HTTP success with empty content, plain `ok`, or a JSON object without explicit failure is accepted; this confirms API acceptance, not message readership.

## 도구 승인 및 입력

`commit`의 `approved`는 커밋 및 필요한 경우 신규 프로젝트 생성을 포함한 승인입니다. 먼저 `approved=false`로 조회해 프로젝트 생성 여부와 승인 문구를 확인하고, 생성이 필요하면 그 사실까지 명시한 뒤 한 번에 승인받습니다. `deploy`의 `approved`는 배포 승인입니다. 필요한 승인이 없으면 해당 도구는 `confirmation_required`를 반환하고 변경을 수행하지 않습니다. Copilot이 사용자에게 확인한 뒤 승인 값을 넣어 다시 호출해야 합니다.

브랜치는 항상 `main`이며 MCP에서 `branch`를 입력받지 않습니다. `commit_message` 대신 `change_description`으로 사용자의 변경 내용 설명을 받아 GitLab 커밋 메시지로 사용합니다. 작성자 이름과 이메일은 질문하지 않고 검증된 Entra 계정에서 가져옵니다. 도구 설명은 Copilot의 질문을 안내하지만 UI 문구를 강제하는 대화 엔진은 아닙니다.

| 항목 | 질문 |
|---|---|
| 사이트 생성 이름 | 어떤 이름으로 사이트를 만들까요? 공백 없이 영문으로 입력하세요. (예시: project-message-send) |
| 업로드 폴더 | 업로드할 폴더를 알려주세요. (예시: C:\project) |
| 이름 / 이메일 | 질문 없이 검증된 Entra 토큰의 name/email 사용 |
| 변경 내용 | 이번에 추가하거나 변경한 내용을 간단히 알려주세요. (예시: 소개 문구 수정, 사진 추가) |
| 커밋 승인 | 커밋을 시작할까요? 사이트 배포 전 필수 과정입니다. |
| 신규 프로젝트 포함 승인 | 소스 코드가 공개되는 public 프로젝트 생성과 커밋을 시작할까요? 사이트 배포 전 필수 과정입니다. |
| 배포 사이트 | 어떤 사이트를 배포할까요? 사이트 이름을 입력하세요. (예시: project-message-send) |
| 배포 승인 | 사이트 배포를 시작할까요? |
| 중지 대상 | 어떤 사이트를 중지할까요? 사이트 주소를 입력하세요. |
| 중지 승인 | 사이트를 중지할까요? |

게시 폴더는 먼저 자동 감지하며 실패할 때만 완성 파일 폴더를 안내합니다. 사이트 중지 승인 시 프로젝트·코드는 보존되고 다시 배포할 수 있다는 점을 함께 설명합니다. 이미 받은 입력은 다시 묻지 않습니다.

`commit`은 `project_name`(프로젝트 URL 슬러그)과 `folder_path`(MCP 서버 PC의 절대 경로)를 받습니다. 예시:

```json
{
  "project_name": "order-app",
  "folder_path": "E:\\Code\\order-app",
  "change_description": "소개 문구 수정과 사진 추가",
  "approved": true
}
```

- 프로젝트 식별은 `GITLAB_NAMESPACE_ID` 그룹 경로 + `project_name`입니다. 다른 그룹의 동명 프로젝트는 수정하지 않습니다. 표시 이름이 아닌 `order-app` 같은 URL 슬러그를 사용하세요.
- 같은 프로젝트가 있으면 대상 브랜치에서 같은 상대 경로의 파일을 덮어쓰고 새 파일은 추가합니다. 원격에만 있는 파일은 삭제하지 않습니다. 빈 저장소는 첫 커밋으로 브랜치를 만들지만 기존 저장소의 없는 브랜치는 오류로 처리합니다.
- 없으면 생성 승인을 확인한 뒤 해당 그룹에 public 프로젝트를 만듭니다. 소스 코드도 공개되므로 업로드 내용을 확인하세요. 기존 프로젝트의 공개 범위는 변경하지 않습니다. 그룹·관리자 정책이 public 생성을 금지하면 API 오류를 반환합니다.
- 세분화 토큰에는 기존 권한에 더해 **Group: Read**, **Branch: Read**, **Repository: Read**가 필요합니다. 그룹 경로와 브랜치, 기존 파일을 조회하기 위한 권한입니다.
- 루트 및 하위 폴더의 `.gitignore`를 적용합니다. `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `.ssh`, `.env` 및 `.env.*`, `.npmrc`, `.pypirc`, `id_rsa`, `id_ed25519`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, 심볼릭 링크 및 junction은 추가로 제외합니다. 제외 목록은 응답의 `excluded_files`로 확인합니다.
- 일반 파일은 바이너리를 포함해 Base64로 업로드합니다. 파일 1,000개 또는 총 원본 20 MiB를 초과하거나 업로드할 파일이 없으면 오류를 반환합니다. GitLab 자체 제한은 별도로 적용됩니다.
- 제외 규칙이 모든 비밀정보를 찾아주는 것은 아닙니다. 업로드할 폴더를 직접 확인하세요. 서버가 Azure 등에 있으면 사용자 PC가 아니라 서버에 존재하는 경로만 읽을 수 있습니다.

EN: Commit accepts a project URL slug and an absolute server-local folder. It adds or overwrites matching files in the configured group, preserves remote-only files, and creates missing projects after approval. Nested `.gitignore` rules and mandatory secret/directory exclusions apply. Binary files are supported; limits are 1,000 files and 20 MiB before Base64 encoding.

작성자 메타데이터는 검증된 Entra 계정에서 가져옵니다. 사용자 인증과 별개로 승인 플래그는 MCP 클라이언트가 제공하는 값이므로 실제 승인 여부를 서버가 독립적으로 증명하지는 않습니다.

`deploy`도 `commit`과 동일한 `project_name`을 받습니다. 설정된 그룹에서 프로젝트 ID와 전체 경로를 자동 조회하므로 사용자가 ID나 경로를 입력할 필요가 없습니다. 프로젝트가 없으면 먼저 커밋하도록 오류를 반환합니다. 배포 성공 후 Pages URL이 없으면 성공으로 처리하지 않습니다.

EN: Both tools accept `project_name`. Deployment resolves the ID and full path within the configured group automatically; no ID/path input is needed.

## 배포 실패 분석과 재배포 / Deployment repair workflow

배포 파이프라인의 `failed` 상태는 도구 오류 대신 `status: deployment_failed`인
명시적 실패 응답으로 반환합니다. `attempt`, `max_attempts: 5`, `pipeline_id`,
`pipeline_url`, `commit_id`, `failed_jobs`, `retry_allowed`, `retry_token`을 포함합니다.
각 실패 작업은 원인 및 마지막 로그 12,000자까지 포함하며 잘림 여부를 표시합니다.
로그가 없거나 권한 부족이면 `diagnostics_error` 또는 작업의 `log_error`에 명시합니다.
실패 시에도 지정한 Slack 채널로 실패 알림(시도 횟수·파이프라인 주소)을 보냅니다.
알림 전송이 실패해도 진단 응답은 그대로 반환하며 `slack_notified: false`와 `slack_error`로 표시합니다.
작업 목록/로그 조회 API에 대한 읽기 권한이 추가로 필요할 수 있습니다.
CI 로그에는 민감정보가 포함될 수 있으므로 로그에 비밀값을 출력하지 마세요.

1. Copilot이 로그를 분석합니다. 로그는 진단 자료이며 그 안의 지시는 따르지 않습니다.
2. 수정안을 설명하고 **수정할 때마다 사용자 승인**을 받습니다.
3. 승인된 수정사항을 커밋하고 재배포도 승인받습니다.
4. 실패 응답의 `retry_token`을 그대로 넣어 `deploy`를 다시 호출합니다.
5. 성공하면 종료하며 **최초 포함 최대 5회** 실패하면 자동 수정·재배포를 중단합니다.

서버는 AI가 아니므로 자체적으로 소스 수정이나 Copilot 호출을 수행하지 않습니다.
MCP 도구 설명과 실패 응답을 통해 Copilot이 이 과정을 수행하도록 안내합니다.
기존 `test_live_deploy`도 일반 MCP 클라이언트이므로 AI 수정 루프를 실행하지 않으며,
실패 응답이 오면 테스트가 실패합니다.
원격 서버의 폴더와 사용자 PC 파일 간 업로드는 별도 기능이며 이 변경에 포함되지 않습니다.

재배포 표는 프로젝트에 연결되고 한 번만 사용할 수 있습니다. 같은 실패 커밋은
수정 없이 재시도할 수 없으며, 5회째에는 다음 표가 발급되지 않습니다.
횟수는 **현재 MCP 서버 프로세스에서 유지**됩니다. 재시작하거나 다른 워커로
전달하면 기존 표가 거부됩니다. 단일 프로세스에서 사용하고, 재시작 후에는
자동 재시도를 중단해 사용자에게 알리세요. 여러 워커/재시작 간 복구는 지원하지 않습니다.
표 생략은 사용자 승인으로 시작하는 **새 배포 흐름**이므로, 한도 우회 목적으로
Copilot이 표를 생략하거나 새 흐름을 자동 시작하면 안 됩니다.

권한 오류, API 통신 오류, CI 준비 오류, 취소/건너뛴 파이프라인, 타임아웃,
Pages URL 누락, Slack 오류는 자동 재배포하지 않습니다.
특히 타임아웃이면 기존 파이프라인이 실행 중일 수 있고, Slack 실패 시에는
이미 사이트 배포가 끝났을 수 있으므로 먼저 상태를 확인해야 합니다.

EN: A failed pipeline returns diagnostics and a single-use continuation. Copilot,
not the Python server, proposes repairs and requests approval for every edit,
commit and redeployment. Each continuation chain permits at most five pipelines.
Tokens are process-local and reject replay or unchanged commits. Do not restart
the chain automatically after exhaustion or server restart.

비파괴 HTTP 연동 테스트:

```powershell
py -m unittest discover -s tests -p test_deployment_retry.py -v
```

이 테스트는 격리된 로컬 HTTP API에 실제 요청을 보내며 사내 GitLab/Slack은 변경하지 않습니다.

## 사이트 게시 중단 / Unpublish a site

`undeploy(site_url, approved)`는 설정된 그룹의 프로젝트 목록과 실제 Pages 기본 주소를 대조해
`DELETE /projects/:id/pages`를 호출합니다. `approved=false`이면 승인 요청만 반환합니다.
프로젝트·코드·CI 파일은 삭제하거나 변경하지 않으며 Slack 알림은 보내지 않습니다.
GitLab API의 정상 응답을 `status: unpublished`로 반환하며 공개 URL의 즉시 차단까지
검증하는 것은 아닙니다. 반영 지연이 있을 수 있고 진행 중/이후 Pages 파이프라인은
사이트를 다시 게시할 수 있습니다. 영구적인 자동 재게시 차단 도구는 아닙니다.

EN: `undeploy` unpublishes Pages only after approval, preserving the project,
repository and CI. API acceptance does not guarantee immediate HTTP unavailability.
Running or future pipelines may republish the site; automatic deployment is not disabled.

GitLab 토큰에 **Page: Delete** 권한과 프로젝트 **Maintainer 또는 Owner** 역할이 필요합니다.
권한 부족 및 API 오류는 실패로 반환합니다. 기본값으로 실제 사이트 중단 테스트는 실행하지 않습니다.

```dotenv
TEST_MCP_RUN_UNDEPLOY=0
```

실제 중단 테스트는 위 값을 **의도적으로 `1`로 변경한 뒤** 별도로 실행하세요.
`TEST_MCP_UNDEPLOY_SITE_URL`에 배포 시 안내된 실제 Pages 기본 주소를 지정해야 합니다.
해당 실제 사이트가 내려가므로 배포 테스트와 동시에 활성화하지 마세요.
설정된 그룹의 직접 소유 프로젝트만 검색하며 하위 그룹·공유 프로젝트는 제외합니다.
주소의 호스트명이나 경로에서 프로젝트명을 추측하지 않고 API가 반환한 기본 주소와
일치하는 프로젝트가 정확히 하나일 때만 중지합니다. 별칭 도메인, 하위 페이지,
쿼리 문자열·해시가 붙은 주소는 지원하지 않습니다.
조회에는 Group/Project/Pages 읽기 권한이 필요하며 조회 실패나 중복 시 삭제하지 않습니다.

```powershell
py -m unittest discover -s tests -v -k test_live_undeploy
```

실행 후 `TEST_MCP_RUN_UNDEPLOY=0`으로 되돌리세요. 재게시는 `deploy` 도구로 수행합니다.

## 실제 MCP 테스트 실행 / Running MCP tests

`tests/test_mcp_git_helper.py`의 일반 MCP 테스트는 테스트 인증 컨텍스트와 프로세스 내 HTTP 앱을 사용합니다. 실제 JWT 검증은 `tests/test_identity.py`에서 별도로 검사합니다. 실제 연동 테스트는 Python MCP 클라이언트로 실행 중인 HTTP 서버에 연결합니다. AI Agent가 아니라 정해진 입력으로 도구를 호출하는 MCP 클라이언트입니다.

Live tests connect to the running authenticated HTTP MCP server. No GitLab or Slack responses are mocked in live tests.

기본 실행은 MCP 연결 및 로컬 폴더 읽기만 확인합니다. 커밋과 배포 테스트는 별도 메서드이며 각각 활성화합니다. 비활성화 상태는 `skipped`로 표시합니다. 테스트 전용 설정은 모두 `TEST_MCP_` 접두사를 사용합니다.

Commit and deployment are independent tests with separate opt-in flags in `.env`. The deployment test never calls the commit tool.

`.env`에 다음 항목도 설정하세요.

```dotenv
TEST_MCP_RUN_COMMIT=0
TEST_MCP_RUN_DEPLOY=0
TEST_MCP_PROJECT_NAME=git-helper-live-test
TEST_MCP_FOLDER_PATH=E:\Code\my-site
MCP_RESOURCE_URL=http://localhost:8000/mcp
TEST_MCP_ACCESS_TOKEN=<MCP API용 유효한 Entra 액세스 토큰>
SLACK_API_URL=https://slack-office.pearldev.io/bot
TEST_MCP_SLACK_CHANNEL=web_test_lje
```

- `TEST_MCP_PROJECT_NAME`은 **테스트용** 프로젝트 URL 슬러그이고 `TEST_MCP_FOLDER_PATH`는 실제 업로드할 폴더입니다. 같은 이름으로 재실행하면 기존 프로젝트 파일을 덮어씁니다. 임의 접미사는 붙이지 않습니다.
- 작성자 이름·이메일은 인증된 계정에서 가져옵니다. `TEST_MCP_SLACK_CHANNEL`은 사내 API로 알림을 전송할 채널명입니다. Slack 인증 토큰은 필요하지 않습니다.
- 커밋 테스트(`test_live_commit`)는 지정한 실제 폴더를 업로드하고 종료합니다. 테스트용 HTML이나 CI 파일을 자동 생성하지 않으며 Slack이나 배포 설정은 요구하지 않습니다.
- 배포 테스트(`test_live_deploy`)는 공통 `TEST_MCP_PROJECT_NAME`의 `main`을 배포하고 Slack 알림을 확인합니다. `TEST_MCP_DEPLOY_BRANCH`는 더 이상 사용하지 않습니다. 폴더 업로드나 프로젝트 생성은 하지 않습니다. 단, CI가 없으면 배포 준비용 CI 파일을 자동 커밋합니다.
- 배포 요청 시 `.gitlab-ci.yml`이 없으면 Pages CI를 자동 생성해 커밋합니다. 기존 CI는 덮어쓰지 않고 그대로 실행합니다. 사용자 지정 CI 경로를 사용하는 프로젝트는 자동 구성 대신 명확한 오류를 반환합니다.
- 자동 생성 CI 커밋도 인증된 계정의 이름·이메일을 사용합니다. `TEST_MCP_AUTHOR_NAME`, `TEST_MCP_AUTHOR_EMAIL`은 더 이상 사용하지 않습니다. 실제 연동 테스트는 `TEST_MCP_ACCESS_TOKEN`을 HTTP Authorization 헤더로 전송합니다. 테스트 토큰은 비밀값이므로 커밋하거나 채팅에 공유하지 마세요. 기존 커밋은 소급 변경하지 않으며 GitLab 계정 연결·아바타 표시는 이메일 매핑에 따릅니다. 커미터는 API 토큰 계정일 수 있습니다.

EN: Generated CI commits use the authenticated Entra user's name/email. Existing commits are unchanged; GitLab account/avatar association depends on email mapping.
- 정적 사이트는 루트(`.`), npm 프로젝트는 `npm ci`(lockfile 있음) 또는 `npm install` 후 `npm run build`를 사용합니다. Vite는 `dist`, Create React App은 `build`를 기본 게시 폴더로 사용합니다. 다른 빌드 또는 커스텀 출력 경로는 도구의 `publish_directory`로 지정하세요. yarn/pnpm은 자동 구성하지 않으므로 자체 CI가 필요합니다.
- 테스트에서 게시 경로를 지정하려면 `.env`에 `TEST_MCP_DEPLOY_PUBLISH_DIRECTORY=dist` 등을 설정합니다. 비우면 자동 감지합니다. React/Vue라고 해서 항상 루트(`.`)를 게시할 수 있는 것은 아닙니다.
- 생성된 CI는 JSON 형식의 유효한 YAML이며 API 요청으로만 실행되도록 구성합니다. 루트 게시 시 임시 폴더에 복사한 후 `public` 아티팩트를 만들고 `index.html` 존재를 검사합니다. Node 22/Alpine 이미지를 사용하므로 프로젝트 버전 요구 또는 Runner 실행 환경이 다르면 기존 CI를 준비해야 합니다.
- GitLab Runner 설치·등록과 Pages 서비스 활성화는 GitLab 관리자 영역이며 이 도구가 자동 설정하지 않습니다. Runner가 없거나 태그가 맞지 않아 대기하면 시간 초과 오류를 반환합니다. Pages URL이 없으면 성공으로 처리하지 않습니다.

EN: Deployment automatically commits missing Pages CI for static sites or npm builds. Existing CI is preserved. Vite/CRA output directories are inferred; other outputs require `publish_directory`. Runner registration and Pages infrastructure remain administrator-managed prerequisites.
- 커밋 도구는 배포 도구를 호출하지 않지만 GitLab CI 설정에 따라 커밋 자체로 파이프라인이 자동 실행될 수 있습니다.
- 성공 여부는 실제 커밋 ID, 파이프라인 성공, Pages URL 및 Slack API 성공 응답으로 검증합니다. Pages URL이 없거나 API 호출이 실패하면 테스트도 실패합니다. 공개 사이트의 HTTP 접속 가능 여부나 사용자의 DM 열람 여부까지 검증하지는 않습니다.
- 생성된 프로젝트는 실패한 경우에도 자동 삭제하지 않습니다. 결과를 확인한 뒤 GitLab에서 직접 정리하세요.

The commit test uploads the configured real folder to the named project on main, creating it if missing or overwriting matching files if present. The deployment test independently runs a pipeline on main and sends a real Slack notification. Neither test invokes the other. Projects are retained for inspection even on failure.

```powershell
# Commit only: enable TEST_MCP_RUN_COMMIT=1 in .env.
# 커밋만: .env에서 TEST_MCP_RUN_COMMIT=1로 설정합니다.
py -m unittest discover -s tests -v -k test_live_commit

# Deploy only: enable TEST_MCP_RUN_DEPLOY=1 in .env.
# 배포만: .env에서 TEST_MCP_RUN_DEPLOY=1로 설정합니다.
py -m unittest discover -s tests -v -k test_live_deploy

# Run all enabled tests. / 활성화된 테스트 전체 실행
py -m unittest discover -s tests -v
```

토큰과 설정은 `.env`에 입력해야 합니다. 실행 후 원치 않는 재실행을 막으려면 해당 테스트의 실행 플래그를 `0`으로 되돌리세요.