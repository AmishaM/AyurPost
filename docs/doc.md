The final-spec.md should be populated with the following in a brief manner and veru techinical manner

Problem defination
Targeting a small business - ayurvedic clinic which lacks social media presence and the owner does not have time to create social media content. However having a good social media presence is very essential for the brand to flourish

Data processing
Presence of authentic ayurvedic text would be fairly minimal in LLM training and hence its not fair to expect grounded ayurvedic content from an LLM. So we have sourced 3 book - name the book, these text don't have good translated PDFs as well, they have scanned versions. We have had to OCR them, lot of precoessing to remove stray OCR symbols
1 line about the chunking stategy we have used
Technology used - Library used for OCR, Voyage and Qdrant
Techniques used - BM25, pre-filerting, meta-data filtering => one brief line about why we need all 3

System design
Draw 2 detailed mermaid diagram
Dig 1 - Sourced text, embed and store
Dig 2 - Background calendar yaml used to generate reel, streamlit app, doctor selects to edit reel and the flow after that

Evals
Task-specifc
Error handling
COst
Latency 
