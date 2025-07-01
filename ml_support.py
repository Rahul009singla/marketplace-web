# ml_support.py
import json
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

with open('faq_data.json') as f:
    data = json.load(f)

faq_data = data['deposit'] + data['orders'] + data['policy']
faq_questions = [q for q, a in faq_data]
faq_answers = [a for q, a in faq_data]

model = SentenceTransformer('all-MiniLM-L6-v2')
faq_embeddings = model.encode(faq_questions)
