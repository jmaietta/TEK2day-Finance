"""The SHA-pin check: third-party actions must be pinned, our own workflows need not be.

From 2026-10-04 the nightly architecture check emailed a drift every morning:
"deploy-api.yml:55 action is not SHA-pinned: ./.github/workflows/tests.yml".
That line is the test gate (d7fabce) calling this repo's own workflow file, which
runs as it stands at the deploying commit and cannot be repointed by anyone.

No network: the workflow text is handed in, never fetched.
"""
import architecture_check

PINNED = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4"
SPEC = {"github_actions_must_be_sha_pinned": True}


def _drift_for(workflow_text, monkeypatch):
    monkeypatch.setattr(architecture_check, "repo_file", lambda _path: workflow_text)
    monkeypatch.setattr(architecture_check, "drift", [])
    architecture_check.check_action_pins(SPEC)
    return architecture_check.drift


def test_our_own_workflow_is_not_reported(monkeypatch):
    text = "jobs:\n  tests:\n    uses: ./.github/workflows/tests.yml\n"
    assert _drift_for(text, monkeypatch) == []


def test_a_sha_pinned_action_is_not_reported(monkeypatch):
    assert _drift_for(f"      - uses: {PINNED}\n", monkeypatch) == []


def test_a_moving_tag_is_still_reported(monkeypatch):
    drift = _drift_for("      - uses: actions/checkout@v4\n", monkeypatch)
    assert len(drift) == 2  # once per workflow checked
    assert "actions/checkout@v4" in drift[0]


def test_an_unpinned_action_with_no_ref_is_still_reported(monkeypatch):
    assert len(_drift_for("      - uses: actions/checkout\n", monkeypatch)) == 2


def test_the_real_deploy_workflows_are_clean(monkeypatch):
    monkeypatch.setattr(architecture_check, "drift", [])
    architecture_check.check_action_pins(SPEC)
    assert architecture_check.drift == []
