FROM ghcr.io/huggingface/text-embeddings-inference:cpu-1.9

CMD ["--model-id", "BAAI/bge-small-zh-v1.5", "--port", "8080"]
