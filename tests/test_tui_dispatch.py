from financial_analyst.tui import parse_input, IntentReport, IntentSlashCmd, IntentChat


def test_parse_natural_report():
    intent = parse_input("看看 600519")
    assert isinstance(intent, IntentReport)
    assert intent.code == "SH600519"


def test_parse_natural_report_with_prefix():
    intent = parse_input("看下 SH600519")
    assert isinstance(intent, IntentReport)
    assert intent.code == "SH600519"


def test_parse_slash_command():
    intent = parse_input("/report SZ000858")
    assert isinstance(intent, IntentReport)
    assert intent.code == "SZ000858"


def test_parse_slash_memory():
    intent = parse_input("/memory list bear-advocate")
    assert isinstance(intent, IntentSlashCmd)
    assert intent.command == "memory"
    assert intent.args == ["list", "bear-advocate"]


def test_parse_unknown_is_chat():
    intent = parse_input("hello there")
    assert isinstance(intent, IntentChat)
