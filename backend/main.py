from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AI Code Screening API", version="0.4.0")

GITHUB_API = "https://api.github.com"
SUPPORTED_EXTENSIONS = {
    ".kt", ".java", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".cpp", ".c", ".h", ".cs", ".swift", ".dart"
}
IGNORED_NAMES = {"build", ".gradle", "node_modules", "vendor", "dist"}
MAX_FILES = 40


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
        raise HTTPException(
            status_code=403,
            detail="GitHub отклонил запрос. Проверьте GITHUB_TOKEN и лимит GitHub API.",
        )
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

    # Repeated natural-language comments/strings can be a useful stylometric signal.
    normalized: dict[str, list[int]] = {}
    for line_no, line in non_empty:
        candidate = re.sub(r"\s+", " ", line.strip().lower())
        if len(candidate) >= 28 and not candidate.startswith(("//", "#", "/*", "*")):
            normalized.setdefault(candidate, []).append(line_no)
    for text, line_numbers in normalized.items():
        if len(line_numbers) >= 3:
            for line_no in line_numbers[:3]:
                add_evidence(line_no, lines[line_no - 1], 5, "повторяющаяся идентичная конструкция")

    # Function declarations with many control-flow tokens are highlighted as a place
    # worth explaining during an interview. Structural metrics are useful in code-AI
    # research, but are not sufficient by themselves to claim AI authorship.
    function_decl = re.compile(r"^\s*(?:fun|func|def|function)\s+[A-Za-z_][A-Za-z0-9_]*")
    control = re.compile(r"\b(if|else|for|while|switch|when|try|catch|match)\b")
    for line_no, line in non_empty:
        if function_decl.search(line):
            window = "\n".join(lines[line_no - 1: min(len(lines), line_no + 45)])
            controls = len(control.findall(window))
            if controls >= 8:
                add_evidence(line_no, line, min(12, controls), "сложная функция с большим числом управляющих конструкций")

    line_count = len(lines)
    if line_count >= 80:
        # File-level size is reported only when we already have concrete evidence.
        if evidence:
            score += 5
            reasons.append("крупный файл усиливает необходимость ручной проверки")

    score = min(score, 95)
    # Deduplicate evidence by line while preserving the strongest first occurrence.
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalyzeRequest) -> AnalysisResponse:
    try:
        owner, repo = parse_repo(request.repository_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=30) as client:
        repo_data = await github_get(client, f"/repos/{owner}/{repo}")
        default_branch = repo_data.get("default_branch") or "main"
        tree = await github_get(
            client,
            f"/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1",
        )

        findings: list[Finding] = []
        files_analyzed = 0
        for item in tree.get("tree", []):
            path = item.get("path", "")
            if item.get("type") != "blob" or not is_source_file(path):
                continue
            if files_analyzed >= MAX_FILES:
                break

            files_analyzed += 1
            blob = await github_get(client, f"/repos/{owner}/{repo}/git/blobs/{item['sha']}")
            try:
                encoded = blob.get("content", "")
                code = base64.b64decode(encoded).decode("utf-8", errors="ignore")
            except (ValueError, TypeError):
                continue

            score, reasons, evidence = evidence_for_code(code)
            if score >= 8 and evidence:
                line_numbers = sorted({item.line for item in evidence})
                if len(line_numbers) == 1:
                    line_range = str(line_numbers[0])
                else:
                    line_range = ", ".join(str(n) for n in line_numbers)
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
        files_analyzed=files_analyzed,
        findings=findings,
        questions=make_questions(findings),
    )
