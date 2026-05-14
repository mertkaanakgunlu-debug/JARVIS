"""Flash-Lite ile oturum ozetleme (Faz 13-A).

Bir oturum archive edildiginde (kullanici /reset yaptiginda) veya
baslangicta backfill icin cagrilir. Fire-and-forget async task olarak
agent._bg_tasks icerisinde yasatilir.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

_PROMPT = (
    "Summarize this conversation in 3-4 sentences for long-term memory recall.\n"
    "Focus on:\n"
    "- What the user wanted (concrete task or topic)\n"
    "- Named entities (people, projects, files, organizations) mentioned\n"
    "- The outcome or decision (if any)\n\n"
    "Skip pleasantries. Write in the SAME language the user used. Keep under 400 chars."
)


async def summarize_session(messages: list, settings) -> str:
    """Return a 3-4 sentence summary of the session, or '' if not enough content.

    Args:
        messages: list of LangChain BaseMessage objects (SystemMessage items skipped).
        settings: JarvisAgent.settings (needs gemini_api_key).

    Returns:
        Summary string, or '' if conversation too short / API error.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    convo_lines: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        content = m.content if isinstance(m.content, str) else str(m.content)
        if content.strip():
            convo_lines.append(f"{role}: {content[:600]}")

    if len(convo_lines) < 2:
        return ""

    body = "\n".join(convo_lines[-40:])  # cap context at last 40 lines

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        google_api_key=settings.gemini_api_key,
        max_output_tokens=300,
        temperature=0.2,
    )
    result = await llm.ainvoke(_PROMPT + "\n\n" + body)
    return (result.content or "").strip()
