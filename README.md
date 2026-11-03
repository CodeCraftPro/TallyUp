# TallyUp // Financial Dissection Engine

```text
 /░░░    /█▀▀█    /░      /░      /░  /░    /░/░    /░░░░
│//▒/   │ ▓▓▓▓   │ ▒     │ ▒     │//▒/▒/   │ ▒ ▒   │_▒ /▒
 │ ▓    │_▒_/▒   │ ▓     │ ▓      │//▓/    │ ▓ ▓   │ ▓▓▓/
 │ █    │ ░│ ░   │ ███   │ ███     │ █     │ ███   │ █_/ 
 │//    │//│//   │/__/   │/__/     │//     │/__/   │//   
```

> **TallyUp** is a terminal-inspired, keyboard-friendly financial expense dissection engine designed for group trips, housemates, and itemized restaurant outings. Built with a retro monospace aesthetic, a 6-phase receipt reconciliation engine, configurable AI vision, and Indian UPI deep linking.

---

## [KEY CAPABILITIES]

### 1. Itemized Restaurant Bill Dissection
- **Dish-by-Dish Consumption**: Assign specific dishes to individual diners or split them across subsets of the table.
- **Portion Splitting (`[➗ SPLIT]`)**: When bills list multiple quantities (e.g., 3 Chicken Biryani shared unevenly), split them with a single click into independent portions (`Dish (1/3)`, `Dish (2/3)`, `Dish (3/3)`) for distinct diners.
- **Proportional Taxes & Surcharges**: Distribute GST (CGST/SGST), voluntary restaurant service charge, packaging, and convenience fees proportionally to each person's exact consumption.
- **Discounts & Coupons**: Factor in percentage or lump-sum discounts (e.g. Zomato / Swiggy Dineout 10-50% off) calculated fairly across qualifying dishes.
- **Off-Bill Outside Items**: Track outside items (e.g. drinks, cigarettes, snacks) paid by a diner without inflating the restaurant bill's tax calculations.
- **Hosted Treats (`[TREAT]`)**: Flag any meal or cab ride as a treat hosted 100% by one person; diners' dues remain ₹0.00 while maintaining transparent bookkeeping.

### 2. Configurable Receipt Scanner: Local OCR or Bring-Your-Own-AI
TallyUp features a dual-engine receipt scanner configurable directly from the in-app **[CONFIG]** settings modal or via environment variables:

| Engine | Description | Best For | Privacy & Cost |
| :--- | :--- | :--- | :--- |
| **Local RapidOCR** *(Default)* | ONNX-accelerated PP-OCRv4 running locally on CPU/GPU (~0.7s execution). | Offline use, high speed, standard paper bills. | 100% Private, 0 cloud calls, ₹0 free forever. |
| **Google Gemini** | Google Gemini Vision API (`gemini-1.5-flash`, `gemini-1.5-pro`, `gemini-2.0-flash`). | Fast, highly accurate extraction with free-tier access. | External API call (requires Gemini API key). |
| **OpenAI / ChatGPT** | OpenAI Vision API (`gpt-4o-mini`, `gpt-4o`). | Complex crumpled, curved, or handwritten bills. | External API call (requires OpenAI API key). |
| **Anthropic Claude** | Anthropic Messages API (`claude-3-5-sonnet-20241022`, `claude-3-5-haiku`). | Detailed receipt extraction and complex multilingual item titles. | External API call (requires Anthropic API key). |
| **Custom OpenAI-Compatible** | Self-hosted or third-party vision endpoints (Ollama, OpenRouter, DeepSeek, LocalAI). | Local open-source models (e.g. Llama 3.2 Vision) or third-party gateways. | Fully controllable endpoint & model. |

> **Graceful Fallback**: If an external AI provider experiences an outage, network timeout, or invalid key, TallyUp automatically falls back to local RapidOCR so you are never left stranded.

### 3. 6-Phase Mathematical Receipt Reconciliation Engine
Every scanned receipt (whether from Local OCR or an LLM) passes through a strict 6-phase reconciliation pipeline (`receipt_reconciler.py`) before populating the form:
1. **Data Sanitization**: Strips currency formatting (`₹`, `$`, `Rs.`), normalizes floating-point amounts, and cleans item descriptions.
2. **Item Validation & Auto-Repair**: Asserts $\text{Qty} \times \text{Price} == \text{Total}$. Automatically calculates missing unit prices or line totals and detects multi-quantity line items.
3. **Subtotal Phase**: Verifies the sum of individual items against the extracted subtotal. Auto-detects and isolates duplicate or hallucinated lines matching the exact discrepancy delta.
4. **Grand Total Phase**: Asserts $\text{Subtotal} + \text{Taxes} + \text{Service Charge} + \text{Fees} - \text{Discounts} == \text{Grand Total}$ within a $\pm 1.0$ rounding tolerance.
5. **Heuristic Phase**: Validates CGST/SGST symmetry and standard GST slab rates (2.5%, 5%, 12%, 18%).
6. **Audit Formulation**: Generates a confidence score, validation badge, and collapsible audit log directly in the UI.

