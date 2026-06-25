import subprocess
import sys
import os
from pathlib import Path
import threading
import time

# Global variable to track if streamlit has been started
_streamlit_process = None
_streamlit_lock = threading.Lock()

def _start_streamlit():
    """Start the Streamlit app in the background."""
    global _streamlit_process
    
    with _streamlit_lock:
        if _streamlit_process is not None:
            return
        
        root_dir = Path(__file__).parent.parent
        
        try:
            _streamlit_process = subprocess.Popen(
                [
                    sys.executable, "-m", "streamlit", "run",
                    str(root_dir / "app.py"),
                    "--server.port=8501",
                    "--server.address=0.0.0.0",
                    "--server.headless=true",
                    "--client.showErrorDetails=false"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"Error starting streamlit: {e}")

# Start streamlit when the handler is first called
_start_streamlit()

def handler(request):
    """Handler function for Vercel."""
    # Ensure streamlit is running
    if _streamlit_process is None or _streamlit_process.poll() is not None:
        _start_streamlit()
    
    return {
        "statusCode": 307,
        "headers": {
            "Location": "http://localhost:8501"
        }
    }
