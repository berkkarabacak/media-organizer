"""Master QA runner: executes every scenario module and prints one tally.

Run: QT_QPA_PLATFORM=offscreen .venv/Scripts/python tools/qa/run_all.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import qa_common  # noqa: E402
import qa_01_validation  # noqa: E402
import qa_02_filenames  # noqa: E402
import qa_03_content  # noqa: E402
import qa_04_flows  # noqa: E402
import qa_05_strategies  # noqa: E402
import qa_07_stress  # noqa: E402
import qa_09_abuse  # noqa: E402
import qa_10_edge  # noqa: E402

MODULES = (qa_01_validation, qa_02_filenames, qa_03_content, qa_04_flows,
           qa_05_strategies, qa_07_stress, qa_09_abuse, qa_10_edge)


def main():
    for module in MODULES:
        try:
            module.run()
        except Exception as exc:
            qa_common.check(f"{module.__name__}: UNCAUGHT EXCEPTION",
                            False, repr(exc))
    return qa_common.summary()


if __name__ == "__main__":
    sys.exit(main())
