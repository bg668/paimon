from __future__ import annotations


def build_system_prompt(role: str, prompt_text: str) -> str:
    prompt_text = prompt_text.strip()
    role = role.strip()

    if role and prompt_text:
        return f"角色职责：{role}\n\n{prompt_text}"
    if role:
        return f"角色职责：{role}"
    return prompt_text
