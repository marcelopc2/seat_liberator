import re
from settings import BASE_URL, API_TOKEN
import requests

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
})

def parse_course_ids(raw_text: str) -> list[str]:
    # separa por coma, espacio o salto de línea
    if not raw_text:
        return []
    ids = re.split(r"[,\s]+", raw_text.strip())
    # solo dígitos
    return [i for i in ids if i.isdigit()]

def fetch_canvas_api(endpoint, params=None):
    full_url = f"{BASE_URL}{endpoint}"
    results = []

    response = session.get(full_url, params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        return data

    results.extend(data)
    while response.links.get("next"):
        url = response.links["next"]["url"]
        response = session.get(url)
        response.raise_for_status()
        results.extend(response.json())

    return results
