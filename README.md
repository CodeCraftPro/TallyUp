# 🧾 TallyUp

**TallyUp** is a fast, intuitive expense-splitting and trip settlement web application designed for group trips, housemates, and restaurant outings.

---

## ✨ Features

- 👥 **Multi-Trip & Event Management**: Organize expenses by trips (e.g. vacation, road trip, weekend outing) with sub-events (Lunch, Dinner, Fuel, Airbnb, etc.).
- 🍽️ **Restaurant Split Mode**:
  - Handles itemized dishes per diner (shared or individual).
  - Handles off-bill outside items (e.g., drinks, extra snacks).
  - Handles discounts & coupons (e.g. Zomato / Swiggy Dineout 10–50% off).
  - Handles service charge & taxes (CGST / SGST) distributed proportionally.
  - Handles DineCoins / cashbacks applied directly to the payer.
- 📸 **100% Offline Local Receipt Scanner**:
  - Powered by ONNX-accelerated **RapidOCR** (`rapidocr-onnxruntime`).
  - No cloud APIs, no subscriptions, zero external data leakage.
  - Automatically extracts restaurant name, date, subtotal, tax breakdown, and itemized dish names with prices.
  - Drag-and-drop or file upload right into event creation or restaurant bill forms.
- 🖨️ **Thermal Receipt Generator**:
  - Generates traditional 42-column ASCII thermal receipts with monospace receipt typography.
  - Ready to copy and paste directly into WhatsApp, Telegram, or email.
- 💸 **UPI QR Code & Deep Linking**:
  - Automatically formats Indian UPI payment URIs (`upi://pay?pa=...&pn=...&am=...`).
  - Generates scannable QR codes for each debtor to settle debts directly with the payer.
- 🎁 **Treats & Gift Support**:
  - Flag events as "Treat" to record spending without creating debts for diners.
- 📊 **Minimal Transaction Settlement**:
  - Optimized debt simplification algorithm to settle all balances in the minimum number of peer-to-peer transfers.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Virtual environment (`venv`)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/CodeCraftPro/TallyUp.git
   cd TallyUp
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the app:
   ```bash
   python3 app.py
   ```

5. Open your browser at:
   ```
   http://127.0.0.1:5001
   ```

---

## 📁 Project Structure

```
├── app.py                  # Flask application routes, settlement engine, API endpoints
├── receipt_parser.py       # RapidOCR pipeline, heuristics for receipt itemization
├── templates/              # Jinja2 HTML templates
│   ├── base.html           # Base layout, navbar, styles
│   ├── dashboard.html      # Trip list & trip creation
│   ├── trip.html           # Trip dashboard, events, members, ASCII summary
│   ├── add_expense.html    # Simple & Restaurant expense forms, receipt scanner modal
│   ├── settlement.html     # Debt simplification & settlement breakdown
│   ├── pay.html            # UPI payment landing page & QR generator
│   └── setup.html          # Initial configuration
├── data/
│   └── db.json             # Persistent JSON storage for trips, events, expenses
└── requirements.txt        # Python package dependencies
```

---

## 🔒 Privacy & Local-First

TallyUp is designed local-first. All data is persisted in a local JSON file (`data/db.json`), and all receipt scanning is run locally on your CPU/GPU using ONNX Runtime. No financial data or receipt images are sent to any third party.

