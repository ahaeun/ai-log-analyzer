from openai import OpenAI


def analyze_error(event, config):
    try:
        client = OpenAI(api_key=config.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 Java/Spring Boot 에러를 분석하는 어시스턴트다. "
                        "에러 타입과 스택트레이스를 보고 원인과 해결방향을 "
                        "한국어로 간결하게 요약해라."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"에러 타입: {event.error_type}\n"
                        f"메시지: {event.message}\n"
                        f"스택트레이스:\n{event.stack_trace}"
                    ),
                },
            ],
            timeout=10,
        )
        return response.choices[0].message.content
    except Exception:
        return None
