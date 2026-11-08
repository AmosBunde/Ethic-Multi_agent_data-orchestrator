"""End-to-end experiment runner.

Signs in to Azure once with a device code, then:
  1. verifies all Key Vault secrets are readable
  2. seeds PostgreSQL and Cosmos DB for MongoDB from data/
  3. starts the chat manager and runs six evaluation queries, two per agent

Everything printed is also written to logs/experiment_<timestamp>.log, which serves as
the terminal log for the submission. Run from the starter/ folder:

    python run_experiment.py            # full run
    python run_experiment.py --skip-load  # databases already seeded
    python run_experiment.py --skip-postgres | --skip-mongo   # seed only one
"""
import argparse
import contextlib
import datetime as dt
import io
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "data"))
sys.path.insert(0, str(BASE / "infra"))

# --- one shared login for every script -------------------------------------------
import azure.identity
from azure_login import get_credential

_shared = get_credential()


def _shared_credential(*args, **kwargs):
    return _shared


# Scripts do `from azure.identity import DeviceCodeCredential` at import time, so patching
# the module attribute before importing them makes them all reuse the one login.
azure.identity.DeviceCodeCredential = _shared_credential


class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def banner(title):
    print("\n" + "#" * 80)
    print(f"# {title}")
    print("#" * 80 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-load", action="store_true", help="skip seeding PostgreSQL and MongoDB")
    ap.add_argument("--skip-postgres", action="store_true", help="skip seeding PostgreSQL only")
    ap.add_argument("--skip-mongo", action="store_true", help="skip seeding MongoDB only")
    ap.add_argument("--skip-verify", action="store_true", help="skip the Key Vault secret check")
    args = ap.parse_args()

    log_path = BASE / "logs" / f"experiment_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)
    print(f"Logging to {log_path}\n")

    # The first Key Vault call below triggers the single device-code prompt. Calling
    # get_token() directly first would log in to the home tenant, and the vault then
    # challenges for its own tenant, producing a second prompt.
    banner("STEP 0  Azure sign-in (device code)")
    print("Signed in (cached login reused when available).")

    if not args.skip_verify:
        banner("STEP 1  Verify Key Vault secrets")
        import verify_keyvault
        sys.argv = ["verify_keyvault.py"]
        if verify_keyvault.main() != 0:
            print("Key Vault check failed; stopping.")
            return 1

    if not (args.skip_load or args.skip_postgres):
        banner("STEP 2a  Seed PostgreSQL from data/structured")
        import structured_load_azure
        structured_load_azure.main()

    if not (args.skip_load or args.skip_mongo):
        banner("STEP 2b  Seed Cosmos DB for MongoDB from data/unstructured")
        import unstructured_load_azure
        unstructured_load_azure.load_pdfs_to_mongo()

    banner("STEP 3  Start chat manager (loads all three agents)")
    import chat  # performs secret retrieval and nothing else at import

    manager = chat.AgentChatManager()

    # Two evaluation prompts per agent (rubric minimum: six). The second multimodal
    # prompt switches the query image so both sample images are exercised.
    queries = [
        ("structured",   "Which demographic group has the most expensive homes?", None),
        ("structured",   "How many houses have a swimming pool in each neighborhood?", None),
        ("unstructured", "Was a permit approved for a restaurant in any of the neighborhoods?", None),
        ("unstructured", "Which permit documents mention school construction and who applied?", None),
        ("multimodal",   "Find me a house like mine", "query-house-2.jpg"),
        ("multimodal",   "Show me a house that looks like this picture", "query-house-1.jpg"),
    ]
    for expected, q, image in queries:
        banner(f"QUERY [{expected}]  {q}")
        if image:
            manager.query_image = image
            print(f"(query image: {image})")
        print(f"You: {q}")
        answer = manager.chat(q)
        print(f"\nAgent: {answer}\n")

    banner("DONE")
    print(f"Full log:          {log_path}")
    print(f"Audit log (JSONL): {BASE / 'logs' / 'audit_log.jsonl'}")
    print(f"Similarity figure: {BASE / 'similar_houses.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
