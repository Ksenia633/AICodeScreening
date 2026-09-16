from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass
from typing import Any

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
except ImportError:  # Backend still starts, but AST mode is disabled until dependencies are installed.
    Parser = None
    get_language = None


@dataclass
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


def language_for(path: str) -> str:
    ext = os.path.splitext(path.lower())[1]
    return {
        ".kt": "kotlin", ".java": "java", ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".jsx": "javascript", ".go": "go", ".rs": "rust", ".cpp": "cpp",
        ".c": "c", ".h": "c", ".cs": "csharp", ".swift": "swift", ".dart": "dart",
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


def walk(root: Any):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def dart_ast_metrics(code: str) -> dict[str, Any] | None:
    """Extract structural Dart/Flutter features with a real syntax tree."""
    if Parser is None or get_language is None:
        return None
    try:
        language = get_language("dart")
        parser = Parser(language)
        tree = parser.parse(code.encode("utf-8"))
        root = tree.root_node
    except Exception:
        return None

    source = code.encode("utf-8")
    function_types = {
        "function_declaration", "method_declaration", "function_expression",
        "getter_signature", "setter_signature", "method_signature",
    }
    branch_types = {
        "if_statement", "if_element", "for_statement", "for_element",
        "while_statement", "switch_statement", "switch_expression",
        "conditional_expression", "catch_clause", "case_clause",
    }

    functions: list[dict[str, int]] = []
    branch_count = 0
    max_depth = 0
    widget_constructors = 0
    parse_errors = 0

    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if node.type == "ERROR":
            parse_errors += 1
        if node.type in branch_types:
            branch_count += 1
        if node.type in function_types:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            functions.append({"line": start_line, "end_line": end_line, "length": max(1, end_line - start_line + 1)})
        if node.type == "constructor_invocation":
            try:
                text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                name = text.split("(", 1)[0].strip().split(".")[-1]
                if name and name[0].isupper():
                    widget_constructors += 1
            except Exception:
                pass
        for child in reversed(node.children):
            stack.append((child, depth + 1))

    return {
        "functions": functions,
        "long_functions": [f for f in functions if f["length"] >= 45],
        "branch_count": branch_count,
        "max_depth": max_depth,
        "widget_constructors": widget_constructors,
        "parse_errors": parse_errors,
    }


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
    ast = dart_ast_metrics(code) if language == "dart" else None
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
    """Find combinations that deserve human review.

    Repeated UI constructs are intentionally ignored. AST complexity is contextual evidence,
    never a standalone AI verdict.
    """
    lines = profile.code.splitlines()
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

    # Strong provenance markers. These are the only single-line signals allowed to be strong.
    ai_marker = re.compile(r"generated\s+by|chatgpt|copilot|github\s+copilot|ai[- ]generated|openai|gemini|claude", re.I)
    for line_no, line in non_empty:
        if ai_marker.search(line):
            add(line_no, line, 70, "явный маркер AI-инструмента или сгенерированного кода")

    # Weak template phrases: useful only in combination with other independent signals.
    template_marker = re.compile(r"TODO|FIXME|PLACEHOLDER|IMPLEMENT\s+HERE|YOUR\s+CODE|ADD\s+YOUR", re.I)
    for line_no, line in non_empty:
        if template_marker.search(line):
            add(line_no, line, 3, "шаблонный маркер незавершённого участка")

    # Generic explanatory prose is more interesting than normal UI repetition, but still weak.
    generic_comment = re.compile(
        r"^\s*(?://|#|/\*|\*)\s*(this (function|method|class|widget|component)|the (function|method|purpose)|returns?\s+the|handles?\s+the|responsible for)",
        re.I,
    )
    generic_hits = []
    for line_no, line in non_empty:
        if generic_comment.search(line):
            generic_hits.append((line_no, line))
            add(line_no, line, 3, "избыточный шаблонный комментарий")
    if len(generic_hits) >= 4:
        score += 3
        reasons.append("много однотипных поясняющих комментариев")

    # AST signal for Dart: only the combination of long function + deep nesting + many branches.
    # Widget constructor count is never suspicious by itself.
    ast = profile.ast
    if ast:
        if ast["long_functions"] and ast["max_depth"] >= 7 and ast["branch_count"] >= 8:
            fn = max(ast["long_functions"], key=lambda x: x["length"])
            line = lines[fn["line"] - 1] if fn["line"] <= len(lines) else ""
            add(fn["line"], line, 6, "AST: длинная функция с глубокой вложенностью и большим числом ветвлений")
        if ast["widget_constructors"] >= 35 and ast["long_functions"]:
            fn = max(ast["long_functions"], key=lambda x: x["length"])
            line = lines[fn["line"] - 1] if fn["line"] <= len(lines) else ""
            add(fn["line"], line, 3, "AST: очень большой Flutter widget tree внутри длинного метода")

    # Generic complexity signal for non-Dart and fallback cases.
    function_re = function_pattern(profile.language)
    control_re = re.compile(r"\b(if|else|for|while|switch|when|try|catch|match)\b")
    for line_no, line in non_empty:
        if function_re.search(line):
            window = lines[line_no - 1:min(len(lines), line_no + 60)]
            controls = len(control_re.findall("\n".join(window)))
            if controls >= 7 and len(window) >= 30:
                add(line_no, line, 4, "функция длинная и насыщена управляющей логикой")

    # Repository-relative style outlier, deliberately low weight.
    same_language = [p for p in repo_profiles if p.language == profile.language and p.non_empty_lines >= 20]
    if len(same_language) >= 4:
        median_len = statistics.median(p.avg_line_length for p in same_language)
        median_comments = statistics.median(p.comment_lines / max(1, p.non_empty_lines) for p in same_language)
        comment_ratio = profile.comment_lines / max(1, profile.non_empty_lines)
        if median_len > 0 and profile.avg_line_length > median_len * 1.7:
            longest = sorted(non_empty, key=lambda x: len(x[1]), reverse=True)[:2]
            for line_no, line in longest:
                add(line_no, line, 1, "строка заметно выбивается по стилю от файлов того же языка")
            reasons.append("стиль файла отличается от основной массы файлов репозитория")
        if median_comments == 0 and comment_ratio > 0.20:
            score += 2
            reasons.append("необычно высокая доля поясняющих комментариев")

    # Comment-heavy files become relevant only when generic comments are also present.
    if profile.non_empty_lines >= 80 and profile.comment_lines / profile.non_empty_lines >= 0.28 and len(generic_hits) >= 3:
        score += 4
        reasons.append("высокая доля комментариев сочетается с шаблонными пояснениями")

    strong = any(item.reason.startswith("явный маркер") for item in evidence)
    independent = len(set(reasons))
    if not strong and independent < 2:
        return None

    score = min(score, 95)
    unique: dict[int, Evidence] = {}
    for item in evidence:
        if item.line not in unique or item.reason.startswith("явный маркер"):
            unique[item.line] = item
    final_evidence = list(unique.values())[:8]
    if not final_evidence:
        return None

    line_numbers = sorted({item.line for item in final_evidence})
    line_range = str(line_numbers[0]) if len(line_numbers) == 1 else ", ".join(map(str, line_numbers))
    return FindingData(
        file=profile.path,
        lines=line_range,
        score=score,
        reason="; ".join(reasons),
        evidence=final_evidence,
    )
