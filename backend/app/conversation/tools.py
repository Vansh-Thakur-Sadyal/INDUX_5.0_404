"""The actions the assistant may propose, and what it takes to run one.

The rule the whole file exists to enforce: **the model proposes, this decides,
and the browser executes exactly one validated thing.** The model never returns
JavaScript, never names a selector, and never reaches the page. It picks a tool
by name and supplies arguments, both of which are checked here against a fixed
schema before anything is sent back.

Risk tiers, from the blueprint
------------------------------
``READ``          summarise, find, scroll. Runs on its own.
``LOW``           open a known same-origin page, expand a panel. Runs when the
                  farmer has switched actions on, and says what it did.
``CONSEQUENTIAL`` submit, send, delete, pay, change a setting. Never runs
                  without the farmer being shown the exact action first and
                  saying yes to that specific one.

Nothing in the registry is consequential today. That is not an oversight - this
assistant has no tool that spends money or sends a message, and the tier exists
so that adding one cannot accidentally be the same thing as shipping it.

Why confirmation is a token rather than a flag
----------------------------------------------
"The user confirmed" sent by the browser is worth nothing: the browser is the
thing being driven. So a consequential proposal is stored here with an id, and
the only way to execute it is to come back with that id before it expires. A
confirmation cannot be forged, replayed after the page changed, or applied to a
different action than the one that was shown.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

READ = "read"
LOW = "low"
CONSEQUENTIAL = "consequential"

# A proposal nobody confirmed is not left lying around. Two minutes is long
# enough to read what it says and short enough that the page is still the page
# it was proposed against.
PENDING_TTL_SECONDS = 120


@dataclass
class Tool:
    name: str
    tier: str
    summary: str
    arguments: Dict[str, str]
    validate: Callable[[Dict[str, Any]], Optional[str]]
    # What the farmer is shown before a consequential action runs. Written as a
    # sentence about consequence, not about mechanism: "send this message to
    # Ramesh", never "POST /api/messages".
    consequence: str = ""


def _target_id(value: Any) -> Optional[str]:
    """Ids are issued by the page snapshot and look like ``el-12``."""
    if not isinstance(value, str) or not re.fullmatch(r"el-\d{1,6}", value):
        return "targetId must be an element id from the current page snapshot"
    return None


# Pages this assistant may navigate to, by the names the voice parser already
# uses. An allowlist rather than a URL check: "same origin" would still let a
# model send a farmer to the admin console.
def _known_page(value: Any) -> Optional[str]:
    from . import actions as action_registry

    if not isinstance(value, str) or value not in action_registry.PAGES:
        return "page must be one of the dashboard pages"
    return None


REGISTRY: Dict[str, Tool] = {
    "get_page_summary": Tool(
        name="get_page_summary", tier=READ,
        summary="Describe what is on the page the farmer is looking at.",
        arguments={"section": "optional heading to focus on"},
        validate=lambda args: None if isinstance(args.get("section", ""), str)
        else "section must be text",
    ),
    "find_element": Tool(
        name="find_element", tier=READ,
        summary="Find a control by what it is called, and report the candidates.",
        arguments={"label": "what the farmer called it"},
        validate=lambda args: None if str(args.get("label", "")).strip()
        else "label is required",
    ),
    "scroll_to": Tool(
        name="scroll_to", tier=READ,
        summary="Bring part of the page into view.",
        arguments={"targetId": "an element id from the snapshot"},
        validate=lambda args: _target_id(args.get("targetId")),
    ),
    "navigate": Tool(
        name="navigate", tier=LOW,
        summary="Open one of the dashboard's own pages.",
        arguments={"page": "a dashboard page name"},
        validate=lambda args: _known_page(args.get("page")),
    ),
    "click_element": Tool(
        name="click_element", tier=LOW,
        summary="Press a button that is visible on the page.",
        arguments={"targetId": "an element id from the snapshot"},
        validate=lambda args: _target_id(args.get("targetId")),
    ),
    "capture_screenshot": Tool(
        name="capture_screenshot", tier=READ,
        summary="Take a picture of a chart to look at it.",
        arguments={"targetId": "optional element id"},
        validate=lambda args: None if not args.get("targetId")
        else _target_id(args.get("targetId")),
    ),
}


@dataclass
class Decision:
    """What the policy engine decided about one proposed action."""

    allowed: bool
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    tier: str = READ
    reason: str = ""
    # Set when the farmer has to be asked first. The action does not run until
    # this id comes back.
    confirm_id: str = ""
    consequence: str = ""

    def as_dict(self) -> Dict[str, Any]:
        body = {"allowed": self.allowed, "tool": self.tool,
                "arguments": self.arguments, "tier": self.tier}
        if self.reason:
            body["reason"] = self.reason
        if self.confirm_id:
            body["confirm_id"] = self.confirm_id
            body["consequence"] = self.consequence
        return body


# Proposals waiting for a yes. Held in memory on purpose: a confirmation that
# survived a restart would be a confirmation of an action nobody can still see.
_pending: Dict[str, Dict[str, Any]] = {}


def _sweep() -> None:
    now = time.time()
    for key in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL_SECONDS]:
        _pending.pop(key, None)


def decide(proposal: Any, *, owner: str = "",
           allow_low_risk: bool = False) -> Decision:
    """Check one proposed action against the schema and the policy."""
    if not isinstance(proposal, dict):
        return Decision(False, reason="an action must be an object")

    name = str(proposal.get("tool") or proposal.get("name") or "")
    tool = REGISTRY.get(name)
    if tool is None:
        # Named rather than guessed at: a model that invents a tool should see
        # that it did, and so should anybody reading the logs.
        return Decision(False, tool=name,
                        reason="there is no tool called %r" % name)

    arguments = proposal.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    problem = tool.validate(arguments)
    if problem:
        return Decision(False, tool=name, tier=tool.tier, reason=problem)

    # Only the arguments the tool declares. An extra key is either a
    # misunderstanding or an attempt, and neither should reach the browser.
    arguments = {k: v for k, v in arguments.items() if k in tool.arguments}

    if tool.tier == READ:
        return Decision(True, tool=name, arguments=arguments, tier=tool.tier)

    if tool.tier == LOW:
        if not allow_low_risk:
            return Decision(False, tool=name, arguments=arguments, tier=tool.tier,
                            reason="the farmer has not switched on actions")
        return Decision(True, tool=name, arguments=arguments, tier=tool.tier)

    _sweep()
    confirm_id = "act-" + uuid.uuid4().hex[:12]
    _pending[confirm_id] = {"tool": name, "arguments": arguments,
                            "owner": owner, "at": time.time()}
    return Decision(False, tool=name, arguments=arguments, tier=tool.tier,
                    reason="this one needs the farmer to agree first",
                    confirm_id=confirm_id,
                    consequence=tool.consequence or tool.summary)


def confirm(confirm_id: str, *, owner: str = "") -> Decision:
    """Redeem a confirmation. One use, by the account it was issued to."""
    _sweep()
    held = _pending.pop(str(confirm_id or ""), None)
    if held is None:
        return Decision(False, reason="that confirmation has expired or was already used")
    if held.get("owner") and held["owner"] != owner:
        return Decision(False, reason="that confirmation belongs to another session")
    tool = REGISTRY[held["tool"]]
    return Decision(True, tool=tool.name, arguments=held["arguments"], tier=tool.tier)


def catalogue() -> List[Dict[str, Any]]:
    """The tools, for the model's prompt and for anybody reading the API."""
    return [{"tool": t.name, "tier": t.tier, "summary": t.summary,
             "arguments": t.arguments} for t in REGISTRY.values()]


def prompt_block() -> str:
    """The tools as the model is told about them."""
    lines = ["TOOLS you may propose. Reply with JSON only when an action is "
             "genuinely wanted; otherwise answer in words."]
    for tool in REGISTRY.values():
        args = ", ".join("%s (%s)" % (k, v) for k, v in tool.arguments.items())
        lines.append("- %s [%s]: %s  arguments: %s"
                     % (tool.name, tool.tier, tool.summary, args or "none"))
    lines.append("Never invent a tool. Never emit code. An element id must come "
                 "from the page snapshot you were given.")
    return "\n".join(lines)
