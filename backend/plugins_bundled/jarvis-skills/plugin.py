"""Bundled plugin: use_skill - lazy-loads full SKILL.md body for any engine."""

from __future__ import annotations


def register(ctx) -> None:
    def use_skill(args: dict, brain: str) -> str:
        slug = str(args.get("slug") or "").strip().lower()
        if not slug:
            return "ERROR: slug is required."
        skill_store = ctx.services.get("skills")
        if skill_store is None:
            return "ERROR: SkillStore service unavailable."
        body = skill_store.read_skill_body(brain, slug)
        if body is None:
            return f"ERROR: Skill '{slug}' not found in brain '{brain}'."
        return body

    ctx.register_tool(
        name="use_skill",
        description="Load the full instructions of an enabled skill by its slug.",
        handler=use_skill,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Skill slug from the AVAILABLE SKILLS list"}
            },
            "required": ["slug"],
        },
    )
