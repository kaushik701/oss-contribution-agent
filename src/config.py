"""
config.py — Load watchlist.yaml and profile.yaml into typed structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WATCHLIST_PATH = ROOT / "watchlist.yaml"
PROFILE_PATH = ROOT / "profile.yaml"


@dataclass
class RepoConfig:
    repo: str
    priority: str
    good_labels: list[str]
    skip_labels: list[str]
    notes: str = ""


@dataclass
class Watchlist:
    repositories: list[RepoConfig]
    scoring: dict
    weights: dict


def load_watchlist() -> Watchlist:
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    repos = [
        RepoConfig(
            repo=r["repo"],
            priority=r.get("priority", "medium"),
            good_labels=r.get("good_labels", []),
            skip_labels=r.get("skip_labels", []),
            notes=r.get("notes", ""),
        )
        for r in data.get("repositories", [])
    ]
    return Watchlist(
        repositories=repos,
        scoring=data.get("scoring", {}),
        weights=data.get("weights", {}),
    )


@dataclass
class Profile:
    name: str
    github_username: str
    skills: dict[str, list[str]] = field(default_factory=dict)
    exclusions: list[str] = field(default_factory=list)
    preferences: dict = field(default_factory=dict)


def load_profile() -> Profile:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cand = data.get("candidate", {})
    return Profile(
        name=cand.get("name", "Contributor"),
        github_username=cand.get("github_username", ""),
        skills=data.get("skills", {}),
        exclusions=data.get("exclusions", []),
        preferences=data.get("preferences", {}),
    )
