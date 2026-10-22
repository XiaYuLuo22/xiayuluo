"""Expose failed CI test reports as GitHub annotations without hiding pytest's exit code."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def annotations(path):
    for case in ET.parse(path).iter("testcase"):
        for kind in ("failure", "error"):
            failure = case.find(kind)
            if failure is not None:
                title = f"{case.get('classname', '')}.{case.get('name', '')}"
                message = (failure.text or failure.get("message", "Test failed"))[:12000]
                escape = lambda text: text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                yield f"::error title={escape(title).replace(',', '%2C')}::{escape(message)}"


if __name__ == "__main__":
    report = Path(sys.argv[1])
    if report.is_file():
        for annotation in annotations(report):
            print(annotation)
