import os
import json
import uuid
import logging
from datetime import datetime, timezone
from azure.ai.projects.models import A2ATool

import azure.functions as func
import httpx

from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient, TableEntity

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# ========== Settings ==========
AZURE_CONN = os.getenv("Azure_Connection_String") or os.getenv("AzureWebJobsStorage")


UPLOADS_CONTAINER = os.getenv("UPLOADS_CONTAINER", "demo-uploads")
PROCESSED_CONTAINER = os.getenv("PROCESSED_CONTAINER", "demo-processed")
OUTPUTS_CONTAINER = os.getenv("OUTPUTS_CONTAINER", "demo-outputs")
TABLE_NAME = os.getenv("TABLE_NAME", "DemoA2ARequests")

A2A_BASE_URL = os.getenv("A2A_BASE_URL", "http://localhost:1738/api").rstrip("/")



def get_clients():
    blob_service = BlobServiceClient.from_connection_string(AZURE_CONN)
    table_service = TableServiceClient.from_connection_string(AZURE_CONN)

    uploads = blob_service.get_container_client(UPLOADS_CONTAINER)
    processed = blob_service.get_container_client(PROCESSED_CONTAINER)
    outputs = blob_service.get_container_client(OUTPUTS_CONTAINER)

    try:
        uploads.create_container()
        processed.create_container()
        outputs.create_container()
    except Exception:
        pass

    table_service.create_table_if_not_exists(TABLE_NAME)
    table = table_service.get_table_client(TABLE_NAME)

    return uploads, processed, outputs, table
def _table():
    try:
        table_service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return table_service.get_table_client(TABLE_NAME)


def _utc_iso():
    return datetime.now(timezone.utc).isoformat()

def _upsert_request(table, request_id: str, **fields):
    table.upsert_entity(entity=entity, mode="Merge")
    entity = TableEntity()
    entity["PartitionKey"] = "A2A"
    entity["RowKey"] = request_id
    entity["updatedAt"] = _utc_iso()
    for k, v in fields.items():
        entity[k] = v
    table.upsert_entity(entity=entity, mode="Merge")

def _upload_text(container, blob_name: str, text: str) -> str:
    container.upload_blob(blob_name, text.encode("utf-8"), overwrite=True)
    # Return a stable reference (container + blob name); URL optional
    return f"{container.container_name}/{blob_name}"

def _download_text(container, blob_name: str) -> str:
    return container.download_blob(blob_name).readall().decode("utf-8", errors="replace")

def _split_ref(ref: str):
    # ref is "container/blobname"
    parts = ref.split("/", 1)
    return parts[0], parts[1]

