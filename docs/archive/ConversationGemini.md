> **ARCHIVED — Pre-Iteration 1 blueprint (circa 2026-05-07)**
> This document describes a zero-cost Ollama-only architecture that was superseded before Iteration 1 launched.
> Current architecture uses Vertex AI Gemini 2.5 Pro/Flash, LangGraph orchestration, and FastAPI.
> See [README.md](../../README.md) and [docs/ARCHITECTURE.md](../ARCHITECTURE.md) for the real state.

---

Here is the compacted, definitive blueprint of Project J.A.R.V.I.S., structured for immediate execution.

### **Project J.A.R.V.I.S. – Executive Summary**

A fully autonomous, zero-cost, local AI personal assistant ecosystem. It operates primarily on native Windows 11, utilizing CLI commands for maximum efficiency, and features a hybrid memory system. It is accessible remotely via a custom mobile app capable of waking the host PC.

**Development Workflow:** Claude Code (Opus for architectural planning, Sonnet for execution) builds the system; local models run it.

---

### **I. The Tech Stack**

* **Hardware:** Local PC (NVIDIA RTX 4070) & Mobile Device.
* **OS/Environment:** Native Windows 11 (No WSL2 dependency) & Python.
* **Brain (LLM):** Ollama (Llama 3 8B Instruct / Mistral).
* **Ears & Mouth:** Faster-Whisper (STT) & Edge-TTS/Piper (TTS).
* **Memory:** ChromaDB/FAISS (Vector/Short-term) + Obsidian (Markdown/Long-term).
* **Networking & Comms:** Tailscale (VPN/WoL), FastAPI (WebSockets).
* **Frontend:** Custom Mobile App (Flutter/React Native).
* **Execution:** CLI-First (Python `subprocess`, `os`), Local APIs (e.g., Spotipy).

---

### **II. The Master Manifesto (`project_jarvis_manifesto_v2.md`)**

*Save this exactly as written in your root directory for Claude Code.*

```markdown
# Project J.A.R.V.I.S. - Master Manifesto & Technical Blueprint

## 1. Project Vision
To build a fully autonomous, zero-cost, open-source personal assistant ecosystem. The system operates locally on high-end hardware (RTX 4070) to ensure privacy. It acts as an "Agentic" companion that researches, codes, plans, and reports even while the user is away or the PC is powered off.

## 2. Execution Strategy: CLI-First & Native Interop
To maximize efficiency and minimize the high failure rates of UI automation:
* **Operating System:** Native Windows 11.
* **System Control:** Execution via Python `subprocess` and `os` libraries to trigger Windows (PowerShell/CMD) commands directly.
* **Application Control:** Deep integration via local Python APIs (e.g., Spotipy) instead of mouse/keyboard simulation.
* **Remote Trigger:** Wake-on-LAN (WoL) protocol support via Tailscale to power on the PC from a mobile device.

## 3. Technical Stack (Zero-Cost Runtime)
* **LLM Brain:** Ollama (Llama 3 8B Instruct/Mistral).
* **Tool Calling:** JSON-based intent recognition to trigger Python functions.
* **Backend:** FastAPI with WebSockets for real-time mobile-to-PC audio/text streams.
* **Networking:** Tailscale for secure P2P tunneling.
* **Frontend:** Custom Mobile App (Flutter/React Native).

## 4. Memory & Knowledge Management (Hybrid Architecture)
* **Short-Term:** Conversation buffer memory.
* **Long-Term (Vector):** ChromaDB for semantic retrieval.
* **Permanent Archive:** Obsidian-compatible Markdown vault for logs and profiles.

## 5. Aesthetics & Reporting
* **Theme:** "Dark Academia" aesthetic.
* **Deliverables:** Academic-grade reports generated in LaTeX (.tex/.pdf) and organized within the Obsidian vault.

## 6. Constraints
* **Development Tool:** Claude Code builds the architecture.
* **Runtime Dependency:** The final assistant MUST be 100% independent of external/paid APIs.

```

---

### **III. The 5-Phase Execution Plan**

Feed these prompts sequentially to Claude Code to build the system step-by-step:

**Phase 1: The Core Engine**

> "Read `project_jarvis_manifesto_v2.md`. I want to start by building Phase 1: The Core Engine. Please write a Python script that connects to a local Ollama instance. Implement a basic CLI loop where I can type a command, the LLM interprets it, and if it's a system command, it executes it natively using Python's `subprocess` or `os` module on Windows 11. Ensure robust error handling."

**Phase 2: Hybrid Memory**

> "Phase 2: Hybrid Memory. Update our core Python application. Integrate `chromadb` for long-term vector storage of conversations. Implement a function that logs session summaries and reports into a local directory formatted as an Obsidian Markdown vault. The LLM must query this vector DB before answering to maintain context."

**Phase 3: Deep Control (Tool Calling)**

> "Phase 3: Deep Control. Implement 'Tool Calling'. Update the prompt structure so the local LLM outputs JSON to perform actions. Create two modular Python tools: 1) A mock Spotify controller (play/pause) and 2) A local task manager that appends to-do items to a markdown file. The execution flow must parse the JSON and trigger the Python function invisibly."

**Phase 4: The Communication Bridge**

> "Phase 4: The Communication Bridge. Wrap the existing J.A.R.V.I.S. engine inside a FastAPI application. Create a WebSocket endpoint (`/ws/chat`) capable of receiving continuous audio/text streams and returning responses. Ensure the server is optimized to run locally while being accessible via our Tailscale network IP."

**Phase 5: The Mobile Client & Remote Wake**

> "Phase 5: The Mobile Client. Generate the scaffold for a React Native (or Flutter) mobile application. Features: 1) A Wake-on-LAN (WoL) function that sends a magic packet to my PC's MAC address over Tailscale. 2) A WebSocket client that connects to the FastAPI server to stream mic input and play back audio. Apply a 'Dark Academia' aesthetic to the UI."
