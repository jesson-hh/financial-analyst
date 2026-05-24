"""Dream aggregator — token-Jaccard clustering for Tier-4 introspector backlog.

验证: 同 (target_agent, semantic pattern) 重复 >= min_count 自动 promote 到 _proposed/,
不同 agent 不同 pattern 不混 cluster, supporting_cases 正确归集.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_analyst.dream.aggregator import (
    BOOST_KEYWORDS, _cluster_proposals, _tokenize, _weighted_jaccard,
    aggregate_pending,
)


# ─────────────────────────── Token / Jaccard ───────────────────────────


class TestTokenize:
    def test_basic_lowercase_split(self):
        t = _tokenize("mv_tier == 'large' AND bull cites bounce")
        assert "mv_tier" in t
        assert "large" in t
        assert "bounce" in t
        assert "AND" not in t  # stopword

    def test_stopwords_removed(self):
        t = _tokenize("is the and or not null be")
        assert t == set()

    def test_chinese_segments(self):
        t = _tokenize("大盘股 factor 失效, 必须 veto")
        assert "大盘股" in t
        assert "factor" in t
        assert "veto" in t

    def test_empty(self):
        assert _tokenize("") == set()
        assert _tokenize(None) == set()


class TestJaccard:
    def test_identical_full_overlap(self):
        a = {"mv_tier", "large", "bounce"}
        assert _weighted_jaccard(a, a) == 1.0

    def test_disjoint_zero(self):
        a = {"foo", "bar"}
        b = {"baz", "qux"}
        assert _weighted_jaccard(a, b) == 0.0

    def test_boost_only_filters_noise(self):
        """Boost-only Jaccard 忽略非 keyword token, 共同 BOOST 主导."""
        # both 含 mv_tier + large + bounce (BOOST), 其他全 noise
        a = {"mv_tier", "large", "bounce", "automatically", "institutional"}
        b = {"mv_tier", "large", "bounce", "macro", "exhaustion"}
        # boost-only: {mv_tier, large, bounce} 完全一致 → 1.0
        assert _weighted_jaccard(a, b, boost_only=True) == 1.0
        # full-token with BOOST 2x: inter weighted = 3*2 = 6, union weighted = 3*2+4*1 = 10
        # → 0.6 (BOOST 主导, 但 noise 稀释)
        assert 0.55 < _weighted_jaccard(a, b, boost_only=False) < 0.65

    def test_fallback_when_no_boost_in_either(self):
        """两边都没 boost keyword → 退回全 token Jaccard."""
        a = {"some", "random", "words"}
        b = {"some", "random", "tokens"}
        # boost-only mode 自动 fallback
        assert 0 < _weighted_jaccard(a, b, boost_only=True) <= 1


# ─────────────────────────── Cluster ───────────────────────────


class TestClustering:
    def test_three_similar_one_cluster(self):
        props = [
            {"pattern": "mv_tier == large AND bounce", "proposed_rule": "veto"},
            {"pattern": "mv_tier == large AND oversold bounce factor", "proposed_rule": "set position_pct=0"},
            {"pattern": "mv_tier large factor decay", "proposed_rule": "auto veto"},
        ]
        clusters = _cluster_proposals(props, threshold=0.4)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_dissimilar_separate_clusters(self):
        props = [
            {"pattern": "mv_tier == large AND bounce", "proposed_rule": "veto"},
            {"pattern": "negative event severity 3", "proposed_rule": "sell"},
            {"pattern": "vol_regime super_distr", "proposed_rule": "reduce position"},
        ]
        clusters = _cluster_proposals(props, threshold=0.4)
        assert len(clusters) == 3

    def test_empty(self):
        assert _cluster_proposals([], threshold=0.4) == []


# ─────────────────────────── E2E aggregate ───────────────────────────


class TestAggregateE2E:
    def _setup(self, tmp_path: Path, payloads: list, offset: int = 0):
        """tmp memory tree with N pending introspection files.

        Args:
            offset: 让多次调用 _setup 加新 case (而不是覆盖). 默认 0 = 覆盖.
        """
        pending = tmp_path / "_pending_introspections"
        pending.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(payloads):
            (pending / f"2026-05-23_TEST{i + offset:03d}.json").write_text(
                json.dumps(p), encoding="utf-8"
            )

    def test_no_promotion_below_threshold(self, tmp_path):
        """2 cases on same pattern → not promoted (min_count=3)."""
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "risk-officer",
                            "pattern": "mv_tier large bounce",
                            "proposed_rule": "veto",
                            "confidence": "low",
                            "rationale": "..."}]} for _ in range(2)
        ])
        written, stats = aggregate_pending(tmp_path, min_count=3, dry_run=True)
        assert stats["clusters_promoted"] == 0
        assert written == []

    def test_three_cases_promoted_med(self, tmp_path):
        """3 cases same pattern → promoted with confidence=med."""
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "risk-officer",
                            "pattern": "mv_tier large bounce factor",
                            "proposed_rule": "veto large-cap factor bounce",
                            "confidence": "low",
                            "rationale": "..."}]} for _ in range(3)
        ])
        written, stats = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        assert stats["clusters_promoted"] == 1
        assert len(written) == 1
        assert "risk-officer" in str(written[0])
        # frontmatter check
        text = written[0].read_text(encoding="utf-8")
        assert "confidence: med" in text
        assert "supporting_cases:" in text

    def test_six_cases_promoted_high(self, tmp_path):
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "bear-advocate",
                            "pattern": "mv_tier large bounce factor RSI",
                            "proposed_rule": "lead with F13",
                            "confidence": "low",
                            "rationale": "..."}]} for _ in range(6)
        ])
        written, stats = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        assert stats["clusters_promoted"] == 1
        text = written[0].read_text(encoding="utf-8")
        assert "confidence: high" in text

    def test_different_agents_not_mixed(self, tmp_path):
        """Same pattern under different target_agent → separate cluster."""
        self._setup(tmp_path, [
            {"proposals": [
                {"target_agent": "risk-officer",
                 "pattern": "mv_tier large bounce",
                 "proposed_rule": "veto", "confidence": "low", "rationale": "..."},
                {"target_agent": "bear-advocate",
                 "pattern": "mv_tier large bounce",
                 "proposed_rule": "lead F13", "confidence": "low", "rationale": "..."},
            ]} for _ in range(3)
        ])
        written, stats = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        # Both agent buckets have 3 cluster members → both promoted
        assert stats["clusters_promoted"] == 2

    def test_empty_pending_dir(self, tmp_path):
        # no _pending_introspections directory at all
        written, stats = aggregate_pending(tmp_path, dry_run=True)
        assert written == []
        assert "no _pending_introspections" in stats.get("reason", "")

    def test_idempotent_skip_unchanged(self, tmp_path):
        """二次跑 aggregate 同 cases 应该 skip, 不重复写文件."""
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "risk-officer",
                            "pattern": "mv_tier large bounce factor",
                            "proposed_rule": "veto large-cap factor bounce",
                            "confidence": "low",
                            "rationale": "..."}]} for _ in range(3)
        ])
        # 第一次写
        written1, stats1 = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        assert stats1["clusters_promoted"] == 1
        assert len(written1) == 1
        first_file = written1[0]

        # 第二次 — cases 没变, 应 skip
        written2, stats2 = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        assert stats2["clusters_promoted"] == 0
        assert stats2["skipped_unchanged"] == 1
        assert written2 == []
        # 旧文件还在
        assert first_file.exists()

    def test_idempotent_replace_on_new_cases(self, tmp_path):
        """同 slug 但 cases 多了一份 → 删旧, 写新."""
        # 第一次 3 cases
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "risk-officer",
                            "pattern": "mv_tier large bounce factor",
                            "proposed_rule": "veto",
                            "confidence": "low",
                            "rationale": "..."}]} for _ in range(3)
        ])
        written1, _ = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        assert len(written1) == 1
        first_file = written1[0]

        # 加第 4 份 introspection (offset=3 避免覆盖)
        self._setup(tmp_path, [
            {"proposals": [{"target_agent": "risk-officer",
                            "pattern": "mv_tier large bounce factor",
                            "proposed_rule": "veto",
                            "confidence": "low",
                            "rationale": "..."}]}
        ], offset=3)
        # 现在 4 cases (TEST000-003)
        # 重跑 — supporting_cases 应该从 3 → 4 (changed), 应 promote replace
        written2, stats2 = aggregate_pending(tmp_path, min_count=3, dry_run=False)
        # 关键不变量: 不应有 2 个 _<slug>.md (旧的被删, 写新的)
        proposed = list((tmp_path / "_proposed" / "risk-officer").glob("*.md"))
        assert len(proposed) == 1, f"多版本 _proposed/<slug>.md 残留: {[p.name for p in proposed]}"
        # 新文件应该含 4 cases
        text = proposed[0].read_text(encoding="utf-8")
        assert "TEST003" in text, f"新加 TEST003 case 应该在 supporting_cases 里"
