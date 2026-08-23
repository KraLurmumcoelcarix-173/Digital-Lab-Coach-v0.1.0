"""The student release zip: right contents, nothing that shouldn't ship."""

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_release_zip_contents(tmp_path):
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_release_zip.py"),
         "--out", str(tmp_path)],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    out = tmp_path / "DigitalLabCoach.zip"
    assert out.exists()
    z = zipfile.ZipFile(out)
    names = z.namelist()

    assert all(n.startswith("DigitalLabCoach-") for n in names)
    for frag in ("dlc/web/server.py", "proxy/dlc_proxy.py",
                 "prompts/", "data/sample_circuits/",
                 "data/official_tests_defaults.json",
                 "START_HERE.bat", "start.sh", "UNINSTALL.bat",
                 "uninstall.sh", "README.md", "uv.lock",
                 "docs/RELEASE_GUIDE.md"):
        assert any(frag in n for n in names), f"missing {frag}"

    for bad in ("/tests/", "__pycache__", ".venv", ".git/",
                ".pytest_cache", ".pyc"):
        assert not any(bad in n for n in names), f"shipped {bad}"

    sh = next(i for i in z.infolist() if i.filename.endswith("/start.sh"))
    assert (sh.external_attr >> 16) & 0o111, "start.sh lost its exec bit"
