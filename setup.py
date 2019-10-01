#!/usr/bin/env python3
from setuptools import setup
from pathlib import Path
import shutil

thisFile = Path(__file__).absolute()
thisDir = thisFile.parent
yumCliDir = thisDir / "yum-cli"

filez=set(thisDir.glob("*.py")) - {thisFile}
yumCliDir.mkdir(exist_ok=True)
for f in filez:
	shutil.copy(f, yumCliDir/f.name)

setup(use_scm_version=True)
