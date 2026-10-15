cd ../kb
python build_kb.py --pdf_loader unstructured \
  --unstructured_mode elements \
  --unstructured_strategy fast
# 若是扫描件/版面复杂：
# --unstructured_strategy hi_res
# 纯扫描文字识别：
# --unstructured_strategy ocr_only
