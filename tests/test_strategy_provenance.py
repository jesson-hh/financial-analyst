# 漂移哨兵:vendored 文件 SHA256 与 _provenance.json 一致
#
# 任一 vendored 文件被改 / 上游升级覆盖了副本但忘了重生 provenance → 红。
# 重生方式见 guanlan_v2/strategy/_PROVENANCE.md。
import hashlib
import json

from guanlan_v2.strategy.paths import PROVENANCE_JSON


def _sha256(p) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def test_provenance_file_exists():
    assert PROVENANCE_JSON.exists(), "缺 _provenance.json(版本戳)"


def test_vendored_hashes_match():
    rows = json.loads(PROVENANCE_JSON.read_text(encoding="utf-8-sig"))
    assert rows, "provenance 为空"
    base = PROVENANCE_JSON.parent
    for rel, expected in rows.items():
        p = base / rel
        assert p.exists(), f"vendored 文件缺失: {rel}"
        actual = _sha256(p)
        assert actual.upper() == expected.upper(), (
            f"漂移: {rel} hash 不一致(副本被改 / 需重生 _provenance.json)"
        )
