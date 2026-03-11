import { kv } from "@vercel/kv";

const KEY = "predCalibration_v1";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();

  if (req.method === "GET") {
    const data = await kv.get(KEY);
    return res.json(data ?? []);
  }

  if (req.method === "POST") {
    if (!Array.isArray(req.body)) {
      return res.status(400).json({ error: "Expected array" });
    }
    await kv.set(KEY, req.body);
    return res.json({ ok: true });
  }

  return res.status(405).json({ error: "Method not allowed" });
}
