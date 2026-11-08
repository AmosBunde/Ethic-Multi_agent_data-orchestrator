from bson import ObjectId
from pymongo import MongoClient

from langchain_chroma import Chroma
from langchain_openai import AzureOpenAIEmbeddings, AzureChatOpenAI

from presidio_analyzer import AnalyzerEngine

class UnstructuredDataAgent:
    """
    MongoDB + Chroma + Azure OpenAI RAG pipeline
    """

    # ============================================
    # INIT
    # ============================================
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        chroma_path: str,
        azure_endpoint: str,
        azure_key: str,
        azure_api_version: str,
        embedding_deployment: str,
        chat_deployment: str,
        collection_label: str = "rag_collection",
        temperature: float = 0.0,
    ):
        # Mongo
        self.mongo_client = MongoClient(mongo_uri)
        self.mongo_db = self.mongo_client[db_name]
        self.mongo_coll = self.mongo_db[collection_name]

        # Embeddings
        self.embeddings = AzureOpenAIEmbeddings(
            azure_endpoint=azure_endpoint,
            azure_deployment=embedding_deployment,
            openai_api_version=azure_api_version,
            api_key=azure_key,
        )

        # Vector DB
        self.vector_db = Chroma(
            collection_name=collection_label,
            embedding_function=self.embeddings,
            persist_directory=chroma_path,
        )

        # Chat model
        self.llm = AzureChatOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            azure_deployment=chat_deployment,
            api_version=azure_api_version,
            temperature=temperature,
        )

    # ============================================
    # BUILD VECTOR INDEX
    # ============================================
    def build_index(self):
        print("Loading documents from MongoDB...")

        docs = list(self.mongo_coll.find({}, {"_id": 1, "content": 1}))

        texts = []
        ids = []
        metadatas = []

        for d in docs:
            doc_id = str(d["_id"])
            text = d.get("content", "").strip()

            if not text:
                continue

            texts.append(text)
            ids.append(doc_id)
            metadatas.append({"mongo_id": doc_id})

        if not texts:
            print("No documents found to index.")
            return

        print(f"Embedding {len(texts)} documents...")

        self.vector_db.add_texts(
            texts=texts,
            ids=ids,
            metadatas=metadatas,
        )

        print("Vector index built.")
        print("Vector DB size:", self.vector_db._collection.count())

    # ============================================
    # RAG QUERY
    # ============================================
    def ask(self, question: str, k: int = 3, run_pii_audit=True):
        results = self.vector_db.similarity_search(question, k=k)

        context_chunks = []
        source_docs = []

        for doc in results:
            mongo_id = doc.metadata.get("mongo_id")

            try:
                mongo_doc = self.mongo_coll.find_one({"_id": ObjectId(mongo_id)})
            except Exception:
                mongo_doc = None

            if mongo_doc and mongo_doc.get("content"):
                content = mongo_doc["content"].strip()
                context_chunks.append(content)

                # store full document metadata for transparency
                source_docs.append({
                    "mongo_id": str(mongo_doc["_id"]),
                    "content": content
                })

        if not context_chunks:
            return {
                "response": "No relevant documents found.",
                "sources": []
            }

        context_text = "\n\n".join(context_chunks)

        prompt = f"""
Use the following documents as context:

{context_text}

Question: {question}

Answer clearly and concisely:
"""

        response = self.llm.invoke(prompt)
        answer = response.content if isinstance(response.content, str) else str(response.content)

        audit = {
            "ran": bool(run_pii_audit),
            "pii_in_sources": [],
            "pii_in_answer": [],
            "redacted": False,
            "flagged": False,
        }

        if run_pii_audit:
            print("\n" + "=" * 80)
            print("Using Unstrucutred Data stored in MongoDB database to answer question")
            print("Seraching for PII data in source documents")
            print("=" * 80)
            audit["pii_in_sources"] = self.__detect_pii(str(source_docs), label="source documents")

            # Actionable check: PII that leaked into the generated answer is redacted
            # before the answer leaves the agent.
            audit["pii_in_answer"] = self.__detect_pii(answer, label="generated answer")
            if audit["pii_in_answer"]:
                answer = self.__redact(answer, audit["pii_in_answer"])
                audit["redacted"] = True
                print("Answer redacted: PII replaced with entity-type placeholders.\n")

            audit["flagged"] = bool(audit["pii_in_sources"] or audit["pii_in_answer"])

        return {
            "response": answer,
            "sources": [s["mongo_id"] for s in source_docs],
            "audit": audit,
        }


    # ============================================
    # OPTIONAL — RESET VECTOR DB
    # ============================================
    def reset_index(self):
        print("Clearing vector database...")
        self.vector_db.reset_collection()
        print("Vector DB cleared.")

    # ============================================
    # PRIVATE METHODS 
    # ============================================

    def __detect_pii(self, text: str, label: str = "text", min_score: float = 0.85) -> list:
        """Run Presidio and return unique high-confidence entities as dicts."""
        analyzer = AnalyzerEngine()

        results = analyzer.analyze(
            text=text,
            language="en"
        )

        found = []
        seen = set()  # track unique PII
        for r in sorted(results, key=lambda r: r.start):
            # skip URLs and low-confidence hits
            if r.entity_type == "URL" or r.score < min_score:
                continue
            pii_text = text[r.start:r.end]
            if pii_text not in seen:
                seen.add(pii_text)
                found.append({
                    "entity_type": r.entity_type,
                    "text": pii_text,
                    "score": round(float(r.score), 2),
                    "start": r.start,
                    "end": r.end,
                })

        if found:
            print(f"\nWARNING: PII detected in {label}:")
            for f in found:
                print(f"- {f['entity_type']}: '{f['text']}' (confidence={f['score']:.2f})")
            print("\n")
        else:
            print(f"\n No PII detected in {label}.\n")

        return found

    @staticmethod
    def __redact(text: str, entities: list) -> str:
        """Replace every occurrence of each detected PII string with a placeholder."""
        for e in sorted(entities, key=lambda e: len(e["text"]), reverse=True):
            text = text.replace(e["text"], f"[{e['entity_type']}]")
        return text
