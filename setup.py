from pathlib import Path
from typing import List
from setuptools import find_packages, setup


def load_requirements(path: str) -> List[str]:
    req_file = Path(__file__).parent / path
    requirements: List[str] = []
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirements.append(line)
    return requirements


setup(
    name="LaTeXTrans",
    version="0.1.13",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=load_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "latextrans=main:main",
            "latextrans-gui=src.gui.launcher:main",
        ],
    },
)
