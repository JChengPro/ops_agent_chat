"""Keep collectors, monitoring, expiry and lease recovery independent of broker consumers."""
from app.worker import main

if __name__ == "__main__":
    main(maintenance_only=True)
