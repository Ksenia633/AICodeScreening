from __future__ import annotations

import hashlib
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
except ImportError:  # pragma: no cover
    Parser = None
    get_language = None


# ---------------------------------------------------------------------------
# API models (Pydantic v2)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Internal compatibility models used by the existing FastAPI endpoint.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Context-aware provenance
# ---------------------------------------------------------------------------

AI_KEYWORDS = ("copilot", "chatgpt", "openai", "claude", "gemini")
AI_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, AI_KEYWORDS) + ())(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)

# These patterns are deliberately much narrower than just an AI brand name.
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
IDENTIFIER_TYPE_RE = re.compile(r"identifier", re.IGNORECASE)
STRING_TYPE_RE = re.compile(r"string|string_literal|string_content", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Language / AST helpers
# ---------------------------------------------------------------------------

FUNCTION_TYPES = {
    "function_declaration", "method_declaration", "function_definition",
    "function_expression", "method_definition", "function_item", "arrow_function",
    "lambda", "anonymous_function", "function_literal", "constructor",
    "method_signature", "getter_signature", "setter_signature",
}

BRANCH_TYPES = {
    "if_statement", "if_element", "for_statement", "for_element", "while_statement",
    "do_statement", "switch_statement", "switch_expression", "conditional_expression",
    "catch_clause", "case_clause", "when_entry", "match_arm", "try_statement",
    "except_clause", "else_clause",
}

LOOP_TYPES = {"for_statement", "for_element", "while_statement", "do_statement"}

UI_IDENTIFIERS = {
    "InputDecoration", "Container", "Text", "Row", "Column", "Padding", "SizedBox",
    "BorderRadius", "Center", "Expanded", "Flexible", "Stack", "Positioned", "Card",
    "Scaffold", "AppBar", "ListView", "GridView", "SafeArea", "GestureDetector",
    "FutureBuilder", "StreamBuilder", "Builder", "Theme", "Icon", "Image", "Spacer",
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
        return re.compile(
            r"^\s*(?:(?:export|default|async|function)\s+)+[A-Za-z_$][\w$]*"
            r"\s*(?:<[^>]+>)?\s*\("
        )
    if language == "go":
        return re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?[A-Za-z_]\w*\s*\(")
    return re.compile(r"^\s*(?:def|func|function)\s+[A-Za-z_]\w*\s*\(")


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(root: Any) -> Iterable[Any]:
    """Iterative Tree-sitter traversal; avoids Python recursion depth issues."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(getattr(node, "children", ())))


def _descendants(node: Any, predicate: Any, limit: int = 8000) -> int:
    count = 0
    stack = list(reversed(getattr(node, "children", ())))
    visited = 0
    while stack and visited < limit:
        current = stack.pop()
        visited += 1
        if predicate(current):
            count += 1
        stack.extend(reversed(getattr(current, "children", ())))
    return count


def _function_metrics(node: Any) -> dict[str, int]:
    branches = 0
    loops = 0
    max_depth = 0
    parse_errors = 0
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if current.type == "ERROR":
            parse_errors += 1
        if current.type in BRANCH_TYPES:
            branches += 1
        if current.type in LOOP_TYPES:
            loops += 1
        stack.extend((child, depth + 1) for child in reversed(getattr(current, "children", ())))
    return {
        "branches": branches,
        "loops": loops,
        "depth": max_depth,
        "parse_errors": parse_errors,
    }


def _structure_signature(node: Any) -> str:
    """Structural fingerprint with names/literals/comments removed."""
    ignored = {
        "identifier", "type_identifier", "property_identifier", "field_identifier",
        "string", "string_literal", "string_content", "integer_literal", "float_literal",
        "comment", "line_comment", "block_comment", "template_string",
    }
    types = [current.type for current in _walk(node) if current.type not in ignored]
    return hashlib.sha1("|".join(types).encode("utf-8"), usedforsecurity=False).hexdigest()


def _is_ui_function(node: Any, source: bytes) -> bool:
    text = _node_text(node, source)
    # Flutter build methods and functions dominated by standard widget constructors
    # are normal application code and should not be flagged for structural repetition.
    if re.search(r"\bbuild\s*\(", text):
        return True
    return sum(1 for name in UI_IDENTIFIERS if re.search(rf"\b{re.escape(name)}\b", text)) >= 3


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
    ui_constructors = 0

    for node in _walk(root):
        if node.type == "ERROR":
            parse_errors += 1
        if node.type in FUNCTION_TYPES or "function_declaration" in node.type:
            metrics = _function_metrics(node)
            functions.append({
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "length": max(1, node.end_point[0] - node.start_point[0] + 1),
                "branches": metrics["branches"],
                "loops": metrics["loops"],
                "depth": metrics["depth"],
                "parse_errors": metrics["parse_errors"],
                "signature": _structure_signature(node),
                "is_ui": _is_ui_function(node, source),
            })
        if language_name == "dart" and node.type == "constructor_invocation":
            text = _node_text(node, source)
            name = text.split("(", 1)[0].strip().split(".")[-1]
            if name in UI_IDENTIFIERS:
                ui_constructors += 1

    signatures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for function in functions:
        if function["length"] >= 8 and not function["is_ui"]:
            signatures[function["signature"]].append(function)

    return {
        "functions": functions,
        "long_functions": [f for f in functions if f["length"] >= 45],
        "repeated_templates": [group for group in signatures.values() if len(group) >= 3],
        "ui_constructors": ui_constructors,
        "parse_errors": parse_errors,
    }


def dart_ast_metrics(code: str) -> dict[str, Any] | None:
    return _ast_metrics(code, "dart")


# ---------------------------------------------------------------------------
# Provenance filter
# ---------------------------------------------------------------------------


def _is_generation_string_node(node_type: str) -> bool:
    normalized = node_type.lower()
    return normalized == "string" or "string_literal" in normalized


def _is_generation_string(text: str) -> bool:
    return any(pattern.search(text) for pattern in GENERATION_PATTERNS)


def provenance_findings(root: Any, source: bytes) -> list[Finding]:
    """Find AI mentions only when their AST context supports provenance evidence."""
    findings: list[Finding] = []

    for node in _walk(root):
        node_type = str(getattr(node, "type", ""))
        node_type_lower = node_type.lower()
        text = _node_text(node, source)
        if not AI_KEYWORD_RE.search(text):
            continue

        is_identifier = bool(IDENTIFIER_TYPE_RE.search(node_type_lower))
        is_string_content = "string_content" in node_type_lower
        is_comment = bool(COMMENT_TYPE_RE.search(node_type_lower))
        is_string_literal = _is_generation_string_node(node_type)

        # Required false-positive rule: identifiers and string content are ignored.
        if is_identifier or is_string_content:
            continue

        # A plain string such as "OpenAI integration" is not provenance evidence.
        if is_string_literal:
            if not _is_generation_string(text):
                continue
            findings.append(Finding(
                type="provenance_generation_string",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                suspected_text=text.strip()[:500],
                weight=0.9,
                reason="Строковый литерал содержит AI-бренд вместе с явным описанием генерации или авторства.",
            ))
            continue

        # Comments are intentionally strong because they are direct provenance claims.
        if is_comment:
            findings.append(Finding(
                type="provenance_comment",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                suspected_text=text.strip()[:500],
                weight=0.9,
                reason="AI-инструмент упомянут в комментарии; это сильный provenance-сигнал, требующий ручной проверки.",
            ))

    unique: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        key = (finding.type, finding.line_start, finding.line_end, finding.suspected_text)
        unique[key] = finding
    return list(unique.values())


# ---------------------------------------------------------------------------
# Stylometry / structural scoring
# ---------------------------------------------------------------------------


def _tokenise_code(code: str) -> list[str]:
    # Lightweight lexical representation: identifiers become ID, literals become STR/NUM.
    code = re.sub(r"//.*", " ", code)
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    code = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", " STR ", code, flags=re.S)
    code = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", code)
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|==|!=|<=|>=|=>|&&|\|\||[{}()\[\];,.?:+*/%<>!=\-]", code)


def _lexical_entropy(code: str) -> float:
    tokens = _tokenise_code(code)
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    total = len(tokens)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _identifier_consistency(code: str) -> float:
    identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code)
    identifiers = [x for x in identifiers if x not in {"if", "else", "for", "while", "return", "class", "fun", "def", "var", "final", "const"}]
    if len(identifiers) < 10:
        return 0.0
    good = sum(bool(re.fullmatch(r"(?:[a-z][A-Za-z0-9]*|[A-Z][A-Za-z0-9]*|_[A-Za-z0-9_]+)", x)) for x in identifiers)
    return good / len(identifiers)


def _line_length_uniformity(code: str) -> float:
    lengths = [len(line.strip()) for line in code.splitlines() if line.strip()]
    if len(lengths) < 10:
        return 0.0
    mean = statistics.mean(lengths)
    if mean == 0:
        return 0.0
    cv = statistics.pstdev(lengths) / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _comment_uniformity(code: str) -> float:
    comments = [line.strip() for line in code.splitlines() if comment_pattern().search(line) and line.strip()]
    if len(comments) < 4:
        return 0.0
    lengths = [len(x) for x in comments]
    mean = statistics.mean(lengths)
    if mean == 0:
        return 0.0
    cv = statistics.pstdev(lengths) / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _repository_outlier(profile: FileProfile, repo_profiles: list[FileProfile]) -> float:
    peers = [p for p in repo_profiles if p.language == profile.language and p.non_empty_lines >= 20]
    if len(peers) < 4:
        return 0.0
    median_len = statistics.median(p.avg_line_length for p in peers)
    median_comments = statistics.median(p.comment_lines / max(1, p.non_empty_lines) for p in peers)
    line_factor = min(1.0, max(0.0, (profile.avg_line_length / max(1.0, median_len) - 1.0) / 1.5))
    comment_ratio = profile.comment_lines / max(1, profile.non_empty_lines)
    comment_factor = min(1.0, max(0.0, (comment_ratio - median_comments) / 0.35))
    return 0.5 * line_factor + 0.5 * comment_factor


def _function_structure_score(ast: dict[str, Any]) -> tuple[float, dict[str, Any] | None]:
    candidates = [
        fn for fn in ast.get("functions", [])
        if fn["length"] >= 35 and not fn.get("is_ui", False)
    ]
    if not candidates:
        return 0.0, None
    best = max(candidates, key=lambda f: (
        f["branches"] * 2 + f["loops"] + f["depth"] + f["length"] / 20
    ))
    # These are deliberately smooth and capped: complexity is evidence, not proof.
    length = min(1.0, max(0.0, (best["length"] - 35) / 100))
    branches = min(1.0, best["branches"] / 12)
    depth = min(1.0, max(0.0, (best["depth"] - 5) / 12))
    loops = min(1.0, best["loops"] / 4)
    score = 0.35 * length + 0.30 * branches + 0.25 * depth + 0.10 * loops
    return score, best


def _repeated_structure_score(ast: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    groups = ast.get("repeated_templates", [])
    if not groups:
        return 0.0, []
    # Three or more structurally identical non-UI functions is unusual; ordinary
    # Flutter widget trees were excluded before this stage.
    largest = max(groups, key=len)
    if len(largest) < 3:
        return 0.0, []
    excess = min(1.0, (len(largest) - 2) / 4)
    return 0.30 + 0.45 * excess, largest


def _build_findings(profile: FileProfile, repo_profiles: list[FileProfile]) -> list[Finding]:
    findings: list[Finding] = []
    source = profile.code.encode("utf-8")

    # Provenance is handled first and independently from style/structure.
    if profile.ast:
        findings.extend(provenance_findings_from_profile(profile))

    ast = profile.ast
    if ast:
        structure_score, fn = _function_structure_score(ast)
        if structure_score >= 0.42 and fn:
            lines = profile.code.splitlines()
            start = fn["line"]
            end = min(fn["end_line"], start + 2)
            findings.append(Finding(
                type="structural_complexity",
                line_start=start,
                line_end=end,
                suspected_text="\n".join(lines[start - 1:end]).strip()[:500],
                weight=round(min(0.65, structure_score * 0.65), 3),
                reason="AST: функция заметно сложная по сочетанию длины, вложенности и управляющей логики; сама по себе сложность не доказывает использование AI.",
            ))

        repeated_score, group = _repeated_structure_score(ast)
        if repeated_score >= 0.45 and group:
            lines = profile.code.splitlines()
            for fn in group[:3]:
                findings.append(Finding(
                    type="structural_template",
                    line_start=fn["line"],
                    line_end=min(fn["end_line"], fn["line"] + 1),
                    suspected_text="\n".join(lines[fn["line"] - 1:min(fn["end_line"], fn["line"] + 1)]).strip()[:500],
                    weight=round(min(0.5, repeated_score), 3),
                    reason="AST: несколько нетипичных функций имеют одинаковый структурный скелет после исключения имён, литералов и UI-кода.",
                ))

    # Stylometry is intentionally low-weight and repository-relative.
    if profile.non_empty_lines >= 30:
        uniformity = _line_length_uniformity(profile.code)
        identifier_consistency = _identifier_consistency(profile.code)
        comment_uniformity = _comment_uniformity(profile.code)
        outlier = _repository_outlier(profile, repo_profiles)
        style_score = (
            0.35 * uniformity
            + 0.25 * identifier_consistency
            + 0.15 * comment_uniformity
            + 0.25 * outlier
        )
        # Style alone never creates a finding. It is emitted only as a supporting factor.
        if style_score >= 0.72 and outlier >= 0.25:
            longest = max(enumerate(profile.code.splitlines(), start=1), key=lambda item: len(item[1]), default=(1, ""))
            findings.append(Finding(
                type="stylometry_outlier",
                line_start=longest[0],
                line_end=longest[0],
                suspected_text=longest[1].strip()[:500],
                weight=round(min(0.32, style_score * 0.32), 3),
                reason="Стиль файла заметно отличается от одноязычных файлов этого репозитория и одновременно показывает высокую регулярность; это только дополнительный сигнал.",
            ))

    return _dedupe_findings(findings)


def provenance_findings_from_profile(profile: FileProfile) -> list[Finding]:
    if not profile.ast or Parser is None or get_language is None:
        return []
    try:
        language = get_language(profile.language)
        parser = Parser(language)
        source = profile.code.encode("utf-8")
        tree = parser.parse(source)
        return provenance_findings(tree.root_node, source)
    except Exception:
        return []


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    unique: dict[tuple[str, int, int, str], Finding] = {}
    for finding in findings:
        key = (finding.type, finding.line_start, finding.line_end, finding.suspected_text)
        unique[key] = finding
    return sorted(unique.values(), key=lambda item: item.weight, reverse=True)


# ---------------------------------------------------------------------------
# Public single-file API requested by the backend/tests.
# ---------------------------------------------------------------------------


def analyze_file(file_content: str, language: str) -> FileAnalysisResult:
    """Parse one source file and return a normalized 0..1 review signal."""
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

    profile = build_profile(f"analysis.{normalized_language}", file_content)
    findings = provenance_findings(tree.root_node, source)

    # Add structural findings without allowing filtered provenance to leak into score.
    ast = profile.ast
    if ast:
        structure_score, fn = _function_structure_score(ast)
        if structure_score >= 0.42 and fn:
            lines = file_content.splitlines()
            start = fn["line"]
            end = min(fn["end_line"], start + 2)
            findings.append(Finding(
                type="structural_complexity",
                line_start=start,
                line_end=end,
                suspected_text="\n".join(lines[start - 1:end]).strip()[:500],
                weight=round(min(0.65, structure_score * 0.65), 3),
                reason="AST: функция сложная по сочетанию длины, вложенности и управляющей логики; это не самостоятельное доказательство AI.",
            ))
        repeated_score, group = _repeated_structure_score(ast)
        if repeated_score >= 0.45 and group:
            lines = file_content.splitlines()
            for fn in group[:3]:
                findings.append(Finding(
                    type="structural_template",
                    line_start=fn["line"],
                    line_end=min(fn["end_line"], fn["line"] + 1),
                    suspected_text="\n".join(lines[fn["line"] - 1:min(fn["end_line"], fn["line"] + 1)]).strip()[:500],
                    weight=round(min(0.5, repeated_score), 3),
                    reason="AST: несколько нетипичных функций имеют одинаковый структурный скелет.",
                ))

    findings = _dedupe_findings(findings)
    # Ensemble-like aggregation: independent signals contribute, with diminishing returns.
    weights = sorted((finding.weight for finding in findings), reverse=True)
    total_score = 0.0
    remaining = 1.0
    for weight in weights:
        contribution = weight * remaining
        total_score += contribution
        remaining *= 0.55
    total_score = min(1.0, total_score)
    suspicious = total_score >= 0.30
    return FileAnalysisResult(
        file_path="analysis",
        is_suspicious=suspicious,
        total_score=round(total_score, 4),
        findings=findings[:12],
    )


# ---------------------------------------------------------------------------
# Existing FastAPI compatibility API.
# ---------------------------------------------------------------------------


def build_profile(path: str, code: str) -> FileProfile:
    language = language_for(path)
    lines = code.splitlines()
    non_empty = [line for line in lines if line.strip()]
    comments = [line for line in non_empty if comment_pattern().search(line)]
    function_re = function_pattern(language)
    controls = sum(
        len(re.findall(r"\b(if|else|for|while|switch|when|try|catch|match)\b", line))
        for line in non_empty
    )
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


def evidence_for_code(profile: FileProfile, repo_profiles: list[FileProfile]) -> FindingData | None:
    """Compatibility adapter for main.py; returns legacy line evidence.

    The score is a 0..100 review signal, not a calibrated probability.
    """
    findings = _build_findings(profile, repo_profiles)
    if not findings:
        return None

    # A provenance signal can stand alone. Structural/style signals require at
    # least two independent categories before the legacy endpoint emits a finding.
    types = {finding.type for finding in findings}
    has_provenance = any(item.startswith("provenance_") for item in types)
    if not has_provenance and len(types) < 2:
        return None

    selected = findings[:8]
    combined = 0.0
    remaining = 1.0
    for finding in selected:
        combined += finding.weight * remaining
        remaining *= 0.55
    score = round(min(95.0, combined * 100.0))

    evidence = [
        Evidence(
            line=finding.line_start,
            text=finding.suspected_text.replace("\n", " ")[:240],
            reason=finding.reason,
        )
        for finding in selected
    ]
    line_numbers = sorted({finding.line_start for finding in selected})
    lines = str(line_numbers[0]) if len(line_numbers) == 1 else ", ".join(map(str, line_numbers))
    reason = "; ".join(dict.fromkeys(f.reason for f in selected))
    return FindingData(
        file=profile.path,
        lines=lines,
        score=score,
        reason=reason,
        evidence=evidence,
    )
