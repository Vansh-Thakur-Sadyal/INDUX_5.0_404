import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVICES = {
    "backend": [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.api.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8001",
    ],
    "frontend": [
        sys.executable,
        "-m",
        "http.server",
        "8000",
        "--directory",
        str(ROOT / "frontend"),
    ],
}


def main():
    processes = {}
    try:
        for name, command in SERVICES.items():
            processes[name] = subprocess.Popen(command, cwd=ROOT)
            print(f"Starting {name}: {' '.join(command)}", flush=True)

        print("Frontend: http://127.0.0.1:8000/index.html", flush=True)
        print("Backend docs: http://127.0.0.1:8001/docs", flush=True)
        print("Press Ctrl+C to stop both servers.", flush=True)

        while True:
            for name, process in processes.items():
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"The {name} server stopped with exit code {return_code}."
                    )
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping the farm application...", flush=True)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
