import os
import warnings
warnings.filterwarnings("ignore", message="Field.*conflict with protected namespace")

from azure.keyvault.secrets import SecretClient
from azure.identity import DeviceCodeCredential

from agents.structured_data_agent import StructuredDataAgent
from agents.unstructured_data_agent import UnstructuredDataAgent
from agents.multimodal_data_agent import MultimodalDataAgent

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#  Secrets
# ============================================================

keyVaultName = "structuredazureendpoint"
KVUri = f"https://{keyVaultName}.vault.azure.net/"

print("Connecting to Azure for authentication.")

credential = DeviceCodeCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

# ============================================================
# Structured Data Agent Secrets
# ============================================================

# Secret names as stored in the Key Vault. The two Azure OpenAI secrets are shared
# by the structured and unstructured agents; the Mongo collection name is fixed by
# data/unstructured_load_azure.py and is not stored as a secret.

structuredazureendpoint = client.get_secret("ai-endpoint").value
structuredazureapikey = client.get_secret("ai-key").value
structuredpostgresqlpassword = client.get_secret("postgres-password").value
structuredpostgresqluser = client.get_secret("postgres-user").value
structuredpostgresqldbname = client.get_secret("postgres-database").value
structuredpostgresqlhost = client.get_secret("postgres-host").value

# ============================================================
# Unstructured Data Agent Secrets
# ============================================================

unstructuredmongourl = client.get_secret("mongodb-connection-string").value
unstructureddbname = client.get_secret("mongodb-database").value
unstructuredcollectionname = "permits"
unstructuredazureendpoint = structuredazureendpoint
unstructuredazurekey = structuredazureapikey

# ============================================================
# Multimodal Data Agent Secrets
# ============================================================

multimodalazureconnstring = client.get_secret("blob-connection-string").value
multimodalazurecontentsafetyendpoint = client.get_secret("content-safety-endpoint").value
multimodalazurecontentsafetykey = client.get_secret("content-safety-key").value

# ============================================================
#  Chat Manager
# ============================================================

