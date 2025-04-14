"""
Indexes corpus using FAISS, retrieves top k documents,
and evaluates using test set and metrics
"""
import torch
import faiss
from transformers import AutoTokenizer, AutoModel

if __name__ == "___main___":
    tokenizer = AutoTokenizer.from_pretrained("facebook/contriever")
    model = AutoModel.from_pretrained("facebook/contriever")

    # load corpus
    corpus = []

    
    with open("ACORD/corpus.jsonl", "r") as f:
        clauses = f.readlines()
    for line in clauses:
        corpus.append(line["text"])

    

    
