#rag_tools.py
from langchain.tools import tool
import chromadb
from chromadb.utils import embedding_functions
import os

from dotenv import load_dotenv
load_dotenv()

PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")

# Initialize ChromaDB client
try:
    client = chromadb.PersistentClient(path=PERSIST_DIR)
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
except Exception:
    client = None
    sentence_transformer_ef = None

def get_or_create_collection(name="egg_regulations"):
    if client is None:
        return None
    return client.get_or_create_collection(
        name=name,
        embedding_function=sentence_transformer_ef
    )

def _ingest_initial_data():
    collection = get_or_create_collection()
    if collection is None:
        return
    if collection.count() == 0:
        docs = [
            "UNECE EGG-1 key rules: size classes (XL>=73g, L=63-73g, M=53-63g, S<53g), Grade A requirements (clean shell, uncracked, normal shape, air cell <=6mm, yolk centered, no blood/meat spots), Grade B (minor defects allowed, industrial use), Grade C (major defects, processing only)",
            "EU 589/2008: Class A eggs must be clean, uncracked; stamped with producer code; cannot be washed; candling required for Class A",
            "INNORPI NT 32.04: Tunisian standard, similar to UNECE EGG-1, mandatory for domestic market, eggs must be labeled in Arabic",
            "Codex Alimentarius: Salmonella control, temperature chain, max 28 days from lay date for retail"
        ]
        ids = ["doc_1", "doc_2", "doc_3", "doc_4"]
        collection.add(documents=docs, ids=ids)

@tool
async def regulatory_rag_tool(query: str) -> dict:
    """Query the local vector database for information regarding egg regulations, such as UNECE EGG-1, INNORPI, or EU standards."""
    try:
        collection = get_or_create_collection()
        if collection is None:
            return {"error": "chromadb not initialized", "fallback": True}
            
        if collection.count() == 0:
            _ingest_initial_data()
            
        results = collection.query(
            query_texts=[query],
            n_results=2
        )
        
        relevant_sections = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                relevant_sections.append({
                    "source": results['ids'][0][i],
                    "content": doc,
                    "score": results['distances'][0][i] if results['distances'] else 0.0
                })
        
        summary = "Based on local knowledge base: " + " | ".join([s['content'] for s in relevant_sections])
        
        return {
            "relevant_sections": relevant_sections,
            "answer_summary": summary
        }
    except Exception as e:
        return {"error": str(e), "fallback": True}
