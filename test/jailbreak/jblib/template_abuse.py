"""Template Abuse / Chat Template Jailbreak — forge Role Tags (ChatInject).

Paper: Chang et al., 2026 — ChatInject Related Work vs ChatBug (Chat Template Jailbreak)

  ChatBug (Zhao et al.):
    Modifies the chat template *structure* itself to break safety bindings.

  ChatInject / Template Abuse (this module):
    Forges Role Tags (system / user / assistant) using the model's native delimiters
    so the model believes the malicious instruction originates from the System role.

Jailbreak env: forged tags live in a **single user message** (direct chat).
Distinct from:
  • IPI test/1 — same tag structure but embedded in **tool output**
  • Strategy 3 — **perturbed** tags (remove/replace/insert), not clean Role Tag forgery
  • ChatBug — template structure modification (not implemented here; different threat model)
"""

from __future__ import annotations

from lib.common import ATTENTION_PREFIX
from lib.templates import ChatTemplate, get_template_for_model

TEMPLATE_ABUSE_VARIANTS: list[str] = ["plain_text", "role_tag"]

ALL_VARIANTS: list[str] = ["direct", *TEMPLATE_ABUSE_VARIANTS]

VARIANT_LABELS: dict[str, str] = {
    "direct": "direct（直接有害请求）",
    "plain_text": "plain_text（纯文本注入，无 Role Tag）",
    "role_tag": "role_tag（伪造 Role Tag · ChatInject）",
}


def _harmful(scenario: dict) -> str:
    return scenario["harmful_request"].strip()


def _override_prefix(scenario: dict) -> str:
    return (scenario.get("attacker_prefix") or ATTENTION_PREFIX).strip()


def build_role_tag_injection(
    template: ChatTemplate,
    *,
    override_prefix: str,
    harmful_request: str,
) -> str:
    """
    ChatInject Figure 2B — forge system + user + assistant role tags.

    Structure:
      wrap_system(override) + wrap_user(harmful) + wrap_assistant("")
    """
    return (
        template.wrap_system(override_prefix)
        + template.wrap_user(harmful_request)
        + template.wrap_assistant("")
    )


def build_template_abuse_prompt(
    scenario: dict,
    variant: str,
    *,
    model: str,
) -> dict:
    """
    Build user prompt for Template Abuse jailbreak.
    Returns user_prompt, template_family, injection_structure, forged_payload.
    """
    harmful = _harmful(scenario)
    prefix = _override_prefix(scenario)

    if variant == "direct":
        return {
            "user_prompt": harmful,
            "template_family": "",
            "injection_structure": "none",
            "forged_payload": "",
            "attacker_prefix": "",
        }

    template = get_template_for_model(model)

    if variant == "plain_text":
        user_prompt = f"{prefix}: {harmful}"
        return {
            "user_prompt": user_prompt,
            "template_family": template.name,
            "injection_structure": "plain_text",
            "forged_payload": user_prompt,
            "attacker_prefix": prefix,
        }

    if variant == "role_tag":
        forged = build_role_tag_injection(
            template,
            override_prefix=prefix,
            harmful_request=harmful,
        )
        return {
            "user_prompt": forged,
            "template_family": template.name,
            "injection_structure": "system+user+assistant",
            "forged_payload": forged,
            "attacker_prefix": prefix,
        }

    raise ValueError(f"unknown template abuse variant: {variant}")
