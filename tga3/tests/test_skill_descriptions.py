from tga3.skills import SkillCatalog, SkillFile


def test_skill_description_uses_standard_frontmatter():
    files = (SkillFile("SKILL.md", "---\nname: web\ndescription: >-\n  Web techniques\n  on demand\n---\n# Web"),)
    assert SkillCatalog._description("web", files) == "Web techniques on demand"


def test_skill_description_falls_back_to_body_for_invalid_metadata():
    for metadata in ("description: [", "description: [not, text]", "name: web"):
        files = (SkillFile("SKILL.md", f"---\n{metadata}\n---\n\n# Web techniques\n"),)
        assert SkillCatalog._description("web", files) == "Web techniques"
