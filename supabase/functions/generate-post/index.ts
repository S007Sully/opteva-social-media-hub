// Opteva — generate-post Edge Function
// Browser calls this; it holds the Claude API key and the business "brain",
// then calls Claude and returns a generated social post.
//
// Secrets required (set in Supabase dashboard -> Edge Functions -> Secrets):
//   ANTHROPIC_API_KEY   your Claude API key (sk-ant-...)
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected automatically.
//
// Model: Claude Haiku 4.5 is the cost-effective default for short social posts
// (~$1/$5 per 1M tokens). Change MODEL to "claude-sonnet-4-6" for higher-quality
// long-form, or "claude-opus-4-8" for maximum quality.

import { createClient } from "jsr:@supabase/supabase-js@2";

const MODEL = "claude-haiku-4-5";
const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);

  const apiKey = Deno.env.get("ANTHROPIC_API_KEY") || Deno.env.get("opteva_demo_key");
  if (!apiKey) return json({ error: "Server is missing the Claude API key secret" }, 500);

  let payload: {
    prompt?: string;
    platform?: string;
    tone?: string;
    business_id?: string;
  };
  try {
    payload = await req.json();
  } catch {
    return json({ error: "Invalid JSON body" }, 400);
  }

  const prompt = (payload.prompt || "").trim() ||
    "booking consultations and growing the business";
  const platform = payload.platform || "Instagram";
  const tone = payload.tone || "Authority";
  const businessId = payload.business_id || "demo";

  // Load the business brain (falls back to a generic brand if no row exists).
  let brain = {
    business_name: "your business",
    industry: "service business",
    services: "",
    brand_voice: "warm, confident, and helpful",
    offers: "",
    audience: "local customers",
  };
  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );
    const { data } = await supabase
      .from("business_brain")
      .select("*")
      .eq("id", businessId)
      .maybeSingle();
    if (data) brain = { ...brain, ...data };
  } catch (_e) {
    // If the table isn't there yet, we still generate using the generic brain.
  }

  const system = [
    `You are the social media manager for ${brain.business_name}, a ${brain.industry}.`,
    brain.services ? `Services offered: ${brain.services}.` : "",
    brain.offers ? `Current offers/promotions: ${brain.offers}.` : "",
    brain.audience ? `Target audience: ${brain.audience}.` : "",
    `Brand voice: ${brain.brand_voice}.`,
    ``,
    `Write a single ${platform} post in a ${tone} style about the topic the user gives.`,
    `Rules: sound human and on-brand, never generic. No hashtags unless they add value.`,
    `Do not use em dashes. Keep it ready to publish. Return only the post text.`,
  ].filter(Boolean).join("\n");

  let claudeRes: Response;
  try {
    claudeRes = await fetch(ANTHROPIC_URL, {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1000,
        system,
        messages: [{ role: "user", content: prompt }],
      }),
    });
  } catch (e) {
    return json({ error: "Could not reach Claude: " + String(e) }, 502);
  }

  if (!claudeRes.ok) {
    const detail = await claudeRes.text();
    return json({ error: "Claude API error", status: claudeRes.status, detail }, 502);
  }

  const result = await claudeRes.json();
  const text = (result.content || [])
    .filter((b: { type: string }) => b.type === "text")
    .map((b: { text: string }) => b.text)
    .join("")
    .trim();

  return json({ text, model: MODEL });
});
