"""The public repository must never track Siemens data: only data/README.md and data/.gitkeep may live in data/."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which('git') is None or not (ROOT / '.git').exists(), reason='needs a Git checkout')
def test_only_the_data_folder_readme_and_keepfile_are_tracked():
    tracked = set(git('ls-files', 'data').stdout.split())
    assert tracked <= {'data/README.md', 'data/.gitkeep'}, tracked


@pytest.mark.skipif(shutil.which('git') is None or not (ROOT / '.git').exists(), reason='needs a Git checkout')
@pytest.mark.parametrize('name', ['data/Prod_Capstone_Data.xlsx', 'data/capstone.sqlite3', 'data/handoff/siemens_uat.sqlite3', 'data/anything.csv', 'data/new_folder/x.txt'])
def test_everything_else_in_data_is_ignored(name):
    assert git('check-ignore', '-q', name).returncode == 0, f'{name} would be committed'
    assert git('check-ignore', '-q', 'data/README.md').returncode == 1
