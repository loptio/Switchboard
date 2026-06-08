"""Offline tests for the brief agents + parsers (no network, no SDK).

The `llm` seam is injected as a fake, so these exercise prompt-building and strict
contract validation only — never a real model call.
"""

import pytest

from agent import AgentContractError
from brief_agent import (
    FILTER_SUMMARY_CHARS,
    Perspective,
    filter_agent,
    parse_filter,
    perspective_agent,
    summarize_item_agent,
)
from sources import SourceItem


def _item(title="T1", text="some text"):
    return SourceItem(title, "https://e/1", "Src", "科技", "pub", text)


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, *, system_prompt, model):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt, "model": model})
        return self.reply


# --- parse_filter ----------------------------------------------------------
def test_parse_filter_valid_indices_preserve_order():
    assert parse_filter('{"keep": [3, 1]}', 3, 8) == [3, 1]


def test_parse_filter_truncates_to_keep_cap():
    assert parse_filter('{"keep": [1,2,3,4,5]}', 5, 3) == [1, 2, 3]


def test_parse_filter_drops_out_of_range_dupes_and_non_ints():
    # 9 out of range, duplicate 1, "x" non-int, true (bool) excluded
    assert parse_filter('{"keep": [1, 9, 1, "x", true, 2]}', 3, 8) == [1, 2]


def test_parse_filter_empty_keep_is_valid():
    assert parse_filter('{"keep": []}', 3, 8) == []


def test_parse_filter_missing_keep_raises():
    with pytest.raises(AgentContractError):
        parse_filter('{"nope": []}', 3, 8)


def test_parse_filter_not_json_raises():
    with pytest.raises(AgentContractError):
        parse_filter("not json at all", 3, 8)


# --- filter_agent ----------------------------------------------------------
def test_filter_agent_maps_indices_back_to_source_items():
    items = [_item("A"), _item("B"), _item("C")]
    llm = FakeLLM('{"keep": [3, 1]}')

    kept = filter_agent(items, "m", llm=llm)

    assert [it.title for it in kept] == ["C", "A"]


def test_filter_agent_feeds_only_a_short_summary_not_full_text():
    long_text = "X" * (FILTER_SUMMARY_CHARS + 200)
    items = [_item("A", text=long_text)]
    llm = FakeLLM('{"keep": [1]}')

    filter_agent(items, "m", llm=llm)

    prompt = llm.calls[0]["prompt"]
    assert "…" in prompt                      # truncation marker present
    assert long_text not in prompt            # the full body never reaches the filter
    # the filter judges only from title/source/summary, no outside knowledge
    assert "outside knowledge" in llm.calls[0]["system_prompt"]


def test_filter_agent_empty_returns_empty_without_calling_llm():
    llm = FakeLLM('{"keep": []}')
    assert filter_agent([], "m", llm=llm) == []
    assert llm.calls == []


def test_filter_agent_ignores_out_of_range_and_duplicate_indices():
    # parse_filter is the single chokepoint: out-of-range/dup indices are dropped
    # here, so the orchestrator only ever maps clean, in-range, unique indices
    # (it never crashes on a bad index nor double-counts an item).
    items = [_item("A"), _item("B")]
    llm = FakeLLM('{"keep": [2, 2, 9, 1]}')  # dup 2, out-of-range 9
    kept = filter_agent(items, "m", llm=llm)
    assert [it.title for it in kept] == ["B", "A"]  # range-checked + deduped, order kept


# --- summarize_item_agent --------------------------------------------------
def test_summarize_item_agent_strips_and_returns_text():
    llm = FakeLLM("  A concise summary.  ")
    assert summarize_item_agent(_item(), "m", llm=llm) == "A concise summary."


def test_summarize_item_agent_empty_raises():
    with pytest.raises(AgentContractError):
        summarize_item_agent(_item(), "m", llm=FakeLLM("   "))


def test_summary_prompt_forbids_fabrication():
    llm = FakeLLM("ok")
    summarize_item_agent(_item(), "m", llm=llm)
    assert "fabrication" in llm.calls[0]["system_prompt"].lower()


# --- perspective_agent -----------------------------------------------------
def test_perspective_agent_sets_stance_and_take():
    llm = FakeLLM("A business angle.")
    p = perspective_agent(_item(), "商业", "m", llm=llm)
    assert p == Perspective(stance="商业", take="A business angle.")
    # the stance lens is in the system prompt; grounding instruction present
    assert "商业" in llm.calls[0]["system_prompt"]
    assert "do not fabricate" in llm.calls[0]["system_prompt"].lower()


def test_perspective_agent_empty_raises():
    with pytest.raises(AgentContractError):
        perspective_agent(_item(), "技术", "m", llm=FakeLLM(""))
