"""Every alert rule must reach the receiver its severity names: page -> page, ticket -> ticket."""

import re
import subprocess
import sys
from pathlib import Path

AMTOOL, CONFIG, RULES = sys.argv[1], sys.argv[2], Path(sys.argv[3])
EXPECTED = {"page": "page", "ticket": "ticket"}
text = RULES.read_text()
alerts = re.findall(r"- alert: (\w+)\n(?:(?!- alert:).*\n)*?\s+labels: \{severity: (\w+)\}", text)
assert len(alerts) == len(re.findall(r"- alert: ", text)) and alerts, "an alert rule without a severity label"
for name, severity in alerts:
    receiver = EXPECTED[severity]
    done = subprocess.run(
        [
            AMTOOL,
            "config",
            "routes",
            "test",
            f"--config.file={CONFIG}",
            f"--verify.receivers={receiver}",
            f"severity={severity}",
            f"alertname={name}",
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, (name, severity, done.stdout, done.stderr)
print(f"{len(alerts)} alert rules route to their severity's receiver")
