"""Baut das Plugin-ZIP für plugins.qgis.org und prüft es vorher.

Aufruf aus dem Plugin-Ordner (normales Python reicht, QGIS ist nicht nötig):

    python scripts/release.py           # prüfen und dist/qgisthwplugin-<version>.zip bauen
    python scripts/release.py --bump    # nächste freie Patch-Version in metadata.txt eintragen

Das ZIP entsteht mit ``git archive`` aus dem letzten Commit, damit es genau dem Stand im Repository
entspricht; ``.gitattributes`` hält Entwicklungsdateien heraus. Vorher wird geprüft:

- keine uncommitteten Änderungen (sonst landet der alte Stand im ZIP)
- die Version aus ``metadata.txt`` gibt es auf plugins.qgis.org noch nicht – auch eine blockierte
  Version belegt ihre Nummer
- ``changelog=`` in ``metadata.txt`` und ``CHANGELOG.md`` nennen die Version
- Bandit und detect-secrets finden im ZIP nichts (beides blockiert sonst die Version); Flake8 nur zur Info

Bandit, detect-secrets und Flake8 kommen aus ``requirements-dev.txt``.
"""

import argparse
import configparser
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Paketname auf plugins.qgis.org – nicht der Name des Repo-Ordners
PACKAGE = "qgisthwplugin"
VERSION_URL = "https://plugins.qgis.org/plugins/" + PACKAGE + "/version/{}/"
DIST_DIR = os.path.join(ROOT, "dist")
# Gehört nicht ins ZIP; fehlt eins davon nicht, stimmt .gitattributes nicht
_UNWANTED = ("/tmp/", "__pycache__", ".pyc", "/.git/", "/.claude/", ".log", "preview.gif")


class ReleaseError(Exception):
    pass


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} fehlgeschlagen: {result.stderr.strip()}")
    return result.stdout


def read_metadata(text: str) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser["general"]


