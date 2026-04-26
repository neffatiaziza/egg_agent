#search_tools.py
from langchain.tools import tool
import httpx
from bs4 import BeautifulSoup
from readability import Document
from tavily import TavilyClient
import os

@tool
async def web_search_tool(query: str) -> list[dict]:
    """Search the web for current egg regulations, INNORPI/UNECE standards, Tunisian market prices, or food safety news. Uses Tavily."""
    try:
        tavily_api_key = os.environ.get("TAVILY_API_KEY")
        if not tavily_api_key:
            return [{"content": "TAVILY_API_KEY not found in environment. Please provide one.", "fallback": True}]
            
        client = TavilyClient(api_key=tavily_api_key)
        # Note: TavilyClient is synchronous by default
        response = client.search(query=query, search_depth="basic", max_results=5)
        
        results = response.get("results", [])
        
        if not results:
            return [{"content": "Tavily returned no results. Try again later.", "fallback": True}]
        
        formatted_results = [{"title": r.get("title"), "url": r.get("url"), "content": r.get("content")} for r in results]
        return formatted_results
    except Exception as e:
        return [{"content": f"Web search failed ({str(e)}). Please rely on your internal knowledge instead.", "fallback": True}]

@tool
async def article_fetcher(url: str) -> dict:
    """Fetch and read the full content of a webpage or article about egg quality, regulations, or market data"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            doc = Document(response.text)
            title = doc.title()
            html_content = doc.summary()
            
            # Strip tags
            soup = BeautifulSoup(html_content, "html.parser")
            clean_text = soup.get_text(separator=' ', strip=True)
            
            # Truncate to 6000 chars
            clean_text = clean_text[:6000]
            
            return {"url": url, "title": title, "content": clean_text, "success": True, "error": None}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "success": False, "error": str(e)}
