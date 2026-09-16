from __future__ import annotations

import math
import statistics

from detector import FileAnalysisResult, FileProfile, Finding, provenance_findings_from_profile


def _line_regularity(code: str) -> float:
    values = [len(x.strip()) for x in code.splitlines() if x.strip()]
    if len(values) < 12:
        return 0.0
    mean = statistics.mean(values)
    return max(0.0, min(1.0, 1.0 - statistics.pstdev(values) / max(mean, 1.0)))


def _identifier_regularity(code: str) -> float:
    words = []
    for token in code.replace("_", " ").split():
        token = "".join(ch for ch in token if ch.isalnum())
        if 3 <= len(token) <= 32 and token.isidentifier():
            words.append(token)
    if len(words) < 25:
        return 0.0
    camel = sum(bool(any(ch.isupper() for ch in x[1:])) for x in words) / len(words)
    return min(1.0, camel)


def _repo_outlier(profile: FileProfile, peers: list[FileProfile]) -> float:
    same = [p for p in peers if p.language == profile.language and p.non_empty_lines >= 20]
    if len(same) < 4:
        return 0.0
    median_len = statistics.median(p.avg_line_length for p in same)
    median_comment_ratio = statistics.median(
        p.comment_lines / max(1, p.non_empty_lines) for p in same
    )
    length_delta = abs(profile.avg_line_length - median_len) / max(median_len, 1.0)
    comment_ratio = profile.comment_lines / max(1, profile.non_empty_lines)
    comment_delta = abs(comment_ratio - median_comment_ratio)
    return min(1.0, 0.65 * length_delta + 0.35 * min(1.0, comment_delta * 3.0))


def _function_signal(profile: FileProfile) -> tuple[float, dict | None]:
    if not profile.ast:
        return 0.0, None
    functions = [
        f for f in profile.ast.get("functions", [])
        if f.get("length", 0) >= 25 and not f.get("is_ui", False)
    ]
    if not functions:
        return 0.0, None
    fn = max(
        functions,
        key=lambda f: f["length"] + f["branches"] * 4 + f["loops"] * 3 + f["depth"] * 2,
    )
    length = min(1.0, max(0.0, (fn["length"] - 25) / 120))
    branches = min(1.0, fn["branches"] / 10)
    depth = min(1.0, max(0.0, (fn["depth"] - 5) / 12))
    loops = min(1.0, fn["loops"] / 4)
    return 0.30 * length + 0.30 * branches + 0.25 * depth + 0.15 * loops, fn


def _structural_repetition(profile: FileProfile) -> tuple[float, list[dict]]:
    groups = profile.ast.get("repeated_templates", []) if profile.ast else []
    if not groups:
        return 0.0, []
    group = max(groups, key=len)
    return min(1.0, max(0.0, (len(group) - 2) / 5)), group


def _aggregate(signals: list[float]) -> float:
    result = 0.0
    remaining = 1.0
    for signal in sorted((max(0.0, min(1.0, x)) for x in signals), reverse=True):
        result += signal * remaining
        remaining *= 0.58
    return min(1.0, result)


def screen_profile(profile: FileProfile, peers: list[FileProfile]) -> FileAnalysisResult:
    """Repository-aware hybrid signal. This is not a probability of AI authorship."""
    findings = list(provenance_findings_from_profile(profile))
    complexity, function = _function_signal(profile)
    repetition, repeated = _structural_repetition(profile)
    outlier = _repo_outlier(profile, peers)
    regularity = _line_regularity(profile.code)
    identifier_style = _identifier_regularity(profile.code)

    ui_functions = sum(1 for f in (profile.ast or {}).get("functions", []) if f.get("is_ui"))
    total_functions = max(1, len((profile.ast or {}).get("functions", [])))
    ui_ratio = ui_functions / total_functions

    if complexity >= 0.50 and function:
        start = function["line"]
        lines = profile.code.splitlines()
        findings.append(Finding(
            type="structural_complexity",
            line_start=start,
            line_end=min(function["end_line"], start + 2),
            suspected_text="\n".join(lines[start - 1:min(function["end_line"], start + 2)])[:500],
            weight=round(min(0.28, complexity * 0.32), 3),
            reason="Сложная функция: сочетание длины, вложенности и ветвлений. Это повод задать вопрос, но не доказательство AI.",
        ))

    if repetition >= 0.60 and ui_ratio < 0.50:
        for function_item in repeated[:3]:
            start = function_item["line"]
            lines = profile.code.splitlines()
            findings.append(Finding(
                type="structural_template",
                line_start=start,
                line_end=min(function_item["end_line"], start + 1),
                suspected_text="\n".join(lines[start - 1:min(function_item["end_line"], start + 1)])[:500],
                weight=round(min(0.20, repetition * 0.20), 3),
                reason="Несколько нетипичных функций имеют близкий AST-скелет после исключения имён и литералов.",
            ))

    style = 0.40 * regularity + 0.20 * identifier_style + 0.40 * outlier
    if ui_ratio >= 0.50:
        style *= 0.35
    if style >= 0.62 and outlier >= 0.35 and profile.non_empty_lines >= 30:
        longest = max(enumerate(profile.code.splitlines(), 1), key=lambda x: len(x[1]), default=(1, ""))
        findings.append(Finding(
            type="stylometry_outlier",
            line_start=longest[0],
            line_end=longest[0],
            suspected_text=longest[1].strip()[:500],
            weight=round(min(0.16, style * 0.16), 3),
            reason="Стиль файла заметно отличается от одноязычных файлов репозитория; строка показана как опорная точка для ручной проверки, а не как доказательство AI.",
        ))

    # Every non-zero repository signal must now have an explainable finding.
    # Style is only a supporting signal and is represented by an explicit finding above.
    evidence_signals = [f.weight for f in findings]
    score = _aggregate(evidence_signals)

    unique = {}
    for finding in findings:
        key = (finding.type, finding.line_start, finding.line_end, finding.suspected_text)
        unique[key] = finding
    findings = sorted(unique.values(), key=lambda f: f.weight, reverse=True)[:12]

    return FileAnalysisResult(
        file_path=profile.path,
        is_suspicious=score >= 0.30,
        total_score=round(score, 4),
        findings=findings,
    )


def repository_signal(results: list[FileAnalysisResult]) -> float:
    if not results:
        return 0.0
    values = sorted((r.total_score for r in results), reverse=True)
    top = values[: min(8, len(values))]
    weights = [1.0 / math.log2(i + 2) for i in range(len(top))]
    weighted = sum(v * w for v, w in zip(top, weights)) / sum(weights)
    return round(min(1.0, max(0.0, weighted)), 4)
