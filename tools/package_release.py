"""Package only the public executable, README and synthetic examples."""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def app_version():
    tree = ast.parse((ROOT / "app/qxqy_direct_translator_gui.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "APP_VERSION" for t in node.targets):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+", version):
                return version
    raise ValueError("APP_VERSION must contain a release version such as v1.07")


def package(executable: Path, output: Path):
    from PyInstaller.archive.readers import CArchiveReader

    if not executable.is_file():
        raise FileNotFoundError(executable)
    archive = CArchiveReader(str(executable))
    forbidden = [name for name in archive.toc if
                 name.lower().endswith((".csv", ".tsv", "settings.json", ".env"))
                 or "termtable" in name.lower()]
    if forbidden:
        raise ValueError(f"Private data found in executable: {forbidden}")
    modules = archive.open_embedded_archive("PYZ.pyz").toc
    required = {"carbon_theme", "custom_glossary", "ui_helpers", "llm_stage2", "prompts"}
    if not required.issubset(modules):
        raise ValueError("Executable is missing application modules")
    version = app_version()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"miliastra-translator-direct-{version}-windows-x64"
    public_exe = output / f"{stem}.exe"
    shutil.copyfile(executable, public_exe)
    bundle = output / f"{stem}.zip"
    # An explicit allowlist prevents local inputs, settings or glossaries leaking.
    included = ["README.md", "examples/glossary.csv", "examples/glossary-custom.tsv", "examples/columns.json"]
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.write(public_exe, "QXQY_Direct_Translator.exe")
        for name in included:
            zipped.write(ROOT / name, name)
    expected = {"QXQY_Direct_Translator.exe", *included}
    with zipfile.ZipFile(bundle) as zipped:
        if set(zipped.namelist()) != expected or zipped.testzip() is not None:
            raise ValueError("Release ZIP validation failed")
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in (public_exe, bundle)
    ), encoding="utf-8")
    return public_exe, bundle, checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/release/QXQY_Direct_Translator.exe")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.version:
        print(app_version())
        return
    for artifact in package(args.exe, args.out or ROOT / "release" / app_version()):
        print(artifact)


if __name__ == "__main__":
    main()
