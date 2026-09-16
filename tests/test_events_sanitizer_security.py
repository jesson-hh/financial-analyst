# -*- coding: utf-8 -*-
"""Focused regressions for URL and nested-payload Events disclosure bypasses."""
from __future__ import annotations

from urllib.parse import quote

from guanlan_v2.events.research import sanitize_outbound


def _encode(value: str, layers: int) -> str:
    for _ in range(layers):
        value = quote(value, safe="")
    return value


def test_non_sensitive_url_keys_and_keyless_fragments_still_redact_secret_values():
    value = {
        "query": "https://example.com/report?q=sk-abcdefghijklmnop&topic=ai",
        "fragment": "https://example.com/report#sk-ponmlkjihgfedcba",
        "scheme_value": "https://example.com/report?note=Bearer%20opaque-credential",
    }
    assert sanitize_outbound(value) == {
        "query": "https://example.com/report?q=[REDACTED]&topic=ai",
        "fragment": "https://example.com/report#[REDACTED]",
        "scheme_value": "https://example.com/report?note=[REDACTED]",
    }


def test_multilayer_encoded_nested_urls_and_payload_values_are_inspected():
    nested_secret_url = "https://nested.example/cb?token=nested-secret"
    nested_userinfo_url = "https://user:password@nested.example/cb?topic=ai"
    nested_payload = r'{"token":"json-secret","label":"keep"}'
    benign_nested_url = "https://nested.example/cb?topic=ai&lang=zh"
    value = (
        "https://example.com/route?"
        f"next={_encode(nested_secret_url, 2)}"
        f"&redirect={_encode(nested_userinfo_url, 2)}"
        f"&payload={_encode(nested_payload, 3)}"
        f"&benign={_encode(benign_nested_url, 2)}"
    )
    sanitized = sanitize_outbound(value)
    assert sanitized == (
        "https://example.com/route?next=[REDACTED]"
        "&redirect=[REDACTED]"
        "&payload=[REDACTED]"
        f"&benign={_encode(benign_nested_url, 2)}"
    )
    assert sanitize_outbound(sanitized) == sanitized


def test_payload_json_keys_are_removed_at_every_mapping_depth():
    value = {
        "payload_json": {"token": "top-level-leak"},
        "keep": {
            "Payload-JSON": "nested-leak",
            "rows": [{
                "PAYLOADJSON": "list-leak",
                "raw_payload_json": "suffix-leak",
                "label": "keep-me",
            }],
            "payload": {"label": "ordinary payload field remains readable"},
        },
    }
    assert sanitize_outbound(value) == {
        "keep": {
            "rows": [{"label": "keep-me"}],
            "payload": {"label": "ordinary payload field remains readable"},
        },
    }


def test_url_sanitizer_is_idempotent_without_harming_benign_research_text():
    value = {
        "title": "Token economy、secret sauce 与 key growth drivers",
        "url": "https://example.com/report?q=token-economy&topic=ai#section-2",
        "relative_route": "gl-mcp/tools/list",
        "malicious": (
            "https://example.com/report?q=sk-abcdefghijklmnop"
            "&next=https%253A%252F%252Fnested.example%252F%253Ftoken%253Dsecret"
        ),
    }
    once = sanitize_outbound(value)
    twice = sanitize_outbound(once)
    assert once == twice
    assert once["title"] == value["title"]
    assert once["url"] == value["url"]
    assert once["relative_route"] == value["relative_route"]
    assert once["malicious"] == (
        "https://example.com/report?q=[REDACTED]&next=[REDACTED]"
    )


def test_navigation_parameters_preserve_encoded_site_relative_routes():
    value = {
        "next": "https://example.com/login?next=%2Freports%2F2026%2Fsummary",
        "redirect": (
            "https://example.com/login?redirect=%252Freports%252F2026%252Fsummary"
        ),
        "return_to": (
            "https://example.com/login?return_to=%2Freports%2Fsummary%3Ftab%3Dnews"
        ),
        "relative": "https://example.com/login?next=reports%2F2026",
    }
    sanitized = sanitize_outbound(value)
    assert sanitized == value
    assert sanitize_outbound(sanitized) == sanitized


def test_navigation_route_exception_does_not_hide_local_paths_or_nested_secrets():
    value = {
        "custom_mount": (
            "https://example.com/login?next=%2Fcustom%2Fprivate%2Fraw.json"
        ),
        "unknown_mount": "https://example.com/login?next=%2Funknown%2Fdata%2Ffile",
        "mount_root": "https://example.com/login?next=%2Fmount%2Fvolume%2Fevents.db",
        "local_posix": "https://example.com/login?next=%2Fsrv%2Fprivate%2Fraw.json",
        "local_windows": "https://example.com/login?next=C%3A%5Cdata%5Cevents.db",
        "nested_query": (
            "https://example.com/login?"
            "next=%2Freports%2Fsummary%3Ftoken%3Dnested-secret"
        ),
        "traversal": (
            "https://example.com/login?next=%2Freports%2F..%2Fetc%2Fpasswd"
        ),
        "non_navigation": "https://example.com/search?q=%2Freports%2F2026%2Fsummary",
    }
    assert sanitize_outbound(value) == {
        "custom_mount": "https://example.com/login?next=[REDACTED]",
        "unknown_mount": "https://example.com/login?next=[REDACTED]",
        "mount_root": "https://example.com/login?next=[REDACTED]",
        "local_posix": "https://example.com/login?next=[REDACTED]",
        "local_windows": "https://example.com/login?next=[REDACTED]",
        "nested_query": "https://example.com/login?next=[REDACTED]",
        "traversal": "https://example.com/login?next=[REDACTED]",
        "non_navigation": "https://example.com/search?q=[REDACTED]",
    }


