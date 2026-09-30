"""Prepare an explicitly marked synthetic source for installed native lifecycle tests.

Run with ``python -m tests.background_ui_acceptance "$PWD/.superpowers/UIAcceptanceFixtures/unique-name"``.
Use a nonsymlinked checkout: native manifest preparation rejects symlink ancestors.
The installed app still owns preparation, collection, resources and worker dispatch.
No result, qualification, campaign identity or runtime identity is fabricated.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

from src.research.day_trader_context import DayTraderContextProtocol
from src.research.round_two_quality import append_observations
from tests.background_research_fixtures import learning_fixture


def prepare(root: Path) -> None:
    root = root.resolve()
    if "UIAcceptanceFixtures" not in root.parts or root.exists():
        raise ValueError("Use a new, uniquely named UIAcceptanceFixtures directory")
    root.mkdir(parents=True)
    (root / "UI-TEST-ONLY.md").write_text(
        "SYNTHETIC UI ACCEPTANCE ONLY. Sparse synthetic receipts prove control flow, never an edge.\n"
    )
    campaign, protocol, observations = learning_fixture(root, training_count=1440, native_context=True)
    DayTraderContextProtocol(round_protocol=protocol)
    endpoint = observations[-1].model_copy(
        update={
            "source_key": "synthetic-native-endpoint",
            "provider_at": campaign.created_at - timedelta(minutes=1),
            "received_at": campaign.created_at - timedelta(seconds=59),
            "available_at": campaign.created_at - timedelta(seconds=58),
        }
    )
    append_observations(campaign.source_directory, protocol, (endpoint,))
    source = root / "PaperResearch/paper-desk-v1"
    source.parent.mkdir()
    campaign.source_directory.rename(source)
    (source / "UI-TEST-ONLY.md").write_text("Synthetic native training acceptance, not live performance.\n")
    (root / "paper-session.json").write_text(
        json.dumps(
            {
                "learningEnabled": False,
                "resumeOnLaunch": False,
                "showMenuBarExtra": False,
                "resourceProfile": "efficient",
                "seed": 42,
            }
        )
    )


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
