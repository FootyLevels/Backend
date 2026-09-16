"""
Catalog Module
Read-only access to the skill and player definitions in config/skills.yaml.
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "skills.yaml"


class SkillCatalog:
    """Loads and queries the skill/player catalog.

    Usage:
        catalog = SkillCatalog()
        skill = catalog.get_skill('cruyff_turn')
        players = catalog.list_players()
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: Path to skills.yaml. Defaults to config/skills.yaml.
        """
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config = self._load()

    def _load(self) -> dict:
        """Read the YAML catalog, tolerating a missing file."""
        if not self.config_path.exists():
            return {"skills": {}, "players": {}, "categories": [], "progression": {}}
        with open(self.config_path, "r") as f:
            return yaml.safe_load(f) or {}

    # -- skills -----------------------------------------------------------

    @property
    def skills(self) -> Dict[str, dict]:
        return self.config.get("skills", {})

    def list_skills(self) -> List[dict]:
        """Every skill, each with its id attached."""
        return [{"id": sid, **cfg} for sid, cfg in self.skills.items()]

    def get_skill(self, skill_id: str) -> dict:
        """One skill by id.

        Raises:
            KeyError: If the skill is not defined.
        """
        if skill_id not in self.skills:
            raise KeyError(f"Unknown skill: {skill_id}")
        return {"id": skill_id, **self.skills[skill_id]}

    def skills_by_difficulty(self, difficulty: str) -> List[dict]:
        """Skills filtered to one difficulty level."""
        return [s for s in self.list_skills() if s.get("difficulty") == difficulty]

    def skills_for_player(self, player_id: str) -> List[dict]:
        """Skills attributed to one player."""
        return [s for s in self.list_skills() if s.get("player_id") == player_id]

    # -- players ----------------------------------------------------------

    @property
    def players(self) -> Dict[str, dict]:
        return self.config.get("players", {})

    def list_players(self) -> List[dict]:
        """Every player, each with its id and owned skill ids attached."""
        return [
            {
                "id": pid,
                **cfg,
                "skills": [s["id"] for s in self.skills_for_player(pid)],
            }
            for pid, cfg in self.players.items()
        ]

    def get_player(self, player_id: str) -> dict:
        """One player by id.

        Raises:
            KeyError: If the player is not defined.
        """
        if player_id not in self.players:
            raise KeyError(f"Unknown player: {player_id}")
        return {
            "id": player_id,
            **self.players[player_id],
            "skills": [s["id"] for s in self.skills_for_player(player_id)],
        }

    def players_by_category(self, category: str) -> List[dict]:
        """Players filtered to one category (e.g. 'legend', 'current')."""
        return [p for p in self.list_players() if p.get("category") == category]

    # -- browsing ---------------------------------------------------------

    @property
    def categories(self) -> List[dict]:
        return self.config.get("categories", [])

    @property
    def progression(self) -> Dict[str, List[str]]:
        return self.config.get("progression", {})
