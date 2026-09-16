from __future__ import annotations

import io
import os
import zipfile
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from detector import Evidence as DetectorEvidence
from detector import evidence_for_code, build_profile

app = FastAPI(title="AI Code Screening API", version="0.7.0")

GITHUB_API = "https://api.github.com"
SUPPORTED_EXTENSIONS = {
    ".kt", ".java", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".cpp", ".c", ".h", ".cs", ".swift", ".dart"
}
IGNORED_NAMES = {"build", ".gradle", "node_modules", "vendor", "dist", ".git"}
MAX_FILES = 40
MAX_FILE_BYTES = 1_000_000


class AnalyzeRequest(BaseModel):
    repository_url: str = Field(..., min_length=1)


class Evidence(BaseModel):
    line: int
    text: str
    reason: str


class Finding(BaseModel):
    file: str
    lines: str
    score: int
    reason: str
    evidence: list[Evidence]


class AnalysisResponse(BaseModel):
    repository: str
    files_analyzed: int
    findings: list[Finding]
    questions: list[str]


def parse_repo(url: str) -> tuple[str, str]:
    value = url.strip()
    if not value:
        raise ValueError("Введите ссылку на GitHub repository")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Нужна ссылка вида https://github.com/owner/repository")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("Ссылка должна иметь вид https://github.com/owner/repository")
    return parts[0], parts[1].removesuffix(".git")


def headers() -> dict[str, str]:
    result = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-code-screening-coursework",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


async def github_get(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(f"{GITHUB_API}{path}", headers=headers())
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Репозиторий не найден. Если он приватный, задайте GITHUB_TOKEN на backend.")
    if response.status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining == "0":
            raise HTTPException(status_code=403, detail=f"GitHub API исчерпал лимит. Добавьте GITHUB_TOKEN или дождитесь сброса. reset={reset}.")
        raise HTTPException(status_code=403, detail=f"GitHub отклонил запрос (403): {response.text[:300]}")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=f"GitHub API error: {response.text[:300]}")
    return response.json()


def is_source_file(path: str) -> bool:
    parts = path.lower().split("/")
    return any(path.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS) and not any(part in IGNORED_NAMES for part in parts)


async def download_repository_zip(client: httpx.AsyncClient, owner: str, repo: str, branch: str) -> dict[str, str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    response = await client.get(url, headers=headers(), follow_redirects=True)
    if response.status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining == "0":
            raise HTTPException(status_code=403, detail=f"GitHub API исчерпал лимит. Добавьте GITHUB_TOKEN или дождитесь сброса. reset={reset}.")
        raise HTTPException(status_code=403, detail="GitHub отклонил скачивание архива (403).")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=f"GitHub archive error: {response.text[:300]}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=502, detail="GitHub вернул некорректный архив репозитория.") from exc

    result: dict[str, str] = {}
    for member in archive.infolist():
        parts = member.filename.split("/", 1)
        path = parts[1] if len(parts) == 2 else member.filename
        if not path or member.is_dir() or not is_source_file(path) or member.file_size > MAX_FILE_BYTES:
            continue
        try:
            result[path] = archive.read(member).decode("utf-8", errors="ignore")
        except (OSError, RuntimeError):
            continue
        if len(result) >= MAX_FILES:
            break
    return result


@app.get("/health")
async def health() -> dict[str, str]:
    from detector import Parser
    return {"status": "ok", "ast": "tree-sitter" if Parser is not None else "fallback"}


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalyzeRequest) -> AnalysisResponse:
    try:
        owner, repo = parse_repo(request.repository_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=60) as client:
        repo_data = await github_get(client, f"/repos/{owner}/{repo}")
        branch = repo_data.get("default_branch") or "main"
        source_files = await download_repository_zip(client, owner, repo, branch)

    profiles = [build_profile(path, code) for path, code in source_files.items()]
    findings: list[Finding] = []
    for profile in profiles:
        finding = evidence_for_code(profile, profiles)
        if finding is None:
            continue
        findings.append(
            Finding(
                file=finding.file,
                lines=finding.lines,
                score=finding.score,
                reason=finding.reason,
                evidence=[Evidence(line=e.line, text=e.text, reason=e.reason) for e in finding.evidence],
            )
        )

    findings.sort(key=lambda item: item.score, reverse=True)
    questions: list[str] = []
    for finding in findings[:5]:
        questions.append(f"Объясните своими словами строки {finding.lines} в {finding.file}. Почему выбран именно такой подход?")
        questions.append(f"Какие альтернативы решению в {finding.file} вы рассматривали и какие у них компромиссы?")
    if not questions:
        questions = [
            "Какой самый сложный участок проекта вы реализовали самостоятельно и почему?",
            "Какие части проекта вы бы переработали при дальнейшем развитии?",
        ]

    return AnalysisResponse(
        repository=repo_data.get("full_name", f"{owner}/{repo}"),
        files_analyzed=len(source_files),
        findings=findings,
        questions=questions[:8],
    )
