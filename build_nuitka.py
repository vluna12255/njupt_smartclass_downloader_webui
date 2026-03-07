import os
import sys
import subprocess
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
MAIN_SCRIPT = PROJECT_ROOT / "app" / "server.py"
OUTPUT_DIR = PROJECT_ROOT / "dist_nuitka"

def build():

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    TARGET_EXE_NAME = "SmartClassDownloader"

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        f"--output-dir={OUTPUT_DIR}",
        "--windows-console-mode=force",
        "--assume-yes-for-downloads",
        "--msvc=latest",
        f"--jobs={os.cpu_count() or 4}",
        "--show-progress",
        f"--output-filename={TARGET_EXE_NAME}",
        "--windows-icon-from-ico=exe.ico", 

        "--module-name-choice=runtime",

        "--lto=yes",
        "--python-flag=no_docstrings",
        "--python-flag=-O",
        "--enable-plugin=anti-bloat",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=test",
        "--nofollow-import-to=tests",
        "--nofollow-import-to=setuptools",
        "--nofollow-import-to=pip",
        "--nofollow-import-to=wheel",
        "--nofollow-import-to=distutils",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=pdb",
        "--nofollow-import-to=pydoc",
        "--nofollow-import-to=doctest",
        "--nofollow-import-to=numpy",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=numba",
        "--nofollow-import-to=curses",
        "--nofollow-import-to=xmlrpc",
        "--nofollow-import-to=pyinstaller",
        "--nofollow-import-to=flask",
        "--nofollow-import-to=pyopenssl",

        "--include-package=app",
        "--include-package=src",

        "--nofollow-import-to=plugins",

        "--include-module=uvicorn.logging",
        "--include-module=uvicorn.loops.auto",
        "--include-module=uvicorn.protocols.http.auto",
        "--include-module=uvicorn.protocols.websockets.auto",
        "--include-module=uvicorn.lifespan.on",
        "--include-module=fastapi",
        "--include-module=jinja2",
        "--include-module=requests",
        "--include-module=lxml.etree",
        "--include-module=Crypto.Cipher.AES",
        "--include-module=pytz",
        "--include-package=anyio",
        
        "--remove-output",
        str(MAIN_SCRIPT)
    ]
    
    try:
        print("=" * 70)
        print("开始编译...")
        print("=" * 70)
        
        subprocess.run(cmd, check=True)
        
        print("\n" + "=" * 70)
        print(f"编译完成！输出目录: {OUTPUT_DIR}")
        print("=" * 70)
        
        return 0
    except subprocess.CalledProcessError as e:
        print("\n" + "=" * 70)
        print("编译失败")
        print("=" * 70)
        print(f"错误: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(build())