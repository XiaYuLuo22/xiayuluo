from tools.report_ci_tests import annotations


def test_ci_annotations_report_failures_and_errors_without_inventing_passes(tmp_path):
    report = tmp_path / "tests.xml"
    report.write_text('<testsuites><testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase>'
        '<testcase classname="windows" name="broken,case"><failure>AssertionError: 5%\nsecond line</failure></testcase>'
        '<testcase name="setup"><error message="Setup failed"/></testcase></testsuite></testsuites>')
    result = list(annotations(report))
    assert len(result) == 2
    assert result[0] == '::error title=windows.broken%2Ccase::AssertionError: 5%25%0Asecond line'
    assert result[1].endswith('::Setup failed')
