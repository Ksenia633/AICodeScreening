from __future__ import annotations

import io
import os
import re
import statistics
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AI Code Screening API", version="0.6.0")

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


@dataclass
class FileProfile:
    path: str
    code: str
    language: str
    non_empty_lines: int
    comment_lines: int
    avg_line_length: float
    long_lines: int
    function_count: int
    control_count: int
    string_literal_count: int
    generic_comment_count: int


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


def language_for(path: str) -> str:
    ext = os.path.splitext(path.lower())[1]
    return {
        ".kt": "kotlin", ".java": "java", ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".jsx": "javascript", ".go": "go", ".rs": "rust", ".cpp": "cpp",
        ".c": "c", ".h": "c", ".cs": "csharp", ".swift": "swift", ".dart": "dart",
    }.get(ext, "unknown")


def comment_pattern(language: str) -> re.Pattern[str]:
    return re.compile(r"^\s*(?://|#|/\*|\*|<!--)")


def function_pattern(language: str) -> re.Pattern[str]:
    if language in {"kotlin", "java", "swift", "dart"}:
        return re.compile(r"^\s*(?:(?:public|private|protected|internal|static|override|suspend|async)\s+)*(?:fun|[A-Za-z_][\w<>?\[\]]*\s+[A-Za-z_]\w*)\s*\(")
    if language in {"python"}:
        return re.compile(r"^\s*(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(")
    if language in {"javascript", "typescript"}:
        return re.compile(r"^\s*(?:(?:export|default|async|function)\s+)+[A-Za-z_$][\w$]*\s*(?:<[^>]+>)?\s*\(")
    if language == "go":
        return re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?[A-Za-z_]\w*\s*\(")
    return re.compile(r"^\s*(?:def|func|function)\s+[A-Za-z_]\w*\s*\(")


def build_profile(path: str, code: str) -> FileProfile:
    language = language_for(path)
    lines = code.splitlines()
    non_empty = [line for line in lines if line.strip()]
    comments = [line for line in non_empty if comment_pattern(language).search(line)]
    function_re = function_pattern(language)
    controls = sum(len(re.findall(r"\b(if|else|for|while|switch|when|try|catch|match)\b", line)) for line in non_empty)
    strings = sum(len(re.findall(r"(['\"]).*?\1", line)) for line in non_empty)
    generic_comments = sum(
        1 for line in comments
        if re.search(r"\b(this|the|function|method|class|widget|component|returns?|handles?|purpose|responsible for)\b", line, re.I)
    )
    return FileProfile(
        path=path,
        code=code,
        language=language,
        non_empty_lines=len(non_empty),
        comment_lines=len(comments),
        avg_line_length=statistics.mean(len(line.rstrip()) for line in non_empty) if non_empty else 0,
        long_lines=sum(1 for line in non_empty if len(line.rstrip()) >= 140),
        function_count=sum(1 for line in non_empty if function_re.search(line)),
        control_count=controls,
        string_literal_count=strings,
        generic_comment_count=generic_comments,
    )


def evidence_for_code(profile: FileProfile, repo_profiles: list[FileProfile]) -> tuple[int, list[str], list[Evidence]]:
    """Score combinations of AI-like signals, not generic code repetition.

    Repeated UI/build constructs are deliberately NOT treated as AI evidence.
    The score is a screening signal, not a probability or proof of authorship.
    """
    code = profile.code
    lines = code.splitlines()
    non_empty = [(i + 1, line) for i, line in enumerate(lines) if line.strip()]
    score = 0
    reasons: list[str] = []
    evidence: list[Evidence] = []

    def add(line_no: int, text: str, points: int, reason: str) -> None:
        nonlocal score
        score += points
        evidence.append(Evidence(line=line_no, text=text.strip()[:240], reason=reason))
        if reason not in reasons:
            reasons.append(reason)

    # 1. Strong provenance markers. These are evidence about origin only when explicitly present.
    ai_marker = re.compile(r"generated\s+by|chatgpt|copilot|ai[- ]generated|openai|gemini|claude", re.I)
    for line_no, line in non_empty:
        if ai_marker.search(line):
            add(line_no, line, 70, "явный маркер AI-инструмента или сгенерированного кода")

    # 2. Template / unfinished-generation markers. Low weight because humans use them too.
    template_marker = re.compile(r"TODO|FIXME|PLACEHOLDER|IMPLEMENT\s+HERE|YOUR\s+CODE|ADD\s+YOUR", re.I)
    for line_no, line in non_empty:
        if template_marker.search(line):
            add(line_no, line, 4, "шаблонный маркер незавершённого участка")

    # 3. AI-style explanatory comments: generic, redundant comments are useful only in combination
    # with other signals. Flutter/Android UI comments are not penalized merely for being repetitive.
    generic_comment = re.compile(
        r"^\s*(?://|#|/\*|\*)\s*(this (function|method|class|widget|component)|the (function|method|purpose)|returns?\s+the|handles?\s+the|responsible for)",
        re.I,
    )
    comment_hits = 0
    for line_no, line in non_empty:
        if generic_comment.search(line):
            comment_hits += 1
            add(line_no, line, 3, "избыточный шаблонный комментарий")
    if comment_hits >= 4:
        score += 3
        reasons.append("много однотипных поясняющих комментариев в одном файле")

    # 4. Long function + many branches. This is a review signal only; AI code often contains
    # simple but verbose generated functions, while legitimate UI builders can also be large.
    function_re = function_pattern(profile.language)
    control_re = re.compile(r"\b(if|else|for|while|switch|when|try|catch|match)\b")
    for line_no, line in non_empty:
        if function_re.search(line):
            window = lines[line_no - 1:min(len(lines), line_no + 60)]
            body = "\n".join(window)
            controls = len(control_re.findall(body))
            approx_lines = len(window)
            if controls >= 7 and approx_lines >= 30:
                add(line_no, line, 5, "функция одновременно длинная и насыщена управляющей логикой")

    # 5. Defensive/boilerplate clusters. One null check is normal; a dense chain in a short
    # function is more useful as a review signal when combined with long generated-looking code.
    defensive = re.compile(r"\b(null|None|undefined|Optional|try|catch|except|isEmpty|isNotEmpty|requireNotNull)\b", re.I)
    for line_no, line in non_empty:
        if defensive.search(line) and len(line) > 110:
            add(line_no, line, 2, "длинная защитная/boilerplate-конструкция")

    # 6. Repository-relative style outlier. A generated block may have a formatting/style profile
    # unlike the author's surrounding files. We compare within the same language, not against a universal style.
    same_language = [p for p in repo_profiles if p.language == profile.language and p.non_empty_lines >= 20]
    if len(same_language) >= 3:
        avg_lengths = [p.avg_line_length for p in same_language]
        comment_ratios = [p.comment_lines / max(1, p.non_empty_lines) for p in same_language]
        median_len = statistics.median(avg_lengths)
        median_comments = statistics.median(comment_ratios)
        this_comment_ratio = profile.comment_lines / max(1, profile.non_empty_lines)
        if median_len > 0 and profile.avg_line_length > median_len * 1.7:
            score += 4
            reasons.append("стиль файла заметно отличается от других файлов этого языка")
            longest = sorted(non_empty, key=lambda item: len(item[1]), reverse=True)[:2]
            for line_no, line in longest:
                add(line_no, line, 1, "строка выбивается по длине относительно стиля репозитория")
        if median_comments == 0 and this_comment_ratio > 0.20:
            score += 3
            reasons.append("файл содержит необычно высокую долю поясняющих комментариев")

    # 7. Complexity mismatch: a very simple file with unusually verbose comments/strings can be
    # worth checking, but it is intentionally low-weight because generated code can be human-edited.
    if profile.non_empty_lines >= 35:
        complexity_density = profile.control_count / profile.non_empty_lines
        text_density = (profile.comment_lines + profile.string_literal_count) / profile.non_empty_lines
        if complexity_density < 0.08 and text_density > 0.35 and profile.generic_comment_count >= 2:
            score += 4
            reasons.append("много поясняющего текста при относительно простой логике")

    score = min(score, 95)

    # Do not create findings from weak standalone style signals. Require either one strong signal
    # or at least two independent weak/medium signals.
    strong = any(e.reason.startswith("явный маркер") for e in evidence)
    independent_reasons = len(set(reasons))
    if not strong and independent_reasons < 2:
        return 0, [], []

    unique: dict[int, Evidence] = {}
    for item in evidence:
        if item.line not in unique or item.reason.startswith("явный маркер"):
            unique[item.line] = item
    evidence = list(unique.values())[:8]
    return score, reasons, evidence


def make_questions(findings: list[Finding]) -> list[str]:
    questions: list[str] = []
    for finding in findings[:5]:
        questions.append(f"Объясните своими словами строки {finding.lines} в {finding.file}. Почему выбран именно такой подход?")
        questions.append(f"Какие альтернативы решению в {finding.file} вы рассматривали и какие у них компромиссы?")
    if not questions:
        questions.extend([
            "Какой самый сложный участок проекта вы реализовали самостоятельно и почему?",
            "Какие части проекта вы бы переработали при дальнейшем развитии?",
        ])
    return questions[:8]


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
        profiles = [build_profile(path, code) for path, code in source_files.items()]

        findings: list[Finding] = []
        for profile in profiles:
            score, reasons, evidence = evidence_for_code(profile, profiles)
            if score >= 8 and evidence:
                line_numbers = sorted({item.line for item in evidence})
                line_range = str(line_numbers[0]) if len(line_numbers) == 1 else ", ".join(map(str, line_numbers))
                findings.append(Finding(file=profile.path, lines=line_range, score=score, reason="; ".join(reasons), evidence=evidence))

    findings.sort(key=lambda item: item.score, reverse=True)
    return AnalysisResponse(
        repository=repo_data.get("full_name", f"{owner}/{repo}"),
        files_analyzed=len(source_files),
        findings=findings,
        questions=make_questions(findings),
    )
