from pathlib import Path

from tools.check_release_secrets import REQUIRED, main


def test_missing_signing_secrets_fail_before_build_or_publish(capsys):
    assert main({}) == 1
    output = capsys.readouterr().out
    assert all(name in output for name in REQUIRED)
    assert output.count('::error::') == 3


def test_secret_values_are_never_reported_even_on_partial_failure(capsys):
    values = {name: f'private-value-{index}' for index, name in enumerate(REQUIRED)}
    assert main(values) == 0
    output = capsys.readouterr().out
    assert not any(value in output for value in values.values())
    values[REQUIRED[1]] = '  '
    assert main(values) == 1
    output = capsys.readouterr().out
    assert REQUIRED[1] in output and REQUIRED[0] not in output
    assert 'private-value' not in output


def test_both_release_builds_require_signing_preflight():
    workflow = (Path(__file__).parents[1] / '.github/workflows/release.yml').read_text()
    assert 'python3 tools/check_release_secrets.py' in workflow
    for job in ('windows', 'android'):
        assert f'  {job}:\n    needs: preflight\n' in workflow
    assert 'needs: [windows, android]' in workflow