# ========== Endpoint 1: Ingest ==========
@app.route(route="demo/ingest", methods=["POST"])
def demo_ingest(req: func.HttpRequest) -> func.HttpResponse:
    try:
        request_id = str(uuid.uuid4())

        # Expect multipart file upload: key "file"
        file = req.files.get("file")
        if not file:
            return func.HttpResponse("Missing file. Use multipart form key 'file'.", status_code=400)

        filename = file.filename or "upload.bin"
        raw_bytes = file.read()

        raw_blob_name = f"{request_id}/{filename}"
        uploads, processed, outputs, table = get_clients()
        uploads.upload_blob(raw_blob_name, raw_bytes, overwrite=True)

        # For speed: treat as text if decodable, else store placeholder.
        # (You can add real PDF extraction later with PyPDF2.)
        try:
            extracted_text = raw_bytes.decode("utf-8")
        except Exception:
            extracted_text = "[binary file uploaded — add PDF/text extraction here]"

        text_blob_name = f"{request_id}/extracted.txt"
        text_ref = _upload_text(processed, text_blob_name, extracted_text)

        _upsert_request(
            table, 
            request_id,
            status="extracted",
            sourceBlob=f"{UPLOADS_CONTAINER}/{raw_blob_name}",
            textBlob=text_ref,
        )

        # A2A: call transform endpoint
        payload = {
            "request_id": request_id,
            "text_ref": text_ref
        }

        transform_url = f"{A2A_BASE_URL}/demo/transform"
        with httpx.Client(timeout=60) as client:
            r = client.post(transform_url, json=payload)
            r.raise_for_status()

        return func.HttpResponse(
            json.dumps({"request_id": request_id, "status": "transform_triggered"}),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.exception("demo_ingest failed")
        return func.HttpResponse(f"demo_ingest error: {e}", status_code=500)

# ========== Endpoint 2: Transform ==========
@app.route(route="demo/transform", methods=["POST"])
def demo_transform(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        request_id = body["request_id"]
        text_ref = body["text_ref"]

        container_name, blob_name = _split_ref(text_ref)
        if container_name != PROCESSED_CONTAINER:
            return func.HttpResponse("text_ref must point to processed container", status_code=400)
        
        uploads, processed, outputs, table = get_clients()
        text = _download_text(processed, blob_name)

        # Quick + concrete transform (not just LLM): structured outline + short summary
        # (You can later swap this with a Foundry agent call.)
        summary = (text[:800] + "...") if len(text) > 800 else text
        structure = {
            "request_id": request_id,
            "length_chars": len(text),
            "preview": text[:300]
        }

        summary_ref = _upload_text(outputs, f"{request_id}/summary.txt", summary)
        structure_ref = _upload_text(outputs, f"{request_id}/structure.json", json.dumps(structure, indent=2))

        _upsert_request(
            table,
            request_id,
            status="transformed",
            summaryBlob=summary_ref,
            structureBlob=structure_ref,
        )

        # A2A: call review endpoint
        review_url = f"{A2A_BASE_URL}/demo/review"
        payload = {
            "request_id": request_id,
            "summary_ref": summary_ref,
            "structure_ref": structure_ref
        }

        with httpx.Client(timeout=60) as client:
            r = client.post(review_url, json=payload)
            r.raise_for_status()

        return func.HttpResponse(
            json.dumps({"request_id": request_id, "status": "review_triggered"}),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.exception("demo_transform failed")
        return func.HttpResponse(f"demo_transform error: {e}", status_code=500)

# ========== Endpoint 3: Review ==========
@app.route(route="demo/review", methods=["POST"])
def demo_review(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        request_id = body["request_id"]
        summary_ref = body["summary_ref"]
        structure_ref = body["structure_ref"]

        # Download artifacts
        c1, b1 = _split_ref(summary_ref)
        c2, b2 = _split_ref(structure_ref)

        if c1 != OUTPUTS_CONTAINER or c2 != OUTPUTS_CONTAINER:
            return func.HttpResponse("summary_ref/structure_ref must point to outputs container", status_code=400)

        uploads, processed, outputs, table = get_clients()
        summary = _download_text(outputs, b1)
        structure = _download_text(outputs, b2)

        # Concrete review logic (not just LLM): simple checks + annotated report
        flags = []
        if len(summary.strip()) < 50:
            flags.append("Summary is very short")
        if "TODO" in summary:
            flags.append("Summary contains TODO")

        report = "\n".join([
            "DEMO REVIEW REPORT",
            f"request_id: {request_id}",
            "",
            "Flags:",
            "- " + "\n- ".join(flags) if flags else "- None",
            "",
            "Structure (raw):",
            structure[:1200],
            "",
            "Summary (raw):",
            summary[:1200],
        ])

        report_ref = _upload_text(outputs, f"{request_id}/final_report.txt", report)

        _upsert_request(
            table, 
            request_id,
            status="reviewed",
            reportBlob=report_ref,
        )

        return func.HttpResponse(
            json.dumps({"request_id": request_id, "status": "reviewed", "report_ref": report_ref}),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.exception("demo_review failed")
        return func.HttpResponse(f"demo_review error: {e}", status_code=500)


