from copy import deepcopy

from guanlan_v2.research.company_report_review import render_company_report_review


def _gate(**extra):
    return {"status": "incomplete", "completion_status": "incomplete",
            "freshness_status": "fresh", "violations": [], "warnings": [], **extra}


def test_review_preserves_exact_findings_status_and_input():
    gate = _gate(violations=[{
        "line": 2, "section_id": "valuation", "code": "unsupported_numeric_claim",
        "check_id": "numeric_fidelity", "message": "Number missing",
        "details": {"number": "42", "cited_refs": ["CE-123"]},
    }])
    before = deepcopy(gate)
    result = render_company_report_review("# Report\nBaseline 42", gate, {
        "status": "complete", "semantic_review": {"status": "not_independently_verified"},
    })
    assert "incomplete" in result and "L2" in result and "Number missing" in result
    assert r"unsupported\_numeric\_claim" in result
    assert r"not\_independently\_verified" in result
    assert "Baseline 42" in result and r"CE\-123" in result
    assert "1 / 0" in result and "风险结构通过不等于反证方向正确" in result
    assert gate == before


def test_review_complete_is_not_claimed_as_semantic_verification():
    result = render_company_report_review("", _gate(status="complete"), {"status": "complete"})
    assert "unknown" in result
    assert "不代表独立语义核验或全部数据源已完成" in result
    assert "empty 不能解释为不存在事件" in result


def test_review_escapes_untrusted_markup_and_does_not_follow_paths():
    text = '<script>alert(1)</script> ![x](https://evil.test/x) | file:///private'
    gate = _gate(warnings=[{"line": 1, "code": text, "message": text}])
    result = render_company_report_review(text, gate, {})
    assert "<script>" not in result and "![x](" not in result
    assert "&lt;script&gt;" in result and r"\|" in result
    assert "0 / 1" in result


def test_review_bounds_long_diagnostics_and_rejects_invalid_line_numbers():
    rows = [{"line": -1, "message": "x" * 10000, "code": "bad"} for _ in range(205)]
    result = render_company_report_review("SECRET", _gate(violations=rows), {})
    assert "前 200 / 205" in result and "（截断）" in result
    assert "SECRET" not in result and len(result) < 120000
    for value in (True, "1", 999):
        result = render_company_report_review("SECRET", _gate(violations=[{"line": value}]), {})
        assert "SECRET" not in result and "无有效行号" in result


def test_review_repeats_line_excerpt_only_once_per_finding_kind():
    gate = _gate(violations=[{"line": 1, "code": str(i)} for i in range(4)])
    result = render_company_report_review("EXACT LINE", gate, {})
    assert result.count("EXACT LINE") == 1
