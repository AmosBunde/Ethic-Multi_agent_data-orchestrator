from azure.storage.blob import BlobServiceClient
from io import BytesIO
import hashlib
import warnings
warnings.filterwarnings("ignore", message="Field.*conflict with protected namespace")
import os
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor
import matplotlib
matplotlib.use("Agg")  # headless: results are saved to a PNG instead of shown in a window
import matplotlib.pyplot as plt

from azure.ai.contentsafety import ContentSafetyClient
from azure.ai.contentsafety.models import AnalyzeImageOptions, ImageData
from azure.core.credentials import AzureKeyCredential

class MultimodalDataAgent:

    def __init__(
        self,
        model_name="openai/clip-vit-base-patch32",
        azure_conn_str = None,
        container_name = None,
        content_safety_endpoint = None,
        content_safety_key = None
    ):
        self.model = CLIPModel.from_pretrained(model_name)
        self.processor = CLIPProcessor.from_pretrained(model_name)

        # Azure Blob Storage
        self.azure_conn_str = azure_conn_str
        self.container_name = container_name

        if azure_conn_str and container_name:
            self.blob_service = BlobServiceClient.from_connection_string(azure_conn_str)
            self.container_client = self.blob_service.get_container_client(container_name)
        else:
            self.container_client = None

        # Azure Content Safety
        self.content_safety_client = ContentSafetyClient(endpoint=content_safety_endpoint, credential=AzureKeyCredential(content_safety_key))
        self._safety_cache = {}  # sha256(image bytes) -> analysis dict

    # ============================================
    # PRIVATE METHODS
    # ============================================

    def __analyze_image(self, image_bytes, label):
        """Run Azure AI Content Safety on raw image bytes and return a result dict."""
        request = AnalyzeImageOptions(image=ImageData(content=image_bytes))
        response = self.content_safety_client.analyze_image(request)

        print(f"Analyzing image: {label}")
        categories = {}
        for c in response.categories_analysis:
            print(c.category, c.severity)
            categories[str(c.category)] = int(c.severity or 0)

        flagged = any(sev != 0 for sev in categories.values())
        if flagged:
            print(f"Warning: Harmful content detected in image: {label}\n")
        else:
            print(f"No harmful content detected in image: {label}\n")

        return {"image": label, "categories": categories, "flagged": flagged}

    def __load_image_from_blob(self, blob_name):
        """Download a blob, screen it with Content Safety (cached), return (image, analysis)."""
        print("\n" + "=" * 80)
        print(f"Analysing image: {blob_name}")

        blob = self.container_client.get_blob_client(blob_name)
        image_bytes = blob.download_blob().readall()

        # Cache by content hash, so a blob replaced between the search and the
        # rendering step is re-screened instead of reusing a stale verdict.
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest not in self._safety_cache:
            self._safety_cache[digest] = self.__analyze_image(image_bytes, blob_name)
        analysis = self._safety_cache[digest]

        return Image.open(BytesIO(image_bytes)).convert("RGB"), analysis


    def __load_image(self, path):
        return Image.open(path).convert("RGB")

    def __screen_query_image(self, query_image_path):
        """Content Safety check on the user's own image before any similarity search."""
        print("\n" + "=" * 80)
        print(f"Screening query image before search: {query_image_path}")
        with open(query_image_path, "rb") as f:
            return self.__analyze_image(f.read(), os.path.basename(query_image_path))

    def __compute_similarity_blob(self, query_image_path, blob_name):
        img1 = self.__load_image(query_image_path)
        img2, analysis = self.__load_image_from_blob(blob_name)

        inputs = self.processor(images=[img1, img2], return_tensors="pt")

        with torch.no_grad():
            outputs = self.model.get_image_features(**inputs)

        if not isinstance(outputs, torch.Tensor):
            image_embeds = outputs.pooler_output
        else:
            image_embeds = outputs

        image_embeds = image_embeds / image_embeds.norm(dim=-1, keepdim=True)
        similarity = torch.dot(image_embeds[0], image_embeds[1]).item()
        return similarity, analysis

    # ============================================
    # PUBLIC METHOD — FIND SIMILAR
    # ============================================

    def find_similar(self, query_image_path, prefix="", top_k=3):
        """
        Returns (matches, query_image, safety) where matches is the top-k list of
        (similarity, blob_name, address) and safety summarises every Content Safety check.
        """
        if not self.container_client:
            raise ValueError("Azure Blob Storage is not configured.")

        safety = {
            "query_image": None,
            "query_blocked": False,
            "blobs": [],
            "excluded": [],
        }
        query_img = self.__load_image(query_image_path)

        # Actionable check 1: refuse to search with a harmful query image.
        safety["query_image"] = self.__screen_query_image(query_image_path)
        if safety["query_image"]["flagged"]:
            safety["query_blocked"] = True
            print("Query image failed content safety screening. Search aborted.")
            return [], query_img, safety

        results = []

        print("\n" + "=" * 80)
        print("Using Multimodal (Image files) Data stored in Azure Data Blob to answer question")
        print("Scan images for harmful contente")
        print("=" * 80)

        blob_list = list(self.container_client.list_blobs(name_starts_with=prefix))
        print("Blob count:", len(blob_list))

        for blob in blob_list:
            fname = blob.name
            print(fname)

            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue

            try:
                similarity, analysis = self.__compute_similarity_blob(query_image_path, fname)
                safety["blobs"].append(analysis)
                address = os.path.splitext(os.path.basename(fname))[0].replace("_", " ")

                # Actionable check 2: flagged images never reach the user.
                if analysis["flagged"]:
                    safety["excluded"].append(fname)
                    print(f"Excluded from results (content safety): {fname}")
                    continue

                results.append((similarity, fname, address))

            except Exception as e:
                print("Skipping:", fname, e)

        results.sort(reverse=True, key=lambda x: x[0])

        return results[:top_k], query_img, safety

    # ============================================
    # PUBLIC METHOD — SHOW RESULTS
    # ============================================

    def show_results(self, query_img, matches, output_path="similar_houses.png"):
        ncols = len(matches) + 1
        plt.figure(figsize=(4 * ncols, 4))

        plt.subplot(1, ncols, 1)
        plt.imshow(query_img)
        plt.title("Query")
        plt.axis("off")

        for i, (score, blob_name, address) in enumerate(matches, start=2):
            img, analysis = self.__load_image_from_blob(blob_name)
            if analysis["flagged"]:
                print(f"Not rendering {blob_name}: failed content safety on re-check")
                continue

            plt.subplot(1, ncols, i)
            plt.imshow(img)
            plt.title(f"{address}\n{score:.3f}")
            plt.axis("off")

        plt.tight_layout()
        plt.savefig(output_path, dpi=120)
        plt.close()
        print(f"Saved similarity results to {output_path}")
