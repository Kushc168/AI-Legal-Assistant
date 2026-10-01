import re
from pathlib import Path

import pytest

from backend.prompts import loader

REQUIRED = {"qa.answer", "contract.summarize", "risk.analyze", "clause.explain", "contract.compare",
            "summary.executive", "impact.business"}
BACKEND = Path(__file__).resolve().parent.parent / "backend"


def test_all_required_prompts_load():
    prompts = loader.registry()
    assert REQUIRED <= set(prompts)
    for pid in REQUIRED:
        p = prompts[pid]
        assert p.system and p.user and p.schema, pid
        assert "GROUNDING RULES" in p.system, f"{pid} must include shared.rules"
        assert "temperature" not in p.meta


def test_declared_variables_match_templates():
    for pid in REQUIRED:
        p = loader.get(pid)
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", p.system + p.user))
        assert used == set(p.variables), f"{pid}: template uses {used}, meta declares {set(p.variables)}"


def test_render_fails_on_missing_variable():
    with pytest.raises(loader.PromptError):
        loader.render("qa.answer", {"question": "x"})


def test_render_does_not_expand_variables_inside_values():
    system, user = loader.render(
        "qa.answer",
        {"question": "{{history}}", "excerpts": "e", "history": "h", "contract_names": "c"},
    )
    assert "{{history}}" in user


def test_api_schema_is_structured_output_compatible():
    for pid in REQUIRED:
        schema = loader.api_schema(loader.get(pid).schema)

        def walk(node):
            if isinstance(node, dict):
                for banned in ("maxLength", "minimum", "maximum", "minItems", "maxItems"):
                    assert banned not in node, f"{pid} still has {banned}"
                if node.get("type") == "object" and "properties" in node:
                    assert node.get("additionalProperties") is False, pid
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(schema)


def test_services_reference_only_known_prompts():
    known = set(loader.registry())
    for path in BACKEND.rglob("*.py"):
        for pid in re.findall(r"complete_json\(\s*\"([\w.]+)\"", path.read_text(encoding="utf-8")):
            assert pid in known, f"{path.name} references unknown prompt {pid}"


def test_no_prompt_text_in_routes():
    for path in (BACKEND / "routes").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "You are" not in text and "GROUNDING RULES" not in text, path.name
