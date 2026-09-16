from __future__ import annotations

from guanlan_v2.research import company_industry_news as industry_news


def _record(industry: str = "半导体"):
    return {
        "status": "available",
        "name": "兆易创新",
        "industry": industry,
        "source_id": "tushare.stock_basic.industry",
        "taxonomy": "Tushare 基础行业（非申万/证监会分类）",
        "observed_at": "2026-08-28T19:32:34+08:00",
        "source_sha256": "a" * 64,
        "point_in_time": False,
    }


def test_exact_industry_crosswalk_selects_only_semiconductor(monkeypatch):
    monkeypatch.setattr(industry_news, "_industry_record", lambda code: _record())

    context = industry_news.resolve_industry_news_context(
        "SH603986", as_of="2026-08-30", today="2026-08-30",
    )

    assert context["status"] == "ready"
    assert context["industry"] == "半导体"
    assert context["radar_sectors"] == ["semi"]
    assert context["selection_mode"] == "automatic_exact_crosswalk"
    assert context["mapping_version"] == "newsradar-industry-crosswalk@2"
    assert context["mapping_quality"] == "exact"


def test_broad_context_mapping_is_visible_not_misrepresented_as_exact(monkeypatch):
    monkeypatch.setattr(industry_news, "_industry_record", lambda code: {
        "status": "available", "name": "示例化工", "industry": "化工原料",
        "source_id": "fixture", "taxonomy": "fixture", "observed_at": "2026-09-04",
        "source_sha256": "a" * 64, "point_in_time": False,
    })
    context = industry_news.resolve_industry_news_context(
        "SH600000", today="2026-09-04",
    )
    assert context["radar_sectors"] == ["energy"]
    assert context["selection_mode"] == "automatic_reviewed_broad_context"
    assert context["mapping_method"] == "reviewed_broad_context"
    assert context["mapping_quality"] == "broad_context"
    assert "不等于化工专类" in context["mapping_note"]
    assert context["user_judgement_required"] is True
    assert context["may_close_issuer_gate"] is False
    assert context["user_judgement_required"] is True


def test_unmapped_industry_fails_closed_without_all_sector_fallback(monkeypatch):
    monkeypatch.setattr(
        industry_news, "_industry_record", lambda code: _record("未覆盖行业"),
    )

    context = industry_news.resolve_industry_news_context(
        "SH603929", as_of="2026-08-30", today="2026-08-30",
    )

    assert context["status"] == "industry_unmapped"
    assert context["radar_sectors"] == []
    assert "用户明确选择" in context["reason"]


def test_user_override_is_audited_but_does_not_claim_company_relevance(monkeypatch):
    monkeypatch.setattr(
        industry_news, "_industry_record", lambda code: _record("装修装饰"),
    )

    context = industry_news.resolve_industry_news_context(
        "SH603929", selected_sectors=["semi", "semi", "tech"],
        as_of="2026-08-30", today="2026-08-30",
    )

    assert context["status"] == "ready"
    assert context["radar_sectors"] == ["semi", "tech"]
    assert context["selection_mode"] == "user_confirmed_override"
    assert context["relation_status"] == "unreviewed"


def test_current_rss_is_not_used_for_historical_research(monkeypatch):
    monkeypatch.setattr(industry_news, "_industry_record", lambda code: _record())

    context = industry_news.resolve_industry_news_context(
        "SH603986", as_of="2026-08-29", today="2026-08-30",
    )

    assert context["status"] == "historical_feed_unavailable"
    assert context["radar_sectors"] == ["semi"]
    assert "非当日历史" in context["reason"]


def test_snapshot_rows_are_candidates_not_issuer_facts(monkeypatch):
    monkeypatch.setattr(industry_news, "_industry_record", lambda code: _record())
    context = industry_news.resolve_industry_news_context(
        "SH603986", as_of="2026-08-30", today="2026-08-30",
    )
    snapshot = industry_news.annotate_newsradar_snapshot(
        {
            "dataset_id": "research.newsradar.live",
            "query": {"sectors": ["semi"]},
            "rows": [{
                "title": "Semiconductor equipment update",
                "url": "https://example.test/item",
                "source": "Example",
                "sector": "semi",
                "ts": 1788055200,
                "subject_code": "SH603986",
            }],
            "warnings": [],
        },
        context,
    )

    row = snapshot["rows"][0]
    relation = snapshot["industry_candidate_relations"]["rows"][0]
    assert "candidate_id" not in row  # 稳定 provider row 不承载扩展关系字段
    assert relation["candidate_id"].startswith("INC-")
    assert relation["collection"] == "rows" and relation["row_index"] == 0
    assert relation["research_scope"] == "industry_context"
    assert relation["relation_status"] == "unreviewed"
    assert relation["issuer_fact"] is False
    assert "subject_code" not in row
    assert snapshot["industry_context"]["industry"] == "半导体"
    assert "用户逐条判断" in snapshot["warnings"][-1]
