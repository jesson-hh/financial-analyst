import io
import os

from guanlan_v2.datafeed import live_runtime as runtime


def test_child_cache_paths_are_owned_without_breaking_provider_leases(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GUANLAN_LIVE_RESILIENCE_ROOT", str(tmp_path / "untrusted_override"))
    monkeypatch.setenv("GUANLAN_PROVIDER_GATEWAY_ROOT", "existing-shared-leases")
    env = runtime.child_environment()
    assert env["GUANLAN_LIVE_RESILIENCE_ROOT"] == str(runtime.runtime_root() / "resilience")
    assert env["GUANLAN_LIVE_HEALTH_ROOT"] == str(runtime.runtime_root() / "health")
    assert env["GUANLAN_PROVIDER_GATEWAY_ROOT"] == "existing-shared-leases"


def test_cache_quotas_ttl_and_no_unrelated_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime, "MAX_FILES", 2)
    monkeypatch.setattr(runtime, "MAX_ITEM_BYTES", 8)
    monkeypatch.setattr(runtime, "MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(runtime, "MAX_AGE_SECONDS", 10)
    monkeypatch.setattr(runtime.time, "time", lambda: 100)
    cache = runtime.runtime_root() / "resilience" / "last_good" / "source"
    cache.mkdir(parents=True)
    for i, (size, modified) in enumerate([(5, 99), (5, 98), (5, 97), (9, 99), (1, 80)]):
        file = cache / f"{i:024x}.json"
        file.write_bytes(b"x" * size)
        os.utime(file, (modified, modified))
    sentinel = cache / "user-notes.json"
    sentinel.write_text("preserve")
    outside = tmp_path / "existing-stocks-cache.json"
    outside.write_text("preserve")
    result = runtime.maintain_last_good()
    assert result == {"files": 2, "bytes": 10, "removed": 3, "errors": 0,
                      "temp_files": 0, "temp_bytes": 0}
    assert sentinel.read_text() == outside.read_text() == "preserve"


def test_only_old_upstream_atomic_temps_expire(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime.time, "time", lambda: 10000)
    cache = runtime.runtime_root() / "resilience" / "last_good" / "source"
    cache.mkdir(parents=True)
    old = cache / ("." + "a" * 24 + ".json.123.1.tmp")
    active = cache / ("." + "a" * 24 + ".json.123.2.tmp")
    for path, modified in ((old, 1), (active, 9999)):
        path.write_bytes(b"test")
        os.utime(path, (modified, modified))
    result = runtime.maintain_last_good()
    assert not old.exists() and active.exists()
    assert result["temp_files"] == 1 and result["temp_bytes"] == 4
    assert result["removed"] == 1


def test_stderr_drain_bounds_newline_free_output(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "LOG_MAX_BYTES", 16384)
    stream = io.BytesIO(b"x" * 100000)
    path = tmp_path / "worker.log"
    runtime.drain_worker_stderr(stream, path)
    assert path.stat().st_size <= 16384
    assert path.with_suffix(".log.1").stat().st_size <= 16384
    assert stream.closed
