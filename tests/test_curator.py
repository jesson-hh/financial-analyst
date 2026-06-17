def test_curator_archives_not_deletes(tmp_path):
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text("- [2026-06-01] a\n- [2026-06-02] b\n- [2026-06-03] c\n", encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=2)
    assert r["ok"] is True
    assert arch.exists()
    assert len(mem.read_text(encoding="utf-8").strip().splitlines()) <= 2   # 主文件收敛
    assert "a" in arch.read_text(encoding="utf-8")                          # 最旧行归档可恢复


def test_curator_at_max_lines_is_noop(tmp_path):
    """边界:行数恰好 == max_lines → archived=0,主文件不动、archive 不创建。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    before = "- [2026-06-01] a\n- [2026-06-02] b\n"
    mem.write_text(before, encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=2)
    assert r["ok"] is True and r["archived"] == 0 and r["kept"] == 2
    assert mem.read_text(encoding="utf-8") == before   # 主文件原样不动
    assert not arch.exists()                           # 无溢出不创建 archive


def test_curator_empty_file(tmp_path):
    """边界:空(或全空行)记忆文件 → archived=0/kept=0,不报错、不创建 archive。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text("\n  \n\n", encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=2)
    assert r["ok"] is True and r["archived"] == 0 and r["kept"] == 0
    assert not arch.exists()


def test_curator_appends_not_overwrites_archive(tmp_path):
    """边界:连续两次合并 → archive 追加第二个 `## 归档于` 段,不覆盖第一段(累积可恢复)。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text("- [2026-06-01] a\n- [2026-06-02] b\n- [2026-06-03] c\n", encoding="utf-8")
    consolidate_memory(mem, arch, max_lines=2)            # 归档 a
    mem.write_text(mem.read_text(encoding="utf-8") +
                   "- [2026-06-04] d\n- [2026-06-05] e\n", encoding="utf-8")
    consolidate_memory(mem, arch, max_lines=2)            # 再归档(b/c/d 溢出)
    arch_body = arch.read_text(encoding="utf-8")
    assert arch_body.count("## 归档于") == 2              # 两段归档头都在
    assert "a" in arch_body and "b" in arch_body          # 第一段未被第二次覆盖


def test_consolidate_keeps_all_keyed(tmp_path):
    """常驻(带 key)行永不归档;只归档最旧的易逝行,保留行按原始顺序。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text(
        "- [2026-06-01] (pool) 常驻A\n"
        "- [2026-06-02] 易逝1\n"
        "- [2026-06-03] (freq) 常驻B\n"
        "- [2026-06-04] 易逝2\n"
        "- [2026-06-05] 易逝3\n", encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=3)
    assert r["ok"] is True and r["archived"] == 2
    body = mem.read_text(encoding="utf-8")
    assert "常驻A" in body and "常驻B" in body
    assert "易逝3" in body
    arch_body = arch.read_text(encoding="utf-8")
    assert "易逝1" in arch_body and "易逝2" in arch_body
    assert "常驻A" not in arch_body


def test_consolidate_keyed_only_never_archives(tmp_path):
    """边界:全是常驻行且超 max_lines → 不归档(keyed 永不丢),archived=0、archive 不创建。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text(
        "- [2026-06-01] (k1) a\n- [2026-06-02] (k2) b\n- [2026-06-03] (k3) c\n",
        encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=2)
    assert r["ok"] is True and r["archived"] == 0
    body = mem.read_text(encoding="utf-8")
    assert "(k1)" in body and "(k2)" in body and "(k3)" in body
    assert not arch.exists()
