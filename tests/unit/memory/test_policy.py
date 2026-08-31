import pytest

from customer_service_agent.memory.service import MemoryIntent, MemoryPolicy


@pytest.mark.unit
def test_temporary_instruction_does_not_authorize_long_term_write() -> None:
    decision = MemoryPolicy().evaluate(
        message="这次回答简短一点",
        kind="preference",
        content="偏好简短回答",
    )

    assert decision.intent is MemoryIntent.NONE
    assert decision.allowed is False
    assert decision.code == "MEMORY_EXPLICIT_INTENT_REQUIRED"


@pytest.mark.unit
def test_long_term_phrase_and_save_verb_authorize_write() -> None:
    decision = MemoryPolicy().evaluate(
        message="以后都用中文回答，请记住",
        kind="preference",
        content="偏好使用中文回答",
    )

    assert decision.intent is MemoryIntent.SAVE
    assert decision.allowed is True
    assert decision.code is None


@pytest.mark.unit
def test_explicit_delete_authorizes_delete_intent() -> None:
    decision = MemoryPolicy().evaluate(
        message="请忘记这条偏好",
        kind="preference",
        content="偏好使用中文回答",
    )

    assert decision.intent is MemoryIntent.DELETE
    assert decision.allowed is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("支付卡号 6222021234567890", "payment_card"),
        ("验证码是 123456", "verification_code"),
        ("API Key 是 sk-secretvalue123", "api_key"),
        ("密码是 P@ssw0rd", "password"),
        ("身份证号 110101199001011234", "identity_document"),
        ("保存 chain-of-thought", "internal_reasoning"),
    ],
)
def test_sensitive_content_is_rejected_without_echoing_value(
    content: str,
    category: str,
) -> None:
    decision = MemoryPolicy().evaluate(
        message=f"以后请记住：{content}",
        kind="preference",
        content=content,
    )

    assert decision.intent is MemoryIntent.SAVE
    assert decision.allowed is False
    assert decision.code == "MEMORY_POLICY_REJECTED"
    assert decision.sensitive_category == category
    assert content not in decision.model_dump_json()
