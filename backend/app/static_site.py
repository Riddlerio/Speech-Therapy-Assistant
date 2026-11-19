"""프로덕션에서 빌드된 프런트엔드(dist) 파일을 백엔드가 직접 서비스한다.
`frontend`에서 `npm.cmd run build`로 만든 `frontend/dist`를 `FRONTEND_DIST`로 지정하면 uvicorn 하나로 API(`/api/...`)와 SPA(`/`, `/play/...`, `/therapist/...`)를 같은 origin에서 서비스한다.
HTML·정적 파일 응답에는 main.py의 보안 헤더 미들웨어가 HTTP 헤더로 CSP(frame-ancestors 포함)를 붙인다.
"""
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[2]
CSP_FILE = ROOT / "shared" / "frontend_csp.json"


def frontend_csp() -> str:
    """shared/frontend_csp.json의 Vite 빌드 meta와 같은 정책에, meta로는 적용되지 않는 frame-ancestors를 더해 CSP 문자열을 만든다.
    프런트엔드 화면 응답에 붙일 보안 헤더 값(FRONTEND_CSP)을 서버 시작 때 한 번 계산한다.
    """
    directives = json.loads(CSP_FILE.read_text(encoding="utf-8"))["directives"]
    return "; ".join([*directives, "frame-ancestors 'none'"])


FRONTEND_CSP = frontend_csp()


def is_frontend_path(path: str) -> bool:
    """요청 경로가 API·API 문서가 아닌 프런트엔드 화면 경로인지 판단한다.
    main.py 보안 헤더 미들웨어가 화면용 CSP와 API용 CSP 중 무엇을 붙일지 고를 때 쓴다.
    """
    return not (path == "/api" or path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"})


def serve(dist: str, path: str):
    """dist 안의 파일을 돌려준다. 확장자가 없는 경로는 SPA 화면이므로 index.html을 돌려준다.
    main.py의 마지막 catch-all 라우트가 부르며, dist 밖으로 나가는 경로와 없는 정적 파일은 404로 막는다.
    """
    if path == "api" or path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if not dist:
        raise HTTPException(404)
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(404)
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:  # 경로 이탈 방지
        raise HTTPException(404)
    if candidate.is_file():
        return FileResponse(candidate)
    if "." in Path(path).name:  # 없는 정적 파일은 index.html로 대신하지 않는다.
        raise HTTPException(404)
    return FileResponse(index, media_type="text/html")
