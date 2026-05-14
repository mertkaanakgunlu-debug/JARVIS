"""Sub-agent specialists for J.A.R.V.I.S. orchestration."""

from jarvis.subagents.math import build_math_agent
from jarvis.subagents.writer import build_writer_agent
from jarvis.subagents.research import build_research_agent
from jarvis.subagents.coder import build_coder_agent
from jarvis.subagents.geomath import build_geomath_agent  # Faz 18

__all__ = [
    "build_math_agent",
    "build_writer_agent",
    "build_research_agent",
    "build_coder_agent",
    "build_geomath_agent",
]
