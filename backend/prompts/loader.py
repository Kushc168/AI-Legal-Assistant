"""Parses PROMPTS.md into a prompt registry. PROMPTS.md is the only place prompt text lives.

Format (see PROMPTS.md "File format"):
    ## prompt: <id>
    ```meta ... ```
    ### system   ```text ... ```
    ### user     ```text ... ```
    ### output schema   ```json ... ```
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import PROMPTS_FILE

_PROMPT_HEADING = re.compile(r"^## prompt:\s*([\w.]+)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```(\w+)\s*\n(.*?)\n```", re.DOTALL)
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}")
_INCLUDE = re.compile(r"\{\{>\s*([\w.]+)\s*\}\}")

# Keywords the structured-output API rejects; enforced locally instead.
_UNSUPPORTED_KEYWORDS = {
    "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minItems", "maxItems", "uniqueItems", "pattern", "description",
}


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    meta: dict
    system: str
    user: str
    schema: dict | None

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "1"))

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    @property
    def variables(self) -> list[str]:
        return list(self.meta.get("variables", []))


def _parse_meta(text: str) -> dict:
    meta: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() in {"true", "false"}:
            meta[key] = value.lower() == "true"
        else:
            meta[key] = value
    return meta


def _section(body: str, heading: str) -> str | None:
    match = re.search(rf"^### {re.escape(heading)}\s*$", body, re.MULTILINE)
    if not match:
        return None
    rest = body[match.end():]
    nxt = re.search(r"^### ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _first_fence(text: str | None, lang: str) -> str | None:
    if text is None:
        return None
    for fence_lang, content in _FENCE.findall(text):
        if fence_lang == lang:
            return content
    return None


def parse_prompts(markdown: str) -> dict[str, Prompt]:
    headings = list(_PROMPT_HEADING.finditer(markdown))
    raw: dict[str, dict] = {}
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        body = markdown[match.end():end]
        # A prompt section ends at the next level-2 heading that is not a prompt heading.
        stop = re.search(r"^## (?!prompt:)", body, re.MULTILINE)
        if stop:
            body = body[: stop.start()]
        pid = match.group(1)
        meta_text = _first_fence(body.split("###", 1)[0], "meta")
        if meta_text is None:
            raise PromptError(f"Prompt {pid} has no meta block")
        schema_text = _first_fence(_section(body, "output schema"), "json")
        raw[pid] = {
            "meta": _parse_meta(meta_text),
            "system": _first_fence(_section(body, "system"), "text") or "",
            "user": _first_fence(_section(body, "user"), "text") or "",
            "schema": json.loads(schema_text) if schema_text else None,
        }

    def expand(text: str, seen: tuple[str, ...] = ()) -> str:
        def repl(m: re.Match) -> str:
            ref = m.group(1)
            if ref in seen:
                raise PromptError(f"Circular include: {ref}")
            if ref not in raw:
                raise PromptError(f"Unknown include: {ref}")
            return expand(raw[ref]["system"], (*seen, ref)).strip()

        return _INCLUDE.sub(repl, text)

    prompts: dict[str, Prompt] = {}
    for pid, data in raw.items():
        prompts[pid] = Prompt(
            id=pid,
            meta=data["meta"],
            system=expand(data["system"]),
            user=expand(data["user"]),
            schema=data["schema"],
        )
    return prompts


@lru_cache(maxsize=1)
def registry(path: Path = PROMPTS_FILE) -> dict[str, Prompt]:
    return parse_prompts(path.read_text(encoding="utf-8-sig"))


def get(prompt_id: str) -> Prompt:
    prompts = registry()
    if prompt_id not in prompts:
        raise PromptError(f"Unknown prompt id: {prompt_id}")
    return prompts[prompt_id]


def _fill(template: str, variables: dict, prompt_id: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in variables:
            raise PromptError(f"Prompt {prompt_id} needs variable '{name}'")
        value = variables[name]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)

    return _VAR.sub(repl, template)


def render(prompt_id: str, variables: dict) -> tuple[str, str]:
    """Return (system, user) text with all {{variables}} filled. Raises on a missing variable."""
    prompt = get(prompt_id)
    return _fill(prompt.system, variables, prompt_id), _fill(prompt.user, variables, prompt_id)


def api_schema(schema: dict) -> dict:
    """Convert a PROMPTS.md schema into one the structured-output API accepts."""

    def walk(node):
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
            is_object = out.get("type") == "object" or (
                isinstance(out.get("type"), list) and "object" in out["type"]
            )
            if is_object and "properties" in out:
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(copy.deepcopy(schema))