class AgentChatManager:
    def __init__(self):
        self._load_agents()

    # -------------------------
    #  Agents
    # -------------------------
    def _load_agents(self):

        print("\nLoading Structured Data Agent")
        print("-------------------------------")
        self.structured = StructuredDataAgent(
            azure_endpoint=structuredazureendpoint,
            api_key=structuredazureapikey,
            deployment="gpt-4.1-mini",
            db_config={
                "host": structuredpostgresqlhost,
                "dbname": structuredpostgresqldbname,
                "user": structuredpostgresqluser,
                "password": structuredpostgresqlpassword,
                "port": 5432,
            }
        )

        print("\nLoading Unstructured Data Agent")
        print("---------------------------------")
        self.unstructured = UnstructuredDataAgent(
            mongo_uri=unstructuredmongourl,
            db_name=unstructureddbname,
            collection_name=unstructuredcollectionname,
            chroma_path=os.path.join(BASE_DIR, "data", "chroma_db_storage"),
            azure_endpoint=unstructuredazureendpoint,
            azure_key=unstructuredazurekey,
            azure_api_version="2024-12-01-preview",
            embedding_deployment="text-embedding-ada-002",
            chat_deployment="gpt-4.1-mini",
        )
        self.unstructured.build_index()

        print("\nLoading Multimodal Data Agent")
        print("-------------------------------")
        self.multimodal = MultimodalDataAgent(
            azure_conn_str=multimodalazureconnstring,
            container_name="houses",
            content_safety_endpoint=multimodalazurecontentsafetyendpoint,
            content_safety_key=multimodalazurecontentsafetykey
        )

    # -------------------------
    #  Routing
    # -------------------------
    def _route_query(self, user_message: str) -> str:
        """
        Route user query to one of:
        - structured
        - unstructured
        - multimodal

        This is a rule-based router. You can extend it later.
        """
        msg = user_message.lower().strip()

        multimodal_keywords = [
            "house like mine",
            "find me a house like mine",
            "show me a house like mine",
            "similar house",
            "similar image",
            "image",
            "photo",
            "picture",
            "looks like",
            "visual",
        ]

        structured_keywords = [
            "demographic",
            "demographics",
            "price",
            "home price",
            "most expensive",
            "average",
            "median",
            "count",
            "how many",
            "highest",
            "lowest",
            "neighborhood",
            "income",
            "population",
            "sql",
            "database",
        ]

        unstructured_keywords = [
            "permit",
            "approved",
            "document",
            "documents",
            "text",
            "restaurant",
            "permit approved",
            "notes",
            "report",
            "permit document",
        ]

        # Priority 1: multimodal
        if any(keyword in msg for keyword in multimodal_keywords):
            return "multimodal"

        # Priority 2: unstructured
        if any(keyword in msg for keyword in unstructured_keywords):
            return "unstructured"

        # Priority 3: structured
        if any(keyword in msg for keyword in structured_keywords):
            return "structured"

        # Default fallback
        return "unstructured"

    # -------------------------
    #  Agent wrappers
    # -------------------------
    def _run_structured(self, prompt: str) -> str:
        result = self.structured.ask(prompt, verbose=False, run_bias_audit=True)

        if isinstance(result, dict):
            return result.get("response", str(result))

        return str(result)

    def _run_unstructured(self, prompt: str) -> str:
        result = self.unstructured.ask(prompt, run_pii_audit=True)

        if isinstance(result, dict):
            return result.get("response", str(result))

        return str(result)

    def _run_multimodal(self, prompt: str) -> str:
        """
        Right now your original code ignored the prompt and always used
        query-house-2.jpg. Keeping that behavior here for parity.
        """
        query_image = os.path.join(BASE_DIR, "query-house-2.jpg")  # or query-house-1.jpg to test the other image
        matches, query_img = self.multimodal.find_similar(query_image)

        print("\nTop matches:")
        for score, path, address in matches:
            print(f"{address} | similarity: {score:.4f}")

        self.multimodal.show_results(query_img, matches, output_path=os.path.join(BASE_DIR, "similar_houses.png"))

        if not matches:
            return "I could not find similar houses."

        lines = ["Here are the top similar houses I found:"]
        for score, path, address in matches[:5]:
            lines.append(f"- {address} (similarity: {score:.4f})")

        return "\n".join(lines)

    # -------------------------
    #  Chat Interface
    # -------------------------
    def chat(self, user_message: str) -> str:
        route = self._route_query(user_message)
        print(f"[Router] Selected agent: {route}")

        try:
            if route == "structured":
                return self._run_structured(user_message)

            if route == "unstructured":
                return self._run_unstructured(user_message)

            if route == "multimodal":
                return self._run_multimodal(user_message)

            return "I could not determine the correct agent."
        except Exception as e:
            return f"An error occurred while processing your request with the {route} agent: {e}"


# ============================================================
#  Run Chat
# ============================================================

if __name__ == "__main__":
    banner = r"""
                                   /\ 
                                  /  \ 
                                 /____\ 
                ________________/______\_______________________
               /                                               \
              /_________________________________________________\
             |   ____      ____      ____      ____      ____   |
             |  | ▇▇ |    | ▇▇ |    | ▇▇ |    | ▇▇ |    | ▇▇ |  |
             |  | ▇▇ |    | ▇▇ |    | ▇▇ |    | ▇▇ |    | ▇▇ |  |
             |  |____|    |____|    |____|    |____|    |____|  |
             |                                                  |
             |        NEIGHBORHOOD INSIGHTS & DATA ASSISTANT    |
             |__________________________________________________|
    """
    print(banner)
    print("\n This AI-powered assistant provides automatically generated responses. "
          "Please use discretion, as answers may contain inaccuracies or errors.\n")
    print("Data comes from a structured demographics database, permit documents in NoSQL, and images stored in Azure.")
    print("Data remains in its original systems and is not copied into the assistant.")
    print("Embeddings and other AI artifacts are generated at runtime and are not permanently stored.")

    chat = AgentChatManager()

    print("\n\n--------------------------------")
    print("\n Agent Chat Ready.\n")
    print("Here are some examples of questions you can ask:")
    print("  - Which demographic group has the most expensive homes?")
    print("  - Was a permit approved for a restaurant in any of the neighborhoods?")
    print("  - Find me a house like mine")
    print("\n Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ")
        if user_input.lower() in ("exit", "quit"):
            break

        answer = chat.chat(user_input)
        print(f"Agent: {answer}\n")