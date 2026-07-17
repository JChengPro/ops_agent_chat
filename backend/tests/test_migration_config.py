from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_script_location_is_independent_of_working_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"

    scripts = ScriptDirectory.from_config(Config(str(config_path)))

    assert scripts.get_current_head() == "d7f2a9c4e681"
