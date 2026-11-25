from config import MODEL, ROOT  # first: loads .env before Andromeda is imported

from andromeda.config import AgentConfig, MiddlewareConfig
from andromeda.core import Agent
from andromeda.core.middleware.skills import SkillsMiddleware


def make_agent(name: str, role: str, skill: str) -> Agent:
    """A specialist whose tools are unlocked by its own skill.

    The agent is given no tools directly. Its tools are registered by name when its module is
    imported, and become available only after the model calls load_skill(skill): the skill's
    `allowed-tools` header is what grants them.
    """
    skills = SkillsMiddleware(backend="filesystem", repo_root=ROOT, sources=[f"/skills/{skill}"])
    return Agent(
        AgentConfig(
            name=name,
            model=MODEL,
            prompt=(
                f"{role}\n"
                f"Before anything else call load_skill('{skill}') and follow it exactly.\n"
                "Use only facts returned by your tools; never invent ids, dates or amounts.\n"
                "Finish with a short factual summary of what you found (max 100 words)."
            ),
            middleware=MiddlewareConfig(enabled=True, tool_error_handler=True, custom=[skills]),
            recursion_limit=80,
        )
    )