def test_url_decode_cap_fails_closed_after_three_layers_only():
    four_layer_path = _encode("/etc/passwd", 4)
    four_layer_assignment = _encode("token=nested-secret", 4)
    unsafe = (
        "https://example.com/search?"
        f"path={four_layer_path}&q={four_layer_assignment}"
    )
    assert sanitize_outbound(unsafe) == (
        "https://example.com/search?path=[REDACTED]&q=[REDACTED]"
    )

    benign = (
        "https://example.com/login?q=quarter%20one&discount=100%25"
        f"&next={_encode('/reports/2026/summary', 3)}"
    )
    assert sanitize_outbound(benign) == benign


def test_complete_and_backslash_escaped_json_strings_are_recursively_sanitized():
    complete = (
        '{"title":"keep","payload_json":{"raw":"opaque-payload-secret"},'
        '"nested":{"token":"nested-secret","label":"readable"}}'
    )
    escaped = (
        r'{\"title\":\"keep\",\"payload_json\":{\"raw\":\"opaque\"},'
        r'\"nested\":{\"api_key\":\"credential\"}}'
    )
    assert sanitize_outbound(complete) == (
        '{"title":"keep","nested":{"token":"[REDACTED]","label":"readable"}}'
    )
    assert sanitize_outbound(escaped) == (
        r'{\"title\":\"keep\",\"nested\":{\"api_key\":\"[REDACTED]\"}}'
    )


def test_incomplete_payload_json_assignments_fail_closed():
    value = {
        "equals": 'prefix payload_json={"raw":"opaque-payload-secret"} suffix',
        "colon": r'note \"payload_json\":{\"raw\":\"opaque\"} trailing',
        "variant": "raw-payload-json = opaque-value",
    }
    assert sanitize_outbound(value) == {
        "equals": "[REDACTED]",
        "colon": "[REDACTED]",
        "variant": "[REDACTED]",
    }


def test_benign_json_remains_readable_and_idempotent():
    value = (
        '{ "title": "Token economy", '
        '"url": "https://example.com/report?q=token-economy&topic=ai", '
        '"items": [1, true, "reports/2026"] }'
    )
    once = sanitize_outbound(value)
    assert once == (
        '{"title":"Token economy",'
        '"url":"https://example.com/report?q=token-economy&topic=ai",'
        '"items":[1,true,"reports/2026"]}'
    )
    assert sanitize_outbound(once) == once


def test_oversized_and_overdeep_json_like_strings_fail_closed():
    unicode_secret_key = (
        r'{"\u0074oken":"arbitrary-secret","pad":"' + ("x" * 65_536) + '"}'
    )
    oversized_benign_json = '{"title":"keep","pad":"' + ("x" * 65_536) + '"}'
    overdeep_json = ("[" * 1_200) + '{"token":"deep-secret"}' + ("]" * 1_200)
    assert sanitize_outbound(unicode_secret_key) == "[REDACTED]"
    assert sanitize_outbound(oversized_benign_json) == "[REDACTED]"
    assert sanitize_outbound(overdeep_json) == "[REDACTED]"


def test_scheme_relative_url_userinfo_is_removed_in_direct_and_embedded_text():
    value = {
        "direct": "//user:password@example.com/private?id=42",
        "embedded": "see //user:password@example.com/private?id=42 now",
        "query": "//user:password@example.com/private?token=secret&id=42",
    }
    sanitized = sanitize_outbound(value)
    assert sanitized == {
        "direct": "//example.com/private?id=42",
        "embedded": "see //example.com/private?id=42 now",
        "query": "//example.com/private?token=[REDACTED]&id=42",
    }
    assert sanitize_outbound(sanitized) == sanitized


def test_windows_device_and_extended_unc_paths_are_fully_redacted():
    value = {
        "extended_unc": r"copy \\?\UNC\server\share\secret.txt after review",
        "device_drive": r"open \\?\C:\vault\events.db now",
        "nt_drive": r"fallback \??\C:\vault\events.db now",
    }
    assert sanitize_outbound(value) == {
        "extended_unc": "copy [REDACTED_PATH] after review",
        "device_drive": "open [REDACTED_PATH] now",
        "nt_drive": "fallback [REDACTED_PATH] now",
    }
