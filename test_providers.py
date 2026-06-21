#!/usr/bin/env python3
"""Test script for ALL AI providers — v5.0 with Pollinations API key testing.

Run: python3 test_providers.py

Pollinations auth API is tested with API key (CHAT/FUNCTION models).
Other providers require API keys in environment or .env file.

Expected output:
  ✅ Pollinations Auth (openai) — responds with Russian text (5/5 quality)
  ✅ Pollinations Auth (mistral) — responds with Russian text (4/5 quality)
  ✅ Pollinations Free JSON — responds with Russian text (fallback)
  
For other providers, you need to set the API keys:
  export GROQ_API_KEY=your_key
  export GEMINI_API_KEY=your_key
  export GH_PAT_TOKEN=your_pat
  export OPENROUTER_API_KEY=your_key
  export CEREBRAS_API_KEY=your_key
  export POLLINATIONS_API_KEY=your_key
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# Import providers
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.groq_provider import GroqProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.cerebras_provider import CerebrasProvider


async def test_provider(name, provider, test_prompt=None, route_type="chat", max_tokens=200):
    """Test a single provider with a Russian prompt."""
    if test_prompt is None:
        test_prompt = "Привет! Скажи мне два-три слова о мебели на русском языке."
    
    messages = [
        {"role": "system", "content": "Ты — Даша, дизайнер мебели из Абакана. Отвечай на русском."},
        {"role": "user", "content": test_prompt},
    ]
    
    print(f"\n{'='*60}")
    print(f"🧪 Testing: {name} (route={route_type})")
    
    try:
        available = await provider.is_available()
        if not available:
            print(f"   ⏭️  NOT CONFIGURED (no API key) — skipping")
            return None
        
        print(f"   📤 Sending request...")
        start = time.time()
        result = await provider.chat(
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens,
            route_type=route_type,
        )
        elapsed = time.time() - start
        
        if result.ok:
            print(f"   ✅ SUCCESS ({elapsed:.1f}s, {result.latency_ms:.0f}ms)")
            print(f"   📝 Provider: {result.provider}")
            print(f"   📝 Model: {result.model}")
            print(f"   📝 Response: {result.text[:200]}")
            return True
        else:
            print(f"   ❌ FAILED: {result.error}")
            return False
    except Exception as e:
        print(f"   ❌ EXCEPTION: {e}")
        return False


async def main():
    print("=" * 60)
    print("🤖 Dasha Bot — AI Provider Test Suite v6.0")
    print("=" * 60)
    print()
    print("Цепочка фолбэка:")
    print("  Local → GitHub → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → Pollinations")
    print()
    print("Pollinations v6.0 ROUTE STRATEGY:")
    print("  CHAT: gpt-5.4-mini → nova-fast → minimax → mistral-small-3.2 → ... (16 models)")
    print("  FUNCTION: nova → gpt-5.4-mini → openai → mistral-large → ... (12 models)")
    print("  COMMENT: Free API only (preserves key quota)")
    print()
    print("NEW v6.0 models (8): gpt-5.4-mini, nova, nova-fast, minimax, minimax-m2.7,")
    print("                     perplexity-fast, step-3.5-flash, grok-large")
    print()
    
    # Check configured providers
    print("📋 Configured API keys:")
    providers_status = {
        "GitHub PAT": os.getenv("GH_PAT_TOKEN", ""),
        "HuggingFace": os.getenv("HF_TOKEN", ""),
        "Groq": os.getenv("GROQ_API_KEY", ""),
        "Gemini": os.getenv("GEMINI_API_KEY", ""),
        "OpenRouter": os.getenv("OPENROUTER_API_KEY", ""),
        "Cerebras": os.getenv("CEREBRAS_API_KEY", ""),
        "Pollinations": os.getenv("POLLINATIONS_API_KEY", "") or "free-only",
    }
    for name, key in providers_status.items():
        if key == "free-only":
            print(f"  ⏭️  {name}: free tier only (no key)")
        elif key:
            print(f"  ✅ {name}: {key[:8]}...")
        else:
            print(f"  ⏭️  {name}: not configured")
    
    # Test providers in fallback order
    results = {}
    
    # 1. GitHub Models (free via PAT)
    if os.getenv("GH_PAT_TOKEN"):
        gh = GitHubModelsProvider(api_key=os.getenv("GH_PAT_TOKEN"))
        results["GitHub Models"] = await test_provider("GitHub Models (GPT-4.1-mini)", gh)
        await asyncio.sleep(1)
    
    # 2. HuggingFace (free via HF_TOKEN)
    if os.getenv("HF_TOKEN"):
        hf = HuggingFaceProvider(api_key=os.getenv("HF_TOKEN"))
        results["HuggingFace"] = await test_provider("HuggingFace (Qwen2.5-7B)", hf)
        await asyncio.sleep(1)
    
    # 3. Groq (free, ultra-fast)
    if os.getenv("GROQ_API_KEY"):
        groq = GroqProvider(api_key=os.getenv("GROQ_API_KEY"))
        results["Groq"] = await test_provider("Groq (Llama-3.3-70B)", groq)
        await asyncio.sleep(1)
    
    # 4. Gemini (free)
    if os.getenv("GEMINI_API_KEY"):
        gemini = GeminiProvider(api_key=os.getenv("GEMINI_API_KEY"))
        results["Gemini"] = await test_provider("Gemini (2.0-Flash)", gemini)
        await asyncio.sleep(1)
    
    # 5. OpenRouter (free)
    if os.getenv("OPENROUTER_API_KEY"):
        orr = OpenRouterProvider(api_key=os.getenv("OPENROUTER_API_KEY"))
        results["OpenRouter"] = await test_provider("OpenRouter (Llama-3.3-70B:free)", orr)
        await asyncio.sleep(1)
    
    # 6. Cerebras (free, ultra-fast)
    if os.getenv("CEREBRAS_API_KEY"):
        cerebras = CerebrasProvider(api_key=os.getenv("CEREBRAS_API_KEY"))
        results["Cerebras"] = await test_provider("Cerebras (Llama-3.3-70B)", cerebras)
        await asyncio.sleep(1)
    
    # 7. Pollinations Auth (CHAT route — uses API key)
    poll_auth = PollinationsProvider(
        api_key=os.getenv("POLLINATIONS_API_KEY", ""),
    )
    results["Pollinations Auth (CHAT)"] = await test_provider(
        "Pollinations Auth — CHAT route", poll_auth,
        route_type="chat",
    )
    await asyncio.sleep(6)  # Rate limit!
    
    # 8. Pollinations Auth (FUNCTION route — uses API key)
    poll_auth2 = PollinationsProvider(
        api_key=os.getenv("POLLINATIONS_API_KEY", ""),
    )
    results["Pollinations Auth (FUNCTION)"] = await test_provider(
        "Pollinations Auth — FUNCTION route", poll_auth2,
        test_prompt="Напиши короткий пост о дизайне кухни для канала.",
        route_type="function",
        max_tokens=300,
    )
    await asyncio.sleep(6)  # Rate limit!
    
    # 9. Pollinations Free (COMMENT route — no API key, free tier only)
    poll_free = PollinationsProvider(api_key="")  # No key = free tier only
    results["Pollinations Free (COMMENT)"] = await test_provider(
        "Pollinations Free — COMMENT route", poll_free,
        route_type="comment",
    )
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 RESULTS SUMMARY")
    print(f"{'='*60}")
    
    success = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    
    for name, result in results.items():
        if result is True:
            print(f"  ✅ {name}")
        elif result is False:
            print(f"  ❌ {name}")
        else:
            print(f"  ⏭️  {name} (not configured)")
    
    print(f"\n  Total: {success} ✅ | {failed} ❌ | {skipped} ⏭️")
    
    if not any(v is True for v in results.values()):
        print("\n⚠️  No providers tested successfully!")
        print("   Set at least one API key to test cloud providers.")
        print("   Pollinations should work without a key (free tier).")
    else:
        print(f"\n🎉 {success} provider(s) working!")


if __name__ == "__main__":
    asyncio.run(main())
