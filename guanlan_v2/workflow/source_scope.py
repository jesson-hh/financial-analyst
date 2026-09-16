"""Pure source-code resolution; an explicit single stock must not widen to a pool."""
import re


def _canonical_code(value):
    value = value.upper()
    if "." in value:
        digits, exchange = value.split(".")
        return exchange + digits
    if len(value) == 6:
        exchange = "BJ" if value.startswith(("4", "8", "92")) else "SH" if value.startswith("6") else "SZ"
        return exchange + value
    return value


def source_codes(params):
    raw = (params or {}).get("codes")
    parts = raw if isinstance(raw, list) else re.split(r"[\s,;，、]+", str(raw or ""))
    codes = [str(item).strip() for item in parts if str(item).strip()]
    if str((params or {}).get("scope") or "").strip().lower() in {"single", "个股"}:
        code = str((params or {}).get("code") or "").strip()
        if not codes:
            codes = [code]
        if (len(codes) != 1 or not re.fullmatch(
                r"(?:(?:SH|SZ|BJ)?\d{6}|\d{6}\.(?:SH|SZ|BJ))", codes[0], re.I)):
            raise ValueError("单股 source 必须明确一个合法股票代码(code 或 codes)；拒绝回落到默认股票池")
        if code and _canonical_code(code) != _canonical_code(codes[0]):
            raise ValueError("单股 source 的 code 与 codes 冲突；请指定同一股票，拒绝猜测范围")
    return codes