### 4. Zero-Emoji Terminal Monospace Aesthetic
- **Monospace Typography**: Styled with IBM Plex Mono, sharp 0px border radiuses, and `#0a0a0a` / `#141414` / `#0A8F20` phosphor-green terminal tones.
- **Strict 0-Emoji Policy**: All diners are identified by unique 2-letter uppercase bracketed ASCII tags (`[GH]`, `[CI]`, `[AB]`, `[SH]`, `[BA]`).
- **Proportional Typography Zoom Scale**: In-app zoom controls (88% Compact, 100% Normal, 115% Large, 130% Extra Large) adjust reading scale without breaking terminal box layouts.

### 5. Settlement Optimization & Indian UPI Deep Linking
- **Minimum-Transaction Debt Simplification**: Solves debts using a greedy bipartite transfer algorithm to minimize peer-to-peer payments.
- **Member UPI Directory**: Store UPI IDs (`username@bank`) to automatically generate Indian UPI payment URIs (`upi://pay?pa=...&pn=...&am=...&cu=INR`).
- **Instant QR Code Generator**: Generates scannable UPI QR codes on the `/pay` landing screen for phone camera or payment app scanning (Google Pay, PhonePe, Paytm).
- **ASCII Thermal Receipt Generator**: Generates 42-column monospace ASCII receipts formatted for instant copy-pasting into WhatsApp, Telegram, or Signal groups.

---

## [GETTING STARTED]

### Prerequisites
- Python 3.10 or higher
- Git

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/CodeCraftPro/TallyUp.git
cd TallyUp

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch TallyUp
python3 app.py
```

Open your browser at:
```text
http://127.0.0.1:5001
```
*(Port 5001 is automatically negotiated to avoid port 5000, which macOS reserves for AirPlay Receiver).*

---

## [AI & SCANNER CONFIGURATION]

You can configure the receipt scanning engine in two ways:

### Option A: Via the Web UI (Recommended)
1. Click **`[CONFIG]`** in the top navigation bar or dashboard telemetry card.
2. Scroll to **`RECEIPT OCR & AI DISSECTION ENGINE`**.
3. Select **`[LOCAL RAPIDOCR]`** for 100% offline scanning, or **`[CUSTOM AI / LLM VISION]`**.
4. If choosing AI, select your provider (Gemini, OpenAI, Claude, or Custom), paste your API key, and specify the model name.
5. Click **`[SAVE AI CONFIG]`**. Settings persist permanently in `data/db.json`.

### Option B: Via Environment Variables
Copy `.env.example` or set environment variables in your shell:

```bash
# Engine selection: "local" (default) or "llm"
export LLM_ENGINE="llm"

# Provider: "gemini", "openai", "claude", or "custom"
export LLM_PROVIDER="gemini"

# API Key
export LLM_API_KEY="your-api-key-here"

# Model name (optional, defaults automatically based on provider)
export LLM_MODEL="gemini-1.5-flash"

# Base URL (only required for custom / self-hosted Ollama endpoints)
export LLM_BASE_URL="http://localhost:11434/v1"
```

---

## [REPOSITORY STRUCTURE]

```text
├── app.py                  # Flask web server, settlement math, group & trip routing
├── receipt_parser.py       # RapidOCR pipeline, bounding box geometry & midpoint partitioning
├── receipt_reconciler.py   # 6-phase mathematical receipt validation & repair pipeline
├── llm_scanner.py          # Vision AI connector (Gemini, OpenAI, Claude, Ollama)
├── requirements.txt        # Production dependencies
├── .env.example            # Environment configuration template
├── templates/              # Monospace terminal Jinja2 HTML templates
│   ├── base.html           # Layout shell, navigation, global settings & configuration modal
│   ├── dashboard.html      # Telemetry cards, trip lists & quick stats
│   ├── trip.html           # Trip ledger, quick receipt dropzone, ASCII receipt modal
│   ├── add_expense.html    # Itemized restaurant & simple expense editors, split controls
│   ├── settlement.html     # Optimized debt matrix & payment breakdown
│   ├── pay.html            # UPI QR code rendering & payment link redirector
│   └── setup.html          # Initial participant onboarding
└── data/
    └── db.json             # Permanent local JSON store (untracked in git)
```

---

## [DATA PRIVACY & LOCAL PERSISTENCE]

- **Single Source of Truth**: All trips, events, expenses, and rosters are stored locally in `data/db.json`.
- **Untracked in Git**: The `data/` directory is strictly ignored by `.gitignore` so your personal financial records never leak to public repositories.
- **Accidental Truncation Guard**: The persistence layer guarantees that trip roster edits never drop or overwrite participants from other trips.

---

## [LICENSE]

MIT License. Designed with retro-computing nostalgia for clear, stress-free group accounting.
