from yuxi.agents.buildin.chatbot.prompt import PROMPT


def test_chatbot_prompt_does_not_duplicate_html_preview_skill_instructions():
    assert "html:preview" not in PROMPT


def test_chatbot_prompt_uses_current_product_name():
    assert "知铸" in PROMPT
    assert "语析" not in PROMPT
