"""
Tests for catalog module.

Runs against the real config/skills.yaml so drift between the catalog and the
code that reads it shows up here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.catalog import SkillCatalog


@pytest.fixture(scope="module")
def catalog() -> SkillCatalog:
    return SkillCatalog()


class TestSkills:
    def test_catalog_is_not_empty(self, catalog):
        assert len(catalog.skills) > 0

    def test_list_skills_attaches_ids(self, catalog):
        assert all("id" in skill for skill in catalog.list_skills())

    def test_get_known_skill(self, catalog):
        skill = catalog.get_skill("cruyff_turn")

        assert skill["id"] == "cruyff_turn"
        assert skill["name"] == "Cruyff Turn"

    def test_get_unknown_skill_raises(self, catalog):
        with pytest.raises(KeyError):
            catalog.get_skill("bicycle_kick")

    def test_every_skill_has_display_fields(self, catalog):
        """The frontend reads these on every card, so none may be missing."""
        for skill in catalog.list_skills():
            assert skill.get("name"), f"{skill['id']} has no name"
            assert skill.get("difficulty"), f"{skill['id']} has no difficulty"

    def test_difficulties_are_from_the_known_set(self, catalog):
        valid = {"beginner", "intermediate", "advanced"}

        for skill in catalog.list_skills():
            assert skill["difficulty"] in valid, f"{skill['id']} has an odd difficulty"

    def test_filter_by_difficulty(self, catalog):
        beginners = catalog.skills_by_difficulty("beginner")

        assert len(beginners) > 0
        assert all(s["difficulty"] == "beginner" for s in beginners)

    def test_scoring_fields_are_gone(self, catalog):
        """Scoring was removed — no checkpoint or threshold data should remain."""
        removed = {"checkpoints", "key_joints"}

        for skill in catalog.list_skills():
            assert not removed & set(skill), f"{skill['id']} still carries scoring config"


class TestPlayers:
    def test_catalog_has_players(self, catalog):
        assert len(catalog.players) > 0

    def test_get_known_player(self, catalog):
        player = catalog.get_player("cruyff")

        assert player["name"] == "Johan Cruyff"
        assert "cruyff_turn" in player["skills"]

    def test_get_unknown_player_raises(self, catalog):
        with pytest.raises(KeyError):
            catalog.get_player("pele")

    def test_filter_by_category(self, catalog):
        legends = catalog.players_by_category("legend")

        assert len(legends) > 0
        assert all(p["category"] == "legend" for p in legends)


class TestReferentialIntegrity:
    def test_every_skill_player_id_resolves(self, catalog):
        """A skill pointing at a missing player would break the API response."""
        for skill in catalog.list_skills():
            player_id = skill.get("player_id")
            if player_id:
                assert player_id in catalog.players, (
                    f"{skill['id']} references unknown player {player_id}"
                )

    def test_category_skill_ids_resolve(self, catalog):
        for category in catalog.categories:
            for skill_id in category.get("skills", []):
                assert skill_id in catalog.skills, (
                    f"category {category['id']} references unknown skill {skill_id}"
                )

    def test_progression_skill_ids_resolve(self, catalog):
        for level, skill_ids in catalog.progression.items():
            for skill_id in skill_ids:
                assert skill_id in catalog.skills, (
                    f"progression {level} references unknown skill {skill_id}"
                )


class TestMissingConfig:
    def test_missing_file_yields_an_empty_catalog(self, tmp_path):
        """A missing config must not crash the API on startup."""
        catalog = SkillCatalog(str(tmp_path / "nope.yaml"))

        assert catalog.skills == {}
        assert catalog.list_players() == []
