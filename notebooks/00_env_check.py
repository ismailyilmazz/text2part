import subprocess
import sys

subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "git+https://github.com/ismailyilmazz/text2part.git@main"],
    check=True,
)
subprocess.run([sys.executable, "-m", "text2part.envcheck", "--gpu"], check=False)
