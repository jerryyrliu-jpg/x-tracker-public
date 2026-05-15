import sys
import os
from sentence_transformers import SentenceTransformer
import torch

class UniversalEmbedder:
    """
    Handles local embeddings using nomic-embed-text via sentence-transformers.
    """
    def __init__(self, model_name='nomic-ai/nomic-embed-text-v1'):
        self.model_name = model_name
        self.model = None
        self._init_model()

    def _init_model(self):
        try:
            print(f"Loading embedding model: {self.model_name}...")
            # trust_remote_code=True runs arbitrary Python from the HuggingFace model repo.
            # Pin a revision SHA via EMBEDDING_MODEL_REVISION env var for supply-chain security.
            # Get SHA via: huggingface-cli tag nomic-ai/nomic-embed-text-v1
            _revision = os.getenv("EMBEDDING_MODEL_REVISION") or None
            _kwargs = {"trust_remote_code": True}
            if _revision:
                _kwargs["revision"] = _revision
            self.model = SentenceTransformer(self.model_name, **_kwargs)
            print("Model loaded successfully.")
        except Exception as e:
            print(f"Error loading model: {e}")
            # Fallback to a simpler model if nomic fails
            fallback_model = 'sentence-transformers/all-MiniLM-L6-v2'
            print(f"Attempting fallback to: {fallback_model}...")
            self.model = SentenceTransformer(fallback_model)
            print("Fallback model loaded.")

    def embed_texts(self, texts):
        """
        Convert a list of texts to embeddings.
        """
        if not self.model:
            raise RuntimeError("Model not loaded.")
        
        # Nomic requires a prefix for search tasks
        processed_texts = [f"search_document: {t}" for t in texts]
        embeddings = self.model.encode(processed_texts, convert_to_numpy=True)
        return embeddings

    def embed_query(self, query):
        """
        Convert a search query to an embedding.
        """
        if not self.model:
            raise RuntimeError("Model not loaded.")
        
        # Nomic requires a prefix for queries
        processed_query = f"search_query: {query}"
        embedding = self.model.encode([processed_query], convert_to_numpy=True)[0]
        return embedding

if __name__ == "__main__":
    # Test embedding
    embedder = UniversalEmbedder()
    test_texts = ["TSMC provides advanced packaging for NVIDIA.", "CPO is a key technology for AI networking."]
    embeddings = embedder.embed_texts(test_texts)
    print(f"Generated {len(embeddings)} embeddings of dimension {len(embeddings[0])}")
    
    query_emb = embedder.embed_query("Who supplies CPO components?")
    print(f"Generated query embedding of dimension {len(query_emb)}")
