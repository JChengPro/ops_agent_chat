from app.runtime.verification import verification_window


def test_runtime_changes_require_consecutive_observations():
    assert verification_window("service.restart", max_attempts=3, required_consecutive=2) == (3, 2)
    assert verification_window("deployment.apply_registered", max_attempts=4, required_consecutive=3) == (4, 3)


def test_non_temporal_config_hash_verification_runs_once():
    assert verification_window("config.update_registered", max_attempts=3, required_consecutive=2) == (1, 1)


def test_required_successes_cannot_exceed_attempts():
    assert verification_window("service.start", max_attempts=2, required_consecutive=5) == (2, 2)
