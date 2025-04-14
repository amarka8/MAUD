"""
define a retriever class using the tokenizer and embeddings
model associated with FAISS
"""
class Retriever:
    def __init__(self, model, tokenizer, device = 'cuda'):
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
    def retrieve(self, query, k):
        pass
