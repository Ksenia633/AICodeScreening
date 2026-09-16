from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AI Code Screening API", version="0.5.0")

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

    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    if not owner or not repo:
        raise ValueError("Не удалось определить owner и repository")
    return owner, repo


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
        raise HTTPException(
            status_code=404,
            detail="Репозиторий не найден. Если он приватный, задайте GITHUB_TOKEN на backend.",
        )
    if response.status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        message = response.text[:300]
        if remaining == "0":
            detail = (
                "GitHub API исчерпал лимит запросов для этого backend. "
                "Добавьте GITHUB_TOKEN или дождитесь сброса лимита. "
                f"Сейчас remaining={remaining}, reset={reset}."
            )
        else:
            detail = f"GitHub отклонил запрос (403). Ответ: {message}"
        raise HTTPException(status_code=403, detail=detail)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=f"GitHub API error: {response.text[:300]}")
    return response.json()


def is_source_file(path: str) -> bool:
    parts = path.lower().split("/")
    return any(path.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS) and not any(
        part in IGNORED_NAMES for part in parts
    )


def evidence_for_code(code: str) -> tuple[int, list[str], list[Evidence]]:
    """Return a screening score plus reasons and exact lines that triggered them.

    This is deliberately evidence-first: a finding should point to concrete lines.
    It is not proof of AI authorship.
    """
    lines = code.splitlines()
    non_empty = [(i + 1, line) for i, line in enumerate(lines) if line.strip()]
    score = 0
    reasons: list[str] = []
    evidence: list[Evidence] = []

    def add_evidence(line_no: int, text: str, points: int, reason: str) -> None:
        nonlocal score
        score += points
        evidence.append(Evidence(line=line_no, text=text.strip()[:240], reason=reason))
        if reason not in reasons:
            reasons.append(reason)

    ai_marker = re.compile(r"generated\s+by|chatgpt|copilot|ai[- ]generated|openai", re.IGNORECASE)
    template_marker = re.compile(r"TODO|FIXME|PLACEHOLDER|IMPLEMENT\s+HERE|YOUR\s+CODE", re.IGNORECASE)
    generic_comment = re.compile(
        r"^\s*(?://|#|/\*|\*)\s*(this|that|the|function|method|class|returns?|handles?|this method)\b",
        re.IGNORECASE,
    )

    for line_no, line in non_empty:
        if ai_marker.search(line):
            add_evidence(line_no, line, 55, "явный маркер AI/generated в строке")
        elif template_marker.search(line):
            add_evidence(line_no, line, 12, "шаблонный маркер TODO/FIXME/PLACEHOLDER")
        elif generic_comment.search(line):
            add_evidence(line_no, line, 8, "шаблонный общий комментарий")

        if len(line.rstrip()) >= 140:
            add_evidence(line_no, line, 5, "необычно длинная строка")

    normalized: dict[str, list[int]] = {}
    for line_no, line in non_empty:
        candidate = re.sub(r"\s+", " ", line.strip().lower())
        if len(candidate) >= 28 and not candidate.startswith(("//", "#", "/*", "*")):
            normalized.setdefault(candidate, []).append(line_no)
    for _, line_numbers in normalized.items():
        if len(line_numbers) >= 3:
            for line_no in line_numbers[:3]:
                add_evidence(line_no, lines[line_no - 1], 5, "повторяющаяся идентичная конструкция")

    function_decl = re.compile(r"^\s*(?:fun|func|def|function)\s+[A-Za-z_][A-Za-z0-9_]*")
    control = re.compile(r"\b(if|else|for|while|switch|when|try|catch|match)\b")
    for line_no, line in non_empty:
        if function_decl.search(line):
            window = "\n".join(lines[line_no - 1: min(len(lines), line_no + 45)])
            controls = len(control.findall(window))
            if controls >= 8:
                add_evidence(line_no, line, min(12, controls), "сложная функция с большим числом управляющих конструкций")

    if len(lines) >= 80 and evidence:
        score += 5
        reasons.append("крупный файл усиливает необходимость ручной проверки")

    score = min(score, 95)
    unique: dict[int, Evidence] = {}
    for item in evidence:
        if item.line not in unique or item.reason.startswith("явный"):
            unique[item.line] = item
    evidence = list(unique.values())[:8]
    return score, reasons, evidence


def make_questions(findings: list[Finding]) -> list[str]:
    questions: list[str] = []
    for finding in findings[:5]:
        questions.append(
            f"Объясните своими словами строки {finding.lines} в {finding.file}. Почему выбран именно такой подход?"
        )
        questions.append(
            f"Какие альтернативы решению в {finding.file} вы рассматривали и какие у них компромиссы?"
        )
    if not questions:
        questions.extend([
            "Какой самый сложный участок проекта вы реализовали самостоятельно и почему?",
            "Какие части проекта вы бы переработали при дальнейшем развитии?",
        ])
    return questions[:8]


async def download_repository_zip(client: httpx.AsyncClient, owner: str, repo: str, branch: str) -> dict[str, str]:
    """Download the repository as one archive instead of making one API request per file.

    This keeps repeated coursework demos well below GitHub's unauthenticated API limit.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    response = await client.get(url, headers=headers(), follow_redirects=True)
    if response.status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining == "0":
            raise HTTPException(
                status_code=403,
                detail=(
                    "GitHub API исчерпал лимит запросов. "
                    "Добавьте GITHUB_TOKEN на backend или дождитесь сброса. "
                    f"remaining={remaining}, reset={reset}."
                ),
            )
        raise HTTPException(status_code=403, detail="GitHub отклонил скачивание архива (403).")
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=f"GitHub archive error: {response.text[:300]}")

    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=502, detail="GitHub вернул некорректный архив репозитория.") from exc

    result: dict[str, str] = {}
    for member in archive.infolist():
        raw_name = member.filename
        parts = raw_name.split("/", 1)
        path = parts[1] if len(parts) == 2 else raw_name
        if not path or not is_source_file(path) or member.is_dir():
            continue
        if member.file_size > MAX_FILE_BYTES:
            continue
        try:
            code = archive.read(member).decode("utf-8", errors="ignore")
        except (OSError, RuntimeError):
            continue
        result[path] = code
        if len(result) >= MAX_FILES:
            break
    return result


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalyzeRequest) -> AnalysisResponse:
    try:
        owner, repo = parse_repo(request.repository_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=60) as client:
        repo_data = await github_get(client, f"/repos/{owner}/{repo}")
        default_branch = repo_data.get("default_branch") or "main"
        source_files = await download_repository_zip(client, owner, repo, default_branch)

        findings: list[Finding] = []
        for path, code in source_files.items():
            score, reasons, evidence = evidence_for_code(code)
            if score >= 8 and evidence:
                line_numbers = sorted({item.line for item in evidence})
                line_range = str(line_numbers[0]) if len(line_numbers) == 1 else ", ".join(map(str, line_numbers))
                findings.append(
                    Finding(
                        file=path,
                        lines=line_range,
                        score=score,
                        reason="; ".join(reasons),
                        evidence=evidence,
                    )
                )

    findings.sort(key=lambda item: item.score, reverse=True)
    return AnalysisResponse(
        repository=repo_data.get("full_name", f"{owner}/{repo}"),
        files_analyzed=len(source_files),
        findings=findings,
        questions=make_questions(findings),
    )
