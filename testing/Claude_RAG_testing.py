import os
import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
import requests
from dotenv import load_dotenv


env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=env_path)


if not os.path.exists(env_path):
    print(f" no .env file found at {env_path}")

#Database_configuration
DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME", "your_db_name"),
    "user": os.environ.get("DB_USER", "your_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"
# how many chunks to retrieve via vector search
TOP_K = 5


print("Loading embedding model...")
embed_model = SentenceTransformer("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)


def embed_query(question):
    # Nomic requires a different prefix for queries vs stored documents
    return embed_model.encode("search_query: " + question).tolist()


#Retrieve closest chunks from document_chunks

# Common filler words to ignore when pulling likely programme-name keywords
# out of a student's question, for the keyword-matching fallback below.
STOPWORDS = {
    "what", "are", "is", "the", "for", "of", "in", "to", "a", "an", "and",
    "do", "does", "i", "need", "modules", "module", "requirements",
    "requirement", "admission", "programme", "program", "course", "degree",
    "year", "years", "will", "can", "you", "tell", "me", "about", "on",
    "with", "my", "please",
}


# Common short forms students actually type, mapped to how they appear in the handbook's section_title text.
SYNONYMS = {
    "maths": "mathematics",
    "math": "mathematics",
    "cs": "computer science",
    "it": "information technology",
    "stats": "statistics",
    "bio": "biology",
    "chem": "chemistry",
    "phys": "physics",
    "geo": "geography",
}


def extract_keywords(question):
    words = [w.strip(".,?!") for w in question.lower().split()]
    keywords = [w for w in words if w and w not in STOPWORDS]
    # Expand any known short forms (e.g. "maths" -> "mathematics")
    expanded = []
    for kw in keywords:
        expanded.append(SYNONYMS.get(kw, kw))
    return expanded


def vector_search(conn, query_vec, top_k):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chunk_id, section_title, chunk_text, page_number,
               embedding <=> %s::vector AS distance
        FROM document_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (query_vec, query_vec, top_k),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def keyword_search(conn, keywords, limit=5):
    """Fallback: directly match programme names in section_title, so a
    phrase like 'computer science and maths' reliably finds
    'COMPUTER SCIENCE AND MATHEMATICS (4BSC21)' even without the code."""
    if not keywords:
        return []
    cur = conn.cursor()
    # Build one ILIKE condition per keyword — all must appear in section_title
    conditions = " AND ".join(["section_title ILIKE %s"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords]
    cur.execute(
        f"""
        SELECT chunk_id, section_title, chunk_text, page_number, 0.0 AS distance
        FROM document_chunks
        WHERE {conditions}
        LIMIT %s
        """,
        (*params, limit),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def retrieve_chunks(conn, question, top_k=TOP_K):
    query_vec = embed_query(question)
    vector_rows = vector_search(conn, query_vec, top_k)

    keywords = extract_keywords(question)
    keyword_rows = keyword_search(conn, keywords)

    # Merge, de-duplicating by chunk_id (keep the vector-search version if both found it)
    seen_ids = set()
    merged = []
    for r in list(vector_rows) + list(keyword_rows):
        if r[0] not in seen_ids:
            seen_ids.add(r[0])
            merged.append(r)

    return [
        {"chunk_id": r[0], "section_title": r[1], "chunk_text": r[2],
         "page_number": r[3], "distance": r[4]}
        for r in merged
    ]


# Ask Groq, grounded in the retrieved chunks
def ask_claude(question, chunks):
    context = "\n\n---\n\n".join(
        f"[Source: {c['section_title']}, page {c['page_number']}]\n{c['chunk_text']}"
        for c in chunks
    )

    system_prompt = (
        "You are a helpful assistant answering questions about UNIZULU using "
        "ONLY the context provided below.\n\n"
        "Students will refer to programmes casually by name (e.g. 'computer "
        "science and maths', 'CS and stats') rather than by their official "
        "programme code (e.g. '4BSC21'). Match the student's wording against "
        "the programme names shown in the context's [Source: ...] labels "
        "yourself — NEVER ask the student to provide a programme code, and "
        "never require exact official phrasing. If the context contains "
        "multiple distinct programmes that could match what the student "
        "means, briefly list the matching programme names (by name, not "
        "code) and ask which one they mean.\n\n"
        "If the answer genuinely isn't in the context, say you don't have "
        "that information rather than guessing.\n\n"
        f"CONTEXT:\n{context}"
    )

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": question},
            ],
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Claude API error {response.status_code}: {response.text}")

    data = response.json()
    return "".join(block["text"] for block in data["content"] if block["type"] == "text")


# MAIN LOOP

def main():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        return

    conn = psycopg2.connect(**DB_CONFIG)
    register_vector(conn)
    print("Connected to database.")
    print("Type a question (or 'quit' to exit).\n")

    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if not question:
            continue

        chunks = retrieve_chunks(conn, question)

        if not chunks:
            print("Assistant: No chunks found in document_chunks — is the table empty?\n")
            continue

        print(f"\n[Retrieved {len(chunks)} chunks: "
              f"{', '.join(c['section_title'] for c in chunks)}]")

        answer = ask_claude(question, chunks)
        print(f"\nAssistant: {answer}\n")

    conn.close()


if __name__ == "__main__":
    main()