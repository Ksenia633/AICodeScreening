from __future__ import annotations

import hashlib
import os
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
except ImportError:  # pragma: no cover
    Parser = None
    get_language = None


class Finding(BaseModel):
    type: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    suspected_text: str
    weight: float = Field(ge=0.0, le=1.0)
    reason: str


class FileAnalysisResult(BaseModel):
    file_path: str
    is_suspicious: bool
    total_score: float = Field(ge=0.0, le=1.0)
    findings: list[Finding]


@dataclass(frozen=True)
class Evidence:
    line: int
    text: str
    reason: str


@dataclass
class FindingData:
    file: str
    lines: str
    score: int
    reason: str
    evidence: list[Evidence]


@dataclass
class FileProfile:
    path: str
    code: str
    language: str
    non_empty_lines: int
    comment_lines: int
    avg_line_length: float
    function_count: int
    control_count: int
    string_literal_count: int
    generic_comment_count: int
    ast: dict[str, Any] | None = None


AI_KEYWORDS = ("copilot", "chatgpt", "openai", "claude", "gemini")
AI_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, AI_KEYWORDS)) + r")(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)

GENERATION_PATTERNS = (
    re.compile(r"\bgenerated\s+by\b", re.IGNORECASE),
    re.compile(r"\bcreated\s+with\b", re.IGNORECASE),
    re.compile(r"\bwritten\s+by\b", re.IGNORECASE),
    re.compile(r"\bauthored\s+by\b", re.IGNORECASE),
    re.compile(r"\bproduced\s+by\b", re.IGNORECASE),
    re.compile(r"\bmade\s+with\b", re.IGNORECASE),
    re.compile(r"\busing\s+(?:chatgpt|copilot|claude|gemini|openai)\b", re.IGNORECASE),
)

COMMENT_TYPE_RE = re.compile(r"comment", re.IGNORECASE)
STRING_TYPE_RE = re.compile(r"string|string_literal|string_content", re.IGNORECASE)
IDENTIFIER_TYPE_RE = re.compile(r"identifier", re.IGNORECASE)
BRANCH_TYPES = {
    "if_statement", "if_element", "for_statement", "for_element", "while_statement",
    "switch_statement", "switch_expression", "conditional_expression", "catch_clause",
    "case_clause", "when_entry", "match_arm", "try_statement", "except_clause",
}
FUNCTION_TYPES = {
    "function_declaration", "method_declaration", "function_definition", "function_expression",
    "method_definition", "function_item", "arrow_function", "lambda", "anonymous_function",
    "function_literal", "constructor", "method_signature", "getter_signature", "setter_signature",
}


def language_for(path: str) -> str:
    ext = os.path.splitext(path.lower())[1]
    return {
        ".kt": "kotlin", ".java": "java", ".py": "python", ".js": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".cpp": "cpp", ".c": "c", ".h": "c", ".cs": "csharp",
        ".swift": "swift", ".dart": "dart",
    }.get(ext, "unknown")


def comment_pattern() -> re.Pattern[str]:
    return re.compile(r"^\s*(?://|#|/\*|\*|<!--)")


