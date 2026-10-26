from tools.report_ci_tests import annotations
import sys
from tools.run_ci_command import main


def test_ci_annotations_report_failures_and_errors_without_inventing_passes(tmp_path):
    report = tmp_path / "tests.xml"
    report.write_text('<testsuites><testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase>'
        '<testcase classname="windows" name="broken,case"><failure>AssertionError: 5%\nsecond line</failure></testcase>'
        '<testcase name="setup"><error message="Setup failed"/></testcase></testsuite></testsuites>')
    result = list(annotations(report))
    assert len(result) == 2
    assert result[0] == '::error title=windows.broken%2Ccase::AssertionError: 5%25%0Asecond line'
    assert result[1].endswith('::Setup failed')


def test_ci_command_preserves_exit_code_and_reports_failure_tail(capsys):
    assert main([sys.executable, '-c', 'print("controlled failure"); raise SystemExit(3)']) == 3
    assert '::error title=Windows build command failed::controlled failure%0A' in capsys.readouterr().out
    assert main([sys.executable, '-c', 'print("ok")']) == 0
    assert '::error' not in capsys.readouterr().out


def test_ci_annotation_keeps_the_final_error_within_github_limit(capsys):
    code = 'print("earlier analysis " * 1000); print("ACTUAL FINAL ERROR"); raise SystemExit(1)'
    assert main([sys.executable, '-c', code]) == 1
    annotation = capsys.readouterr().out.split('::error title=Windows build command failed::')[-1]
    assert annotation.endswith('ACTUAL FINAL ERROR%0A\n')
    assert len(annotation.encode('utf-8')) < 4096


def test_non_windows_ci_command_can_report_an_escaped_specific_title(capsys):
    assert main([sys.executable, '-c', 'print("package not available"); raise SystemExit(1)'],
                title='Vision dependency, failed\nnext:') == 1
    assert '::error title=Vision dependency%2C failed%0Anext%3A::package not available%0A' in capsys.readouterr().out
