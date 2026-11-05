"""Embeddings of Qwen3-Embedding-0.6B on Modal (Т-§10.5, ТЗ Ф4 WP20.3; D-C-117).

On CPU, without a GPU. `Embedder.embed(texts, prompt, dim)` returns normalized vectors cut to
`dim` components (MRL); the caller (`quack.embeddings.client.ModalEmbeddingClient`) passes the
task instruction as `prompt` for a query and nothing for documents. The model is loaded once
per container. Deploy is the owner's action (ТЗ Ф4 §1.3):

    modal deploy modal/embeddings.py
"""

from __future__ import annotations

import modal

APP_NAME = "quack-embeddings"
MODEL = "Qwen/Qwen3-Embedding-0.6B"
# [ОРИЕНТИР] (WP20.3): how long an idle container stays warm; Т-§10.5 keeps it warm while
# sessions are active — tuned by the owner with the Modal cost check (Т-§10.5 «Проверить»).
SCALEDOWN_WINDOW_S = 300
CPU_CORES = 2.0

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "torch==2.8.0",
        extra_index_url="https://download.pytorch.org/whl/cpu",
        extra_options="--index-strategy unsafe-best-match",
    )
    .uv_pip_install("transformers==4.56.1", "sentence-transformers==5.1.0")
)

app = modal.App(APP_NAME, image=image)


@app.cls(cpu=CPU_CORES, scaledown_window=SCALEDOWN_WINDOW_S)
class Embedder:
    @modal.enter()
    def load(self) -> None:
        # Installed only in the image above, not in the backend environment.
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]

        self.model = SentenceTransformer(MODEL, device="cpu")

    @modal.method()
    def embed(self, texts: list[str], prompt: str | None, dim: int) -> list[list[float]]:
        vectors = self.model.encode(
            texts, prompt=prompt, truncate_dim=dim, normalize_embeddings=True
        )
        return [[float(x) for x in vector] for vector in vectors]