def version_exists(version: str) -> bool | None:
    """Gibt es die Version auf plugins.qgis.org schon? ``None``, wenn die Seite nicht erreichbar ist."""
    request = urllib.request.Request(VERSION_URL.format(version), headers={"User-Agent": "thw-toolbox-release"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except urllib.error.URLError:
        return None


def next_patch(version: str) -> str:
    parts = [int(p) for p in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


def bump() -> None:
    """Nächste freie Patch-Version in ``metadata.txt`` eintragen (auch in der ersten Zeile von ``changelog=``)."""
    path = os.path.join(ROOT, "metadata.txt")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    current = read_metadata(text)["version"]
    candidate = next_patch(current)
    while True:
        exists = version_exists(candidate)
        if exists is None:
            raise ReleaseError("plugins.qgis.org nicht erreichbar – freie Version lässt sich nicht prüfen")
        if not exists:
            break
        print(f"  {candidate} gibt es schon")
        candidate = next_patch(candidate)
    text = re.sub(r"^version=.*$", f"version={candidate}", text, count=1, flags=re.M)
    text = re.sub(rf"^changelog={re.escape(current)}\s*$", f"changelog={candidate}", text, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"Version {current} -> {candidate} in metadata.txt eingetragen.")
    print("Jetzt den Changelog in metadata.txt und CHANGELOG.md ergänzen, committen und ohne --bump erneut starten.")


def check_sources() -> str:
    """Stand im letzten Commit prüfen; gibt die Version zurück."""
    pending = git("status", "--porcelain").rstrip()
    if pending:
        raise ReleaseError(
            "Uncommittete Änderungen – das ZIP wird aus dem letzten Commit gebaut, erst committen:\n" + pending
        )
    meta = read_metadata(git("show", "HEAD:metadata.txt"))
    version = meta["version"].strip()
    first_line = meta.get("changelog", "").strip().splitlines()[:1]
    if first_line != [version]:
        raise ReleaseError(f"changelog= in metadata.txt beginnt nicht mit {version}")
    if f"## [{version}]" not in git("show", "HEAD:CHANGELOG.md"):
        raise ReleaseError(f"CHANGELOG.md hat keinen Abschnitt „## [{version}]“")
    exists = version_exists(version)
    if exists is None:
        raise ReleaseError("plugins.qgis.org nicht erreichbar – ob die Version frei ist, bleibt offen")
    if exists:
        raise ReleaseError(
            f"Version {version} gibt es auf plugins.qgis.org schon (auch blockierte zählen). "
            "Mit --bump die nächste freie eintragen."
        )
    return version


def build(version: str) -> str:
    os.makedirs(DIST_DIR, exist_ok=True)
    target = os.path.join(DIST_DIR, f"{PACKAGE}-{version}.zip")
    git("archive", f"--prefix={PACKAGE}/", "-o", target, "HEAD")
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
        tops = {n.split("/")[0] for n in names}
        if tops != {PACKAGE}:
            raise ReleaseError(f"Oberster Ordner im ZIP ist {tops}, erwartet {PACKAGE}")
        zipped = read_metadata(z.read(f"{PACKAGE}/metadata.txt").decode("utf-8"))["version"].strip()
        if zipped != version:
            raise ReleaseError(f"metadata.txt im ZIP nennt {zipped}, erwartet {version}")
        unwanted = [n for n in names if any(u in n for u in _UNWANTED)]
        if unwanted:
            raise ReleaseError(f"Gehört nicht ins ZIP: {unwanted[:5]}")
        if f"{PACKAGE}/.flake8" not in names:
            raise ReleaseError(".flake8 fehlt im ZIP (der Scan von plugins.qgis.org liest sie)")
    print(f"ZIP: {target} ({os.path.getsize(target) / 1e6:.1f} MB, {len(names)} Einträge)")
    return target


def _tool(module: str, args: list[str], cwd: str) -> subprocess.CompletedProcess | None:
    result = subprocess.run(
        [sys.executable, "-m", module, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if "No module named" in result.stderr:
        print(f"  {module} fehlt – pip install -r requirements-dev.txt")
        return None
    return result


def scan(target: str) -> None:
    """Die Prüfungen von plugins.qgis.org nachstellen; Bandit- und Secrets-Funde brechen ab."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(target) as z:
            z.extractall(tmp)
        root = os.path.join(tmp, PACKAGE)

        result = _tool("bandit", ["-r", ".", "-q", "-f", "json"], root)
        if result is None:
            raise ReleaseError("Bandit nicht installiert – ohne Bandit-Prüfung kein Upload")
        findings = json.loads(result.stdout or "{}").get("results", [])
        for f in findings:
            print(f"  Bandit {f['test_id']}: {f['filename']}:{f['line_number']} {f['issue_text']}")
        if findings:
            raise ReleaseError(f"Bandit: {len(findings)} Fund(e) – plugins.qgis.org blockiert die Version")
        print("Bandit: keine Funde")

        result = _tool("detect_secrets", ["scan", "--all-files", "."], root)
        if result is None:
            raise ReleaseError("detect-secrets nicht installiert – ohne Secrets-Prüfung kein Upload")
        secrets = {path: items for path, items in json.loads(result.stdout)["results"].items() if items}
        for path, items in secrets.items():
            print(f"  Secret? {path}: {', '.join(i['type'] for i in items)}")
        if secrets:
            raise ReleaseError("detect-secrets: mögliche Zugangsdaten im ZIP")
        print("detect-secrets: keine Funde")

        result = _tool("flake8", [".", "--count"], root)
        if result is not None:
            count = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "0"
            print(f"Flake8: {count} Hinweis(e) (blockiert nicht)")


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bump", action="store_true", help="nächste freie Patch-Version eintragen")
    args = parser.parse_args()
    try:
        if args.bump:
            bump()
            return 0
        version = check_sources()
        print(f"Version {version} ist auf plugins.qgis.org frei.")
        target = build(version)
        scan(target)
    except ReleaseError as e:
        print(f"ABBRUCH: {e}")
        return 1
    print(f"\nFertig. Hochladen: https://plugins.qgis.org/plugins/{PACKAGE}/ -> „Add version“")
    return 0


if __name__ == "__main__":
    sys.exit(main())
