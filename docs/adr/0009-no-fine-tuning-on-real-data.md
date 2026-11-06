---
area: system
status: accepted
---
# No model fine-tuning on real financial data

Models are never fine-tuned on real data. Fine-tuned weights can memorise training data and cannot selectively forget it, so deleting a Memory or a Statement Import would no longer delete it, which breaks the data policy. Facts also change monthly, so a tuned model goes stale. Personalisation lives in data the system can delete: Memories, Corrections, feedback and the preference models. LoRA via MLX is allowed only on synthetic tool-calling data, and only when the model benchmark shows a gap.
