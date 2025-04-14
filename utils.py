import pandas as pd

# mean pooling for embeddings (normalize and use cosine similarity for MIPS)
def mean_pooling_embedding_with_normalization(model, tokenizer, device):
    pass

"""
calculate label-star precision @ k for a specific query belonging to the test set
"""
def label_precision_k(label, query_id , results, k):
    test_data = pd.read_csv("ACORD/test.tsv", sep="\t")
    query_data = test_data[test_data["query_id"] == query_id]
    #number of relevant documents
    relevant_docs = query_data[query_data["score"] == (label - 1)]
    num_relevant_docs = len(relevant_docs)
    #computer number of documents in results which have a rating >= label
    set_relevant = set(relevant_docs["corpus-id"])
    set_results = set(results)
    rec_and_relevant = len(set_results.intersection(set_relevant))


#calculate label-star recall @ k
def label_recall_k(y_true, y_pred, k):
    pass

#calculate ndcg @ k
def ndcg_k(y_true, y_pred, k):
    pass