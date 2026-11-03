import os
import json
import base64
import urllib.request
import urllib.error
import mimetypes
from typing import Dict, Any, Optional
from receipt_reconciler import reconcile_receipt

RECEIPT_EXTRACTION_PROMPT = """You are an expert financial receipt dissection engine for restaurant and store bills.
Analyze the attached receipt image and extract structured JSON matching EXACTLY this schema:
{
  "venue": "Merchant or restaurant name",
  "date": "YYYY-MM-DD (or empty string if not found)",
  "meal_type": "dinner | lunch | breakfast | drinks | shopping | transport | other",
  "items": [
    {
      "name": "Item or dish name (cleaned, title-cased)",
      "qty": 1,
      "price": 0.0,
      "total": 0.0
    }
  ],
  "subtotal": 0.0,
  "cgst": 0.0,
  "sgst": 0.0,
  "total_tax": 0.0,
  "service_charge": 0.0,
  "convenience_fee": 0.0,
  "discount": 0.0,
  "discount_percent": 0.0,
  "round_off": 0.0,
  "grand_total": 0.0
}

Rules:
1. Strip all currency symbols (₹, $, Rs, etc.) and return pure numbers for monetary amounts.
2. If multiple quantities of an item exist (e.g. 2 x 300.00), set qty=2, price=300.00, total=600.00.
3. If an item name spans multiple lines, join them into a single clean title.
4. If taxes are labeled CGST and SGST, extract them separately and sum into total_tax.
5. If grand_total is not printed, sum subtotal + taxes + service_charge - discount.
6. Return ONLY valid JSON, with no markdown backticks, no preamble, and no explanation."""


def encode_image_base64(image_path: str) -> tuple[str, str]:
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"
    with open(image_path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode("utf-8")
    return b64, mime_type


def call_gemini(b64_img: str, mime_type: str, api_key: str, model: str) -> str:
    model_name = model.strip() if model else "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": RECEIPT_EXTRACTION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_img
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        candidates = res_data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no response candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ValueError("Gemini candidate has no content parts.")
        return parts[0].get("text", "")


def call_openai_compatible(b64_img: str, mime_type: str, api_key: str, model: str, base_url: Optional[str] = None) -> str:
    endpoint_base = (base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{endpoint_base}/chat/completions"
    model_name = model.strip() if model else "gpt-4o-mini"

    payload = {
        "model": model_name,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": RECEIPT_EXTRACTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_img}"
                        }
                    }
                ]
            }
        ]
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=35) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError("OpenAI/Custom endpoint returned no choices.")
        return choices[0].get("message", {}).get("content", "")


def call_claude(b64_img: str, mime_type: str, api_key: str, model: str) -> str:
    url = "https://api.anthropic.com/v1/messages"
    model_name = model.strip() if model else "claude-3-5-sonnet-20241022"

    payload = {
        "model": model_name,
        "max_tokens": 2048,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_img
                        }
                    },
                    {
                        "type": "text",
                        "text": RECEIPT_EXTRACTION_PROMPT + "\nOutput raw JSON only."
                    }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=35) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        content_list = res_data.get("content", [])
        for c in content_list:
            if c.get("type") == "text":
                return c.get("text", "")
        raise ValueError("Anthropic returned no text content in response.")


def extract_json_from_llm_output(raw_text: str) -> Dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip markdown fences
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


def scan_receipt_with_llm(image_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scans a receipt image with the configured LLM provider and validates through
    the 6-phase reconciliation engine.
    """
    provider = config.get("provider", "gemini").lower()
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "").strip()
    base_url = config.get("base_url", "").strip()

    b64_img, mime_type = encode_image_base64(image_path)

    raw_response = ""
    if provider == "gemini":
        raw_response = call_gemini(b64_img, mime_type, api_key, model or "gemini-1.5-flash")
    elif provider in ("openai", "chatgpt"):
        raw_response = call_openai_compatible(b64_img, mime_type, api_key, model or "gpt-4o-mini", "https://api.openai.com/v1")
    elif provider in ("claude", "anthropic"):
        raw_response = call_claude(b64_img, mime_type, api_key, model or "claude-3-5-sonnet-20241022")
    elif provider == "custom":
        raw_response = call_openai_compatible(b64_img, mime_type, api_key, model or "default", base_url)
    else:
        raise ValueError(f"Unsupported AI provider: '{provider}'")

    parsed_json = extract_json_from_llm_output(raw_response)

    # Harmonize field names
    venue = parsed_json.get("venue") or parsed_json.get("restaurant") or parsed_json.get("merchant") or ""
    date = parsed_json.get("date") or ""
    meal_type = parsed_json.get("meal_type") or "dinner"

    # Pipe through our strict 6-phase reconciliation engine
    reconciled = reconcile_receipt(parsed_json)
    repaired = reconciled.get("repaired_payload", {})

    items_list = []
    for it in repaired.get("items", []):
        items_list.append({
            "name": it.get("name", "Item"),
            "price": f"{float(it.get('price', 0.0)):.2f}",
            "qty": it.get("qty", 1),
            "total": f"{float(it.get('total', 0.0)):.2f}",
            "is_outside": False,
            "paid_by": "",
            "consumed_by": [],
            "portions": it.get("portions", [])
        })

    return {
        "success": True,
        "engine": f"LLM: {provider.upper()} ({model or 'default'})",
        "venue": venue,
        "date": date,
        "meal_type": meal_type,
        "items": items_list,
        "subtotal": f"{repaired.get('subtotal', 0.0):.2f}",
        "total_bill": f"{repaired.get('grand_total', 0.0):.2f}",
        "taxes": f"{repaired.get('total_tax', 0.0):.2f}",
        "tax_percent": f"{repaired.get('tax_percent', 0.0):.1f}",
        "service_charge": f"{repaired.get('service_charge', 0.0):.2f}",
        "convenience_fee": f"{repaired.get('convenience_fee', 0.0):.2f}",
        "discount": f"{repaired.get('discount', 0.0):.2f}",
        "discount_percent": f"{repaired.get('discount_percent', 0.0):.1f}",
        "confidence": round(reconciled.get("confidence_score", 0.9) * 100),
        "is_valid": reconciled.get("is_valid", True),
        "reconciliation_notes": reconciled.get("reconciliation_notes", [])
    }

