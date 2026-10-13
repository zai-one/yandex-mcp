"""Routing eval: messy user requests -> router line -> skill/guide -> expected tool.

The router (server instructions) is the only thing an agent sees at startup. Each
case must match exactly one router line by its intent words, and the expected tool
must be reachable from that line (named on it, in its skill, or in its guide).
Also fails if any tool, skill or guide is unreachable from the router.
"""

import json
import re
from pathlib import Path

import pytest

from zai_yandex.agent_layer import GUIDE_TOPICS, ROUTES, build_instructions, guide
from zai_yandex.toolsets import ALL_TOOLS, TOOLSETS, build_surface

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/evals/routing_cases.json").read_text(encoding="utf-8"))
WORD = re.compile(r"[a-zа-яё0-9]+")


def stems(text: str) -> set[str]:
    return {word[:4] for word in WORD.findall(text.lower().replace("ё", "е")) if len(word) >= 3}


def route_for(request: str) -> str:
    words = stems(request)
    scores = {rid: len(stems(intent) & words) for rid, intent, *_ in ROUTES}
    best = max(scores.values())
    winners = [rid for rid, score in scores.items() if score == best and score > 0]
    assert len(winners) == 1, (request, winners or "no route")
    return winners[0]


def skill_text(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8") if name else ""


def test_eval_has_enough_realistic_cases():
    assert len(CASES) >= 20
    assert {case["route"] for case in CASES} == {route[0] for route in ROUTES}


@pytest.mark.parametrize("case", CASES, ids=[c["request"][:40] for c in CASES])
def test_request_routes_to_skill_guide_and_tool(case):
    instructions = build_instructions(build_surface("all"))
    rid = route_for(case["request"])
    assert rid == case["route"]
    _, intent, first_tool, skill, topic = next(r for r in ROUTES if r[0] == rid)
    line = next(line for line in instructions.splitlines() if line.startswith(f"- {intent}:"))
    assert topic in line and (not skill or skill in line) and (not first_tool or first_tool in line)
    if skill:
        assert (ROOT / "skills" / skill / "SKILL.md").is_file()
    assert topic in GUIDE_TOPICS
    reachable = " ".join((line, skill_text(skill), guide(topic)))
    assert not case["tool"] or case["tool"] in reachable, (case["tool"], rid)
    assert not first_tool or first_tool in ALL_TOOLS


def test_every_capability_is_reachable_from_router():
    instructions = build_instructions(build_surface("default"))
    assert "yandex://playbook" in instructions and "yandex://guide/tool-map" in instructions
    tool_map = guide("tool-map")
    missing_tools = sorted(tool for tool in ALL_TOOLS if f"`{tool}`" not in tool_map)
    assert not missing_tools, f"tools missing from tool-map: {missing_tools}"
    assert all(f"## {name}" in tool_map for name in TOOLSETS)
    skills = {path.name for path in (ROOT / "skills").iterdir()}
    assert skills <= {route[3] for route in ROUTES}, "every skill needs a router line"
    from zai_yandex.agent_layer import register_agent_layer  # noqa: F401  (playbook lists guides)

    routed_topics = {route[4] for route in ROUTES}
    for topic in set(GUIDE_TOPICS) - routed_topics:
        assert any(f"references/{topic}" in skill_text(name) for name in skills), topic
