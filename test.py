import base64
import json
import mimetypes
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

IMAGE_PATH = "shared/output/screenshots/www.gov.uk/Home/Search/Transparency documents/Committee on Radioactive Waste Management terms of reference/interactions/navigation/041_c077_cookies.png"
MODEL_NAME = os.getenv("OLLAMA_MODEL", "llama3.2-vision")
OLLAMA_BASE_URL = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")


def image_to_base64(image_path: str) -> str:
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/png"

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return encoded


def build_prompt() -> str:
    return """
You are a senior UI/UX auditor.

Analyze this website screenshot professionally.
Detect the navigation bar buttons and name them.
Return STRICT JSON ONLY.
""".strip()


def main() -> None:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("OLLAMA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": MODEL_NAME,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
        "messages": [
            {
                "role": "user",
                "content": build_prompt(),
                "images": [image_to_base64(IMAGE_PATH)],
            }
        ],
    }

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        headers=headers,
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()

    content = data["message"]["content"]

    print("\n=== RESULT ===\n")
    print(content)

    try:
        parsed = json.loads(content)
        print("\n=== PARSED JSON ===\n")
        print(json.dumps(parsed, indent=2))
    except Exception:
        print("\nNot valid JSON")


if __name__ == "__main__":
    main()
