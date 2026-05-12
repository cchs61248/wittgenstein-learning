from urllib.parse import quote_plus

import httpx


async def search_web(query: str, max_results: int = 3) -> list[dict]:
    """
    Lightweight web search fallback for out-of-source student questions.
    Uses DuckDuckGo Instant Answer API (no key required).
    """
    q = quote_plus(query.strip())
    url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    results: list[dict] = []
    abstract = (data.get("AbstractText") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    if abstract:
        results.append(
            {
                "title": data.get("Heading") or "DuckDuckGo Abstract",
                "snippet": abstract,
                "url": abstract_url or "https://duckduckgo.com/",
            }
        )

    for item in data.get("RelatedTopics", []):
        if isinstance(item, dict) and item.get("Text"):
            first_url = item.get("FirstURL") or "https://duckduckgo.com/"
            results.append(
                {
                    "title": item.get("Text", "")[:80],
                    "snippet": item.get("Text", ""),
                    "url": first_url,
                }
            )
        if len(results) >= max_results:
            break

    return results[:max_results]
