from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass
from typing import Any

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
except ImportError:
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


def _node_depth(node: Any, limit: int = 2000) -> int:
    max_depth = 0
    stack = [(node, 0)]
    visited = 0
    while stack and visited < limit:
        current, depth = stack.pop()
        visited += 1
        max_depth = max(max_depth, depth)
        stack.extend((child, depth + 1) for child in reversed(current.children))
    return max_depth


def _descendant_count(node: Any, wanted: set[str], limit: int = 4000) -> int:
    count = 0
    stack = [node]
    visited = 0
    while stack and visited < limit:
        current = stack.pop()
        visited += 1
        if current.type in wanted:
            count += 1
        stack.extend(reversed(current.children))
    return count


def dart_ast_metrics(code: str) -> dict[str, Any] | None:
    """Extract function-local Dart structure; normal repeated Flutter widgets are ignored."""
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
    widget_constructors = 0
    parse_errors = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR":
            parse_errors += 1
        if node.type in function_types:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            functions.append({
                "line": start_line,
                "end_line": end_line,
                "length": max(1, end_line - start_line + 1),
                "depth": _node_depth(node),
                "branches": _descendant_count(node, branch_types),
            })
        if node.type == "constructor_invocation":
            try:
                text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                name = text.split("(", 1)[0].strip().split(".")[-1]
                if name and name[0].isupper():
                    widget_constructors += 1
            except Exception:
                pass
        stack.extend(reversed(node.children))

    return {
        "functions": functions,
        "long_functions": [f for f in functions if f["length"] >= 45],
        "branch_count": sum(f["branches"] for f in functions),
        "max_depth": max((f["depth"] for f in functions), default=0),
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


def _normalise_function_body(lines: list[str]) -> str:
    """Remove identifiers/literals so only unusually identical function skeletons remain."""
    text = "\n".join(lines)
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "STR", text)
    text = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", "ID", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _function_blocks(profile: FileProfile) -> list[tuple[int, int, str]]:
    if not profile.ast:
        return []
    blocks = []
    lines = profile.code.splitlines()
    for fn in profile.ast["functions"]:
        start = max(1, fn["line"])
        end = min(len(lines), fn["end_line"])
        if end - start + 1 >= 8:
            blocks.append((start, end, _normalise_function_body(lines[start - 1:end])))
    return blocks


def evidence_for_code(profile: FileProfile, repo_profiles: list[FileProfile]) -> FindingData | None:
    """Return only multi-signal evidence that is worth discussing with a candidate.

    Repeated InputDecoration/Container/Text/Row/Column and similar UI constructs are
    explicitly not treated as AI evidence. No score here is a calibrated probability.
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

    # Explicit provenance is strong, but still displayed as evidence rather than proof.
    ai_marker = re.compile(r"generated\s+by|chatgpt|copilot|github\s+copilot|ai[- ]generated|openai|gemini|claude", re.I)
    for line_no, line in non_empty:
        if ai_marker.search(line):
            add(line_no, line, 70, "явный маркер AI-инструмента или сгенерированного кода")

    # Template markers are intentionally weak.
    template_marker = re.compile(r"TODO|FIXME|PLACEHOLDER|IMPLEMENT\s+HERE|YOUR\s+CODE|ADD\s+YOUR", re.I)
    for line_no, line in non_empty:
        if template_marker.search(line):
            add(line_no, line, 2, "шаблонный маркер незавершённого участка")

    # Polished explanatory comments: weak on their own, stronger when repeated with other signals.
    generic_comment = re.compile(
        r"^\s*(?://|#|/\*|\*)\s*(this (function|method|class|widget|component)|the (function|method|purpose)|returns?\s+the|handles?\s+the|responsible for)",
        re.I,
    )
    generic_hits = []
    for line_no, line in non_empty:
        if generic_comment.search(line):
            generic_hits.append((line_no, line))
    if generic_hits:
        for line_no, line in generic_hits[:4]:
            add(line_no, line, 2, "избыточный шаблонный комментарий")
        if len(generic_hits) >= 4:
            score += 3
            reasons.append("много однотипных поясняющих комментариев")

    ast = profile.ast
    if ast:
        # Function-local, not file-global, complexity avoids false positives from large Flutter trees.
        suspicious_functions = [
            fn for fn in ast["functions"]
            if fn["length"] >= 45 and fn["depth"] >= 8 and fn["branches"] >= 8
        ]
        if suspicious_functions:
            fn = max(suspicious_functions, key=lambda x: (x["branches"], x["length"], x["depth"]))
            line = lines[fn["line"] - 1] if fn["line"] <= len(lines) else ""
            add(fn["line"], line, 6, "AST: одна функция одновременно длинная, глубоко вложенная и содержит много ветвлений")

        # A huge widget tree is normal in Flutter, so it is only a tiny contextual signal.
        if ast["widget_constructors"] >= 35 and ast["long_functions"] and profile.comment_lines >= 6:
            fn = max(ast["long_functions"], key=lambda x: x["length"])
            line = lines[fn["line"] - 1] if fn["line"] <= len(lines) else ""
            add(fn["line"], line, 2, "AST: большой widget tree сочетается с нетипично подробными комментариями")

    # Repeated function skeletons are considered only outside obvious UI constructors.
    blocks = _function_blocks(profile)
    skeletons: dict[str, list[int]] = {}
    for start, end, skeleton in blocks:
        if len(skeleton) < 80:
            continue
        if re.search(r"InputDecoration|Container|Text|Row|Column|Padding|SizedBox|BorderRadius", skeleton):
            continue
        skeletons.setdefault(skeleton, []).append(start)
    repeated = [starts for starts in skeletons.values() if len(starts) >= 2]
    if repeated:
        starts = repeated[0][:3]
        for line_no in starts:
            add(line_no, lines[line_no - 1], 2, "одинаковый нетипичный шаблон нескольких функций")

    # Repository-relative style outliers are deliberately tiny signals.
    same_language = [p for p in repo_profiles if p.language == profile.language and p.non_empty_lines >= 20]
    if len(same_language) >= 4:
        median_len = statistics.median(p.avg_line_length for p in same_language)
        median_comments = statistics.median(p.comment_lines / max(1, p.non_empty_lines) for p in same_language)
        comment_ratio = profile.comment_lines / max(1, profile.non_empty_lines)
        if median_len > 0 and profile.avg_line_length > median_len * 1.8:
            longest = sorted(non_empty, key=lambda x: len(x[1]), reverse=True)[:2]
            for line_no, line in longest:
                add(line_no, line, 1, "строка заметно выбивается по стилю от файлов того же языка")
        if median_comments == 0 and comment_ratio > 0.20:
            score += 1
            reasons.append("необычно высокая доля поясняющих комментариев")

    # Never flag a file merely because it is large, repetitive, or full of UI widgets.
    independent = len(set(reasons))
    strong = any(item.reason.startswith("явный маркер") for item in evidence)
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
