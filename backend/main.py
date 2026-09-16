from __future__ import annotations

import io
import os
import zipfile
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from detector import FileAnalysisResult, Finding as DetectorFinding, build_profile
from scoring import repository_signal, screen_profile

app = FastAPI(title="AI Code Screening API", version="0.9.0")
GITHUB_API = "https://api.github.com"
SUPPORTED_EXTENSIONS = {".kt",".java",".py",".js",".ts",".tsx",".jsx",".go",".rs",".cpp",".c",".h",".cs",".swift",".dart"}
IGNORED_NAMES = {"build",".gradle","node_modules","vendor","dist",".git"}
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
    screening_signal: int
    file_results: list[FileAnalysisResult]
    findings: list[Finding]
    questions: list[str]


def parse_repo(url: str) -> tuple[str,str]:
    parsed=urlparse(url.strip())
    if parsed.scheme not in {"http","https"} or parsed.netloc.lower() not in {"github.com","www.github.com"}:
        raise ValueError("Нужна ссылка вида https://github.com/owner/repository")
    parts=[p for p in parsed.path.split("/") if p]
    if len(parts)<2: raise ValueError("Ссылка должна иметь вид https://github.com/owner/repository")
    return parts[0],parts[1].removesuffix(".git")


def headers() -> dict[str,str]:
    h={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"ai-code-screening-coursework"}
    token=os.getenv("GITHUB_TOKEN")
    if token: h["Authorization"]=f"Bearer {token}"
    return h

async def github_get(client: httpx.AsyncClient,path: str) -> Any:
    r=await client.get(f"{GITHUB_API}{path}",headers=headers())
    if r.status_code==404: raise HTTPException(404,"Репозиторий не найден. Для приватного репозитория задайте GITHUB_TOKEN.")
    if r.status_code==403 and r.headers.get("x-ratelimit-remaining")=="0":
        raise HTTPException(403,"GitHub API исчерпал лимит. Добавьте GITHUB_TOKEN.")
    if r.status_code>=400: raise HTTPException(r.status_code,f"GitHub API error: {r.text[:300]}")
    return r.json()


def is_source_file(path: str) -> bool:
    parts=path.lower().split("/")
    return any(path.lower().endswith(x) for x in SUPPORTED_EXTENSIONS) and not any(p in IGNORED_NAMES for p in parts)

async def download_repository_zip(client: httpx.AsyncClient,owner: str,repo: str,branch: str) -> dict[str,str]:
    r=await client.get(f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}",headers=headers(),follow_redirects=True)
    if r.status_code>=400: raise HTTPException(r.status_code,"Не удалось получить архив GitHub.")
    try: archive=zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile as e: raise HTTPException(502,"GitHub вернул некорректный архив.") from e
    result={}
    for member in archive.infolist():
        parts=member.filename.split("/",1); path=parts[1] if len(parts)==2 else member.filename
        if not path or member.is_dir() or not is_source_file(path) or member.file_size>MAX_FILE_BYTES: continue
        try: result[path]=archive.read(member).decode("utf-8",errors="ignore")
        except (OSError,RuntimeError): continue
        if len(result)>=MAX_FILES: break
    return result


def to_legacy_finding(path: str, result: FileAnalysisResult) -> Finding | None:
    if not result.findings: return None
    # Only surface findings with actual evidence. The repository score is continuous independently.
    selected=result.findings[:8]
    meaningful=[f for f in selected if f.weight>=0.14]
    if not meaningful: return None
    score=round(min(95,100*(1.0-__import__('math').prod(1.0-min(.85,f.weight) for f in meaningful))))
    nums=sorted({f.line_start for f in meaningful})
    evidence=[Evidence(line=f.line_start,text=f.suspected_text.replace("\n"," ")[:260],reason=f.reason) for f in meaningful]
    return Finding(file=path,lines=str(nums[0]) if len(nums)==1 else ", ".join(map(str,nums)),score=score,
                   reason="; ".join(dict.fromkeys(f.reason for f in meaningful)),evidence=evidence)

@app.get("/health")
async def health()->dict[str,str]:
    from detector import Parser
    return {"status":"ok","ast":"tree-sitter" if Parser is not None else "fallback"}

@app.post("/api/analyze",response_model=AnalysisResponse)
async def analyze(request: AnalyzeRequest)->AnalysisResponse:
    try: owner,repo=parse_repo(request.repository_url)
    except ValueError as e: raise HTTPException(400,str(e)) from e
    async with httpx.AsyncClient(timeout=60) as client:
        repo_data=await github_get(client,f"/repos/{owner}/{repo}")
        branch=repo_data.get("default_branch") or "main"
        source_files=await download_repository_zip(client,owner,repo,branch)
    profiles=[build_profile(path,code) for path,code in source_files.items()]
    file_results=[screen_profile(p,profiles) for p in profiles]
    file_results.sort(key=lambda r:r.total_score,reverse=True)
    findings=[]
    for result in file_results:
        item=to_legacy_finding(result.file_path,result)
        if item: findings.append(item)
    findings.sort(key=lambda x:x.score,reverse=True)
    questions=[]
    for finding in findings[:5]:
        questions.append(f"Объясните своими словами строки {finding.lines} в {finding.file}. Почему выбран именно такой подход?")
        questions.append(f"Какие альтернативы решению в {finding.file} вы рассматривали и какие у них компромиссы?")
    if not questions:
        questions=["Какой самый сложный участок проекта вы реализовали самостоятельно и почему?","Какие части проекта вы бы переработали при дальнейшем развитии?"]
    return AnalysisResponse(repository=repo_data.get("full_name",f"{owner}/{repo}"),files_analyzed=len(source_files),
        screening_signal=round(repository_signal(file_results)*100),file_results=file_results,findings=findings,questions=questions[:8])
