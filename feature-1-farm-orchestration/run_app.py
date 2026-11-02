import subprocess
import sys
import time
import urllib.error
import urllib.request
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


def service_is_ready(url):
    """Return whether a service already responds at its expected local URL."""
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main():
    processes = {}
    service_urls = {
        "backend": "http://127.0.0.1:8001/docs",
        "frontend": "http://127.0.0.1:8000/index.html",
    }
    try:
        for name, command in SERVICES.items():
            if service_is_ready(service_urls[name]):
                print(f"Reusing the already running {name}: {service_urls[name]}", flush=True)
                continue
            processes[name] = subprocess.Popen(command, cwd=ROOT)
            print(f"Starting {name}: {' '.join(command)}", flush=True)

        print("Frontend: http://127.0.0.1:8000/index.html", flush=True)
        print("Backend docs: http://127.0.0.1:8001/docs", flush=True)
        print("Keep this window open while using the app. Press Ctrl+C to stop servers started by this launcher.", flush=True)

        if not processes:
            while True:
                time.sleep(1)

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
