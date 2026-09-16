# Benchmark: hard

| Task type | With RAG | Without RAG | LLM → SQL over the same places |
|---|---|---|---|
| Disambiguation (confusable neighbour) | 9/9 (100%) | 0/9 (0%) | 9/9 (100%) |
| Ordered multi-step plan | 9/9 (100%) | 0/9 (0%) | 9/9 (100%) |
| Spatial relation ("nearest to base") | 3/6 (50%) | 0/6 (0%) | 0/6 (0%) |
| Plausible nonexistent place | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) |
