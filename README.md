# a2a-demo

This repository helps demonstrate how to set up agents on Azure following the Agent-to-Agent (A2A) protocol.

---

## Overview

This repository demonstrates a simple **Agent-to-Agent (A2A)** workflow on Azure where agents coordinate real work by passing files and structured context — not just text.

It is intentionally small and designed as a scaffold for larger agentic systems.

### What This Demo Shows

- Agent-to-agent communication via HTTP (A2A-style)
- Passing files using Azure Blob Storage
- Passing context using explicit request IDs and JSON payloads
- Clear separation of agent responsibilities

---

## Architecture

### Agents

The demo consists of **three agents**, each exposed as an HTTP endpoint:

#### 1. Ingestion Agent
- Accepts a file upload  
- Stores the raw file and extracted text in Blob Storage  
- Triggers the next agent via A2A  

#### 2. Transformation Agent
- Reads extracted text from Blob Storage  
- Produces a summary and structured metadata  
- Stores outputs as blobs  
- Triggers the review agent via A2A  

#### 3. Review Agent
- Reads transformation artifacts  
- Produces a final review report  

Agents communicate **only via A2A calls and artifact references**.

---

## Data Flow

### Blob Storage

- `demo-uploads` — raw files  
- `demo-processed` — extracted text  
- `demo-outputs` — summaries and final reports  

### Table Storage

- `DemoA2ARequests` — tracks request state and artifact references  

---

## A2A Pattern

Agents pass only **small, explicit payloads**:

```json
{
  "request_id": "uuid",
  "artifact_ref": "container/blob"
}
