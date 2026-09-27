import requests
import os
import json

# Load environment variables
STOCK_SCREENER_API_URL = os.environ.get("STOCK_SCREENER_API_URL", "http://localhost:8000") # Default to local development URL
STOCK_SCREENER_AUTH_TOKEN = os.environ.get("STOCK_SCREENER_AUTH_TOKEN") # If API requires authentication

# Define known endpoints based on README and previous discussions
# NOTE: These are *assumed* endpoints. Actual API structure needs verification.
# We will prioritize /api/v1/stocks/:ticker for detailed data.
# For scanning, we might need to call a general /scan endpoint and parse results, or specific /scans/* endpoints if they exist.
ENDPOINTS = {
    "VOLUME_BREAKOUT_SCAN": "/api/v1/scans", # Placeholder - requires checking screener API for exact scan endpoints
    "RELATIVE_STRENGTH_SCAN": "/api/v1/scans", # Placeholder
    "STOCK_DETAIL": "/api/v1/stocks/{ticker}",
    "THEMES": "/api/v1/themes",
    "ASSISTANT": "/api/v1/assistant",
    "MARKET_BREADTH": "/api/v1/breadth", # Example endpoint
    "GROUP_RANKINGS": "/api/v1/groups", # Example endpoint
}

def get_screener_api_url(endpoint_key: str, **kwargs) -> str:
    """Constructs the full API URL for a given endpoint key."""
    if not STOCK_SCREENER_API_URL:
        raise ValueError("STOCK_SCREENER_API_URL environment variable not set.")

    endpoint_path = ENDPOINTS.get(endpoint_key)
    if not endpoint_path:
        raise ValueError(f"Unknown endpoint key: {endpoint_key}")

    # Format the path with any provided keyword arguments (e.g., ticker)
    try:
        full_url = f"{STOCK_SCREENER_API_URL.rstrip('/')}{endpoint_path.format(**kwargs)}"
    except KeyError as e:
        raise ValueError(f"Missing argument for endpoint path {endpoint_path}: {e}")
    return full_url

def fetch_screener_data(endpoint_key: str, params: dict = None, **kwargs) -> dict:
    """Fetches data from the stock screener API.

    Args:
        endpoint_key: Key from ENDPOINTS dictionary (e.g., "STOCK_DETAIL").
        params: Dictionary of query parameters to send with the request.
        **kwargs: Arguments to format the endpoint path (e.g., ticker="AAPL").

    Returns:
        JSON response from the API.
    """
    url = get_screener_api_url(endpoint_key, **kwargs)
    
    headers = {}
    # Example: Using a custom token header. Adjust if the screener uses "Authorization: Bearer ..."
    if STOCK_SCREENER_AUTH_TOKEN:
        headers["X-Auth-Token"] = STOCK_SCREENER_AUTH_TOKEN

    try:
        print(f"Fetching data from: {url} with params: {params}")
        response = requests.get(url, params=params, headers=headers, timeout=60) # Increased timeout for robustness
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        return response.json()
    except requests.exceptions.Timeout:
        print(f"Request timed out for URL: {url}")
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from screener API: {e}")
        return {"error": str(e)}

def get_stocks_by_scan_criteria(criteria: str) -> list[dict]:
    """
    Fetches a list of stocks based on specific scan criteria (e.g., Volume Breakthrough, Relative Strength).
    NOTE: The actual endpoint for scanning needs to be determined from the screener