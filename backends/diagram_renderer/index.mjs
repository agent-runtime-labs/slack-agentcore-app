// Mermaid source in, PNG out. Invoked (IAM-authenticated) by the agent's draw_diagram tool.
//
//   event:    { "mermaid": "flowchart LR\n A --> B" }
//   returns:  { "png_base64": "..." }
//          or { "error": "invalid_source" | "too_large" | "empty", "detail": "..." }
//
// A browser failure throws instead, so the caller sees a Lambda FunctionError and treats
// the renderer as unavailable. This function has no secrets and no table access, and it
// never logs the diagram source: that is the point of running it here and not on kroki.io.

import { renderMermaid } from "@mermaid-js/mermaid-cli";
import chromium from "@sparticuz/chromium-min";
import puppeteer from "puppeteer";

// The arm64 Chromium pack unpacked by the Dockerfile (checksum-verified there).
const CHROMIUM_PACK = process.env.CHROMIUM_PACK_DIR ?? "/opt/chromium";

const MAX_SOURCE_CHARS = 20_000;
// A synchronous Lambda response is capped at 6 MB and base64 adds a third.
const MAX_PNG_BYTES = 4_000_000;
const MAX_DETAIL_CHARS = 600;

// Mermaid's default theme draws chart series in near-white lavender, which is unreadable
// for metrics. These are distinguishable on white and for most colour-blind readers.
const PALETTE = ["#2563EB", "#DC2626", "#16A34A", "#D97706", "#7C3AED", "#0891B2", "#DB2777", "#65A30D"];
const CHART_COLOURS = {
  xyChart: { plotColorPalette: PALETTE.join(",") }, // line and bar series, in order
  ...Object.fromEntries(PALETTE.map((colour, i) => [`pie${i + 1}`, colour])),
  pieSectionTextColor: "#FFFFFF",
  pieStrokeColor: "#FFFFFF",
  pieStrokeWidth: "2px",
  pieOuterStrokeColor: "#FFFFFF",
};

let browserPromise;

async function launch() {
  return puppeteer.launch({
    args: chromium.args,
    executablePath: await chromium.executablePath(CHROMIUM_PACK),
    headless: "shell",
  });
}

// One browser per warm container; relaunched if it died between invocations.
async function getBrowser() {
  if (browserPromise) {
    const browser = await browserPromise.catch(() => null);
    if (browser?.connected) return browser;
  }
  browserPromise = launch();
  return browserPromise;
}

// Mermaid's message without the stack frames the browser appends after it.
function reasonOf(err) {
  const lines = String(err?.message ?? err).split("\n");
  const end = lines.findIndex((line) => /^\s*at |\(https?:|\.mjs:\d+/.test(line) || /^\w+\.\w+ \(/.test(line));
  return (end === -1 ? lines : lines.slice(0, end)).join("\n").slice(0, MAX_DETAIL_CHARS);
}

export const handler = async (event) => {
  const source = typeof event?.mermaid === "string" ? event.mermaid.trim() : "";
  if (!source) return { error: "empty", detail: "the Mermaid source is empty" };
  if (source.length > MAX_SOURCE_CHARS) return { error: "too_large", detail: "the diagram is too large" };

  const started = Date.now();
  const browser = await getBrowser();
  let data;
  try {
    ({ data } = await renderMermaid(browser, source, "png", {
      backgroundColor: "white", // opaque: a transparent PNG is unreadable on Slack's dark theme
      viewport: { width: 1200, height: 800, deviceScaleFactor: 2 },
      // "strict" encodes HTML in labels and turns off click handlers.
      mermaidConfig: { theme: "default", securityLevel: "strict", themeVariables: CHART_COLOURS },
    }));
  } catch (err) {
    // A dead browser is our fault: let it surface as a function error.
    if (!browser.connected) throw err;
    // Otherwise Mermaid refused the source. Its message names the line, never the whole diagram.
    console.log(`invalid_source after ${Date.now() - started} ms`);
    return { error: "invalid_source", detail: reasonOf(err) };
  }

  const png = Buffer.from(data);
  console.log(`rendered ${png.length} bytes from ${source.length} chars in ${Date.now() - started} ms`);
  if (png.length > MAX_PNG_BYTES) return { error: "too_large", detail: "the diagram is too large" };
  return { png_base64: png.toString("base64") };
};
