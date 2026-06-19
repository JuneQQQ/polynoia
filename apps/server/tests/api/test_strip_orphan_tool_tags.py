"""Orphan tool-call protocol tags (a lone </parameter>, dangling <invoke>, antml:
variants) must never survive into displayed text — the structured recoveries
only catch well-formed blocks, so this sweeps the stragglers (observed: opus
leaking a trailing </parameter> after an ask_user call, which rendered as text)."""
from __future__ import annotations

from polynoia.api.routes import _strip_orphan_tool_tags as strip


def test_orphan_closing_parameter_stripped():
    assert strip("我用表单问你,填完我直接开干。\n\n</parameter>") == "我用表单问你,填完我直接开干。"


def test_dangling_open_and_wrapper_tags_stripped():
    assert strip('text <parameter name="x"> tail').strip() == "text  tail".strip()
    assert strip("a </invoke> b </function_calls> c").strip() == "a  b  c".strip()


def test_real_angle_brackets_in_prose_and_code_untouched():
    # Must NOT strip legitimate < / > in normal text or code.
    assert strip("code: if a < b and c > d") == "code: if a < b and c > d"
    assert strip("normal text, no tags") == "normal text, no tags"
    assert strip("<div>html in a fence</div>") == "<div>html in a fence</div>"
