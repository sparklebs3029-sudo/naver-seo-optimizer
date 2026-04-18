import os
import sys
import socket
import subprocess
import time
import webbrowser

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(PROJECT_DIR, "app.py")
PORT = 8501

# pythonw.exe → python.exe (streamlit 실행용)
python_exe = sys.executable.replace("pythonw.exe", "python.exe")
if not os.path.exists(python_exe):
    python_exe = sys.executable

def is_running():
    s = socket.socket()
    try:
        return s.connect_ex(("localhost", PORT)) == 0
    finally:
        s.close()

if is_running():
    webbrowser.open(f"http://localhost:{PORT}")
else:
    subprocess.Popen(
        [python_exe, "-m", "streamlit", "run", APP_PATH,
         "--browser.gatherUsageStats", "false"],
        cwd=PROJECT_DIR,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    # Streamlit이 실제로 뜰 때까지 최대 20초 폴링
    for _ in range(40):
        time.sleep(0.5)
        if is_running():
            break
    webbrowser.open(f"http://localhost:{PORT}")