def function_pattern(language: str) -> re.Pattern[str]:
    if language in {"kotlin", "java", "swift", "dart"}:
        return re.compile(
            r"^\s*(?:(?:public|private|protected|internal|static|override|suspend|async)\s+)*"
            r"(?:fun|[A-Za-z_][\w<>?\[\]]*\s+[A-Za-z_]\w*)\s*\("
        )
    if language == "python":
        return re.compile(r"^\s*(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(")
    if language in {"javascript", "typescript"}:
        return re.compile(r"^\s*(?:(?:export|default|async|function)\s+)+[A-Za-z_$][\w$]*\s*(?:<[^>]+>)?\s*\(")
    if language == "go":
        return re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?[A-Za-z_]\w*\s*\(")
    return re.compile(r"^\s*(?:def|func|function)\s+[A-Za-z_]\w*\s*\(")


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(root: Any) -> Iterable[Any]:
    """Iterative traversal: no Python recursion on deeply nested source trees."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(getattr(node, "children", ())))


def _is_generation_string_node(node_type: str) -> bool:
    normalized = node_type.lower()
    return normalized == "string" or "string_literal" in normalized


def _is_ignored_provenance_context(node: Any) -> bool:
    node_type = str(getattr(node, "type", "")).lower()
    return bool(IDENTIFIER_TYPE_RE.search(node_type) or "string_content" in node_type)


def _is_generation_string(text: str) -> bool:
    return any(pattern.search(text) for pattern in GENERATION_PATTERNS)


def provenance_findings(root: Any, source: bytes) -> list[Finding]:
    """Apply the context filter before adding any provenance score."""
    findings: list[Finding] = []
    for node in _walk(root):
        node_type = str(getattr(node, "type", ""))
        node_type_lower = node_type.lower()
        text = _node_text(node, source)
        if not AI_KEYWORD_RE.search(text):
            continue

        is_comment = bool(COMMENT_TYPE_RE.search(node_type_lower))
        is_string_literal = _is_generation_string_node(node_type)
        is_string_content = "string_content" in node_type_lower
        is_identifier = bool(IDENTIFIER_TYPE_RE.search(node_type_lower))

        # Critical false-positive rule: normal strings, string content and
        # identifiers are not provenance evidence unless the string-literal
        # exception below is satisfied.
        if is_identifier or is_string_content:
            continue

        if is_comment:
            findings.append(Finding(
                type="provenance_comment",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                suspected_text=text.strip()[:500],
                weight=0.9,
                reason="AI-инструмент упомянут в комментарии; это сильный provenance-сигнал, который нужно проверить вручную.",
            ))
            continue

        if is_string_literal and _is_generation_string(text):
            findings.append(Finding(
                type="provenance_generation_string",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                suspected_text=text.strip()[:500],
                weight=0.9,
                reason="Строковый литерал содержит AI-бренд вместе с явным описанием генерации или авторства.",
            ))

    unique: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        unique[(finding.type, finding.line_start, finding.line_end, finding.suspected_text)] = finding
    return list(unique.values())


def _function_metrics(node: Any) -> dict[str, int]:
    branch_count = 0
    max_depth = 0
    parse_errors = 0
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if current.type == "ERROR":
            parse_errors += 1
        if current.type in BRANCH_TYPES:
            branch_count += 1
        stack.extend((child, depth + 1) for child in reversed(current.children))
    return {"branch_count": branch_count, "max_depth": max_depth, "parse_errors": parse_errors}


def _structure_signature(node: Any) -> str:
    """Hash AST structure while ignoring identifiers, literals and comments."""
    ignored = {
        "identifier", "type_identifier", "property_identifier", "field_identifier",
        "string", "string_literal", "string_content", "integer_literal", "float_literal",
        "comment", "line_comment", "block_comment",
    }
    types = [current.type for current in _walk(node) if current.type not in ignored]
    return hashlib.sha1("|".join(types).encode("utf-8"), usedforsecurity=False).hexdigest()


def _ast_metrics(code: str, language_name: str) -> dict[str, Any] | None:
    if Parser is None or get_language is None:
        return None
    try:
        language = get_language(language_name)
        parser = Parser(language)
        source = code.encode("utf-8")
        tree = parser.parse(source)
    except Exception:
        return None

    root = tree.root_node
    functions: list[dict[str, Any]] = []
    parse_errors = 0
    widget_constructors = 0

    for node in _walk(root):
        if node.type == "ERROR":
            parse_errors += 1
        if node.type in FUNCTION_TYPES or "function_declaration" in node.type:
            metrics = _function_metrics(node)
            functions.append({
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "length": max(1, node.end_point[0] - node.start_point[0] + 1),
                "branches": metrics["branch_count"],
                "depth": metrics["max_depth"],
                "signature": _structure_signature(node),
            })
        if language_name == "dart" and node.type == "constructor_invocation":
            text = _node_text(node, source)
            name = text.split("(", 1)[0].strip().split(".")[-1]
            if name and name[0].isupper():
                widget_constructors += 1

    signatures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for function in functions:
        if function["length"] >= 8:
            signatures[function["signature"]].append(function)

    return {
        "functions": functions,
        "long_functions": [fn for fn in functions if fn["length"] >= 45],
        "repeated_templates": [group for group in signatures.values() if len(group) >= 3],
        "widget_constructors": widget_constructors,
        "parse_errors": parse_errors,
    }


def dart_ast_metrics(code: str) -> dict[str, Any] | None:
    return _ast_metrics(code, "dart")


def build_profile(path: str, code: str) -> FileProfile:
    language = language_for(path)
    lines = code.splitlines()
    non_empty = [line for line in lines if line.strip()]
    comments = [line for line in non_empty if comment_pattern().search(line)]
    function_re = function_pattern(language)
    controls = sum(len(re.findall(r"\b(if|else|for|while|switch|when|try|catch|match)\b", line)) for line in non_empty)
    strings = sum(len(re.findall(r"(['\"]).*?\1", line)) for line in non_empty)
    generic_comments = sum(
        1 for line in comments
        if re.search(r"\b(this|the|function|method|class|widget|component|returns?|handles?|purpose|responsible for)\b", line, re.I)
    )
    ast = _ast_metrics(code, language) if language != "unknown" else None
    return FileProfile(
        path=path,
        code=code,
        language=language,
        non_empty_lines=len(non_empty),
        comment_lines=len(comments),
        avg_line_length=statistics.mean(len(line.rstrip()) for line in non_empty) if non_empty else 0.0,
        function_count=sum(1 for line in non_empty if function_re.search(line)),
        control_count=controls,
        string_literal_count=strings,
        generic_comment_count=generic_comments,
        ast=ast,
    )


def _normalise_function_body(lines: list[str]) -> str:
    text = "\n".join(lines)
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "STR", text)
    text = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", "ID", text)
    return re.sub(r"\s+", " ", text).strip()


def _function_blocks(profile: FileProfile) -> list[tuple[int, int, str]]:
    if not profile.ast:
        return []
    lines = profile.code.splitlines()
    blocks = []
    for fn in profile.ast["functions"]:
        start = max(1, fn["line"])
        end = min(len(lines), fn["end_line"])
        if end - start + 1 >= 8:
            blocks.append((start, end, _normalise_function_body(lines[start - 1:end])))
    return blocks


def analyze_file(file_content: str, language: str) -> FileAnalysisResult:
    """Parse and score one file; total_score is a review signal, not AI probability."""
    normalized_language = language.lower().lstrip(".")
    try:
        if Parser is None or get_language is None:
            return FileAnalysisResult(file_path="analysis", is_suspicious=False, total_score=0.0, findings=[])
        language_obj = get_language(normalized_language)
        parser = Parser(language_obj)
        source = file_content.encode("utf-8")
        tree = parser.parse(source)
    except Exception:
        return FileAnalysisResult(file_path="analysis", is_suspicious=False, total_score=0.0, findings=[])

    findings = provenance_findings(tree.root_node, source)
    ast = _ast_metrics(file_content, normalized_language)
    lines = file_content.splitlines()

    if ast:
        suspicious = [
            fn for fn in ast["functions"]
            if fn["length"] >= 45 and fn["depth"] >= 8 and fn["branches"] >= 8
        ]
        for fn in suspicious[:4]:
            start = fn["line"]
            end = min(fn["end_line"], start + 2)
            findings.append(Finding(
                type="structural_complexity",
                line_start=start,
                line_end=end,
                suspected_text="\n".join(lines[start - 1:end]).strip()[:500],
                weight=0.28,
                reason="AST: длинная функция одновременно имеет глубокую вложенность и много ветвлений.",
            ))

        # Repetition is structural evidence only. Common Flutter UI constructors
        # are explicitly excluded because repetition there is normal.
        for group in ast["repeated_templates"][:3]:
            representative = min(group, key=lambda fn: fn["line"])
            start = representative["line"]
            body = "\n".join(lines[start - 1:representative["end_line"]])
            if re.search(r"\b(InputDecoration|Container|Text|Row|Column|Padding|SizedBox|BorderRadius)\b", body):
                continue
            findings.append(Finding(
                type="repeated_function_template",
                line_start=start,
                line_end=representative["end_line"],
                suspected_text=body.strip()[:500],
                weight=0.12,
                reason="AST: несколько функций имеют одинаковый структурный шаблон после исключения имён и литералов; это слабый сигнал, а не доказательство AI.",
            ))

    unique: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        unique[(finding.type, finding.line_start, finding.line_end, finding.suspected_text)] = finding
    final_findings = sorted(unique.values(), key=lambda item: (-item.weight, item.line_start))[:12]

    # Diminishing returns prevent many similar findings from dominating the score.
    total_score = 0.0
    seen_types: set[str] = set()
    for finding in final_findings:
        multiplier = 1.0 if finding.type not in seen_types else 0.35
        total_score += finding.weight * multiplier
        seen_types.add(finding.type)
    total_score = min(1.0, round(total_score, 3))

    return FileAnalysisResult(
        file_path="analysis",
        is_suspicious=bool(final_findings),
        total_score=total_score,
        findings=final_findings,
    )


def evidence_for_code(profile: FileProfile, repo_profiles: list[FileProfile]) -> FindingData | None:
    """Backward-compatible adapter for the existing FastAPI response contract."""
    result = analyze_file(profile.code, profile.language)
    if not result.findings:
        return None
    evidence = [
        Evidence(line=item.line_start, text=item.suspected_text.replace("\n", " ")[:240], reason=item.reason)
        for item in result.findings[:8]
    ]
    line_numbers = sorted({item.line for item in evidence})
    line_range = str(line_numbers[0]) if len(line_numbers) == 1 else ", ".join(map(str, line_numbers))
    score = round(result.total_score * 100)
    reason = "; ".join(dict.fromkeys(item.reason for item in result.findings))
    return FindingData(profile.path, line_range, score, reason, evidence)
