"""Launch the full streaming pipeline: producer + worker as independent processes.
Run this in one terminal, the Streamlit dashboard in another — watch posts stream in
and get scored live.

Run:  python -m stream.run_stream
"""
import sys, subprocess, signal

def main():
    procs = [
        subprocess.Popen([sys.executable, "-m", "stream.producer"]),
        subprocess.Popen([sys.executable, "-m", "stream.worker"]),
    ]
    print("streaming pipeline up (producer + worker). Ctrl+C to stop both.")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        print("\nstreaming pipeline stopped.")

if __name__ == "__main__":
    main()
