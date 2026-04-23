#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!key.startsWith("--")) {
      throw new Error(`Unexpected argument: ${key}`);
    }
    if (value === undefined || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    args[key.slice(2)] = value;
    i += 1;
  }
  return args;
}

function requireArg(args, name) {
  const value = args[name];
  if (!value) {
    throw new Error(`Missing required argument --${name}`);
  }
  return value;
}

function resolveChromePath(explicitPath) {
  if (explicitPath && fs.existsSync(explicitPath)) {
    return explicitPath;
  }

  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }

  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser"
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  throw new Error("Chrome/Chromium executable not found. Pass --chrome-path explicitly.");
}

async function main() {
  const args = parseArgs(process.argv);
  const lottiePath = requireArg(args, "lottie");
  const outputDir = requireArg(args, "output-dir");
  const width = Number(requireArg(args, "width"));
  const height = Number(requireArg(args, "height"));
  const totalFrames = Number(requireArg(args, "frames"));
  const sourceFrames = Number(args["source-frames"] || String(totalFrames));
  const sourceFps = Number(args["source-fps"] || "60");
  const renderFps = Number(args["render-fps"] || String(sourceFps));
  const scaleFactor = Number(args["scale-factor"] || "2");
  const transparent = String(args["transparent"] || "0") === "1";
  const pageBackground = transparent ? "#00ff00" : "transparent";
  const chromePath = resolveChromePath(args["chrome-path"]);

  const lottieAnimation = JSON.parse(fs.readFileSync(lottiePath, "utf8"));
  const lottiePlayer = fs.readFileSync(
    path.join(__dirname, "node_modules", "lottie-web", "build", "player", "lottie.min.js"),
    "utf8"
  );

  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: true,
    args: [
      "--allow-file-access-from-files",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--no-default-browser-check",
      "--no-first-run",
      "--no-sandbox",
      "--disable-setuid-sandbox"
    ],
    defaultViewport: {
      width,
      height,
      deviceScaleFactor: scaleFactor
    }
  });

  const page = await browser.newPage();
  await page.setContent(
    `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: ${pageBackground};
        overflow: hidden;
        width: ${width}px;
        height: ${height}px;
      }
      #app {
        width: ${width}px;
        height: ${height}px;
        background: ${pageBackground};
      }
    </style>
  </head>
  <body>
    <div id="app"></div>
    <script>${lottiePlayer}</script>
    <script>
      window.__animationData = ${JSON.stringify(lottieAnimation)};
      window.__animation = lottie.loadAnimation({
        container: document.getElementById("app"),
        renderer: "svg",
        loop: false,
        autoplay: false,
        animationData: window.__animationData,
        rendererSettings: {
          preserveAspectRatio: "xMidYMid meet",
          progressiveLoad: false
        }
      });
    </script>
  </body>
</html>`,
    { waitUntil: "load" }
  );

  await page.waitForFunction(() => {
    return window.__animation && window.__animation.isLoaded;
  });

  const resolveSourceFrame = (frameIndex) => {
    if (totalFrames <= 1 || sourceFrames <= 1 || renderFps <= 0 || sourceFps <= 0) {
      return 0;
    }
    const durationSeconds = sourceFrames / sourceFps;
    const frameTime = Math.min(frameIndex / renderFps, durationSeconds);
    return Math.min(sourceFrames - 1, Math.round(frameTime * sourceFps));
  };

  for (let frame = 0; frame < totalFrames; frame += 1) {
    const sourceFrame = resolveSourceFrame(frame);
    await page.evaluate((frameNumber) => {
      window.__animation.goToAndStop(frameNumber, true);
      return new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(resolve));
      });
    }, sourceFrame);

    const framePath = path.join(outputDir, `frame_${String(frame).padStart(5, "0")}.png`);
    await page.screenshot({
      path: framePath,
      omitBackground: transparent
    });

    if (
      frame === 0 ||
      frame === totalFrames - 1 ||
      frame % Math.max(1, Math.floor(totalFrames / 50)) === 0
    ) {
      console.log(`FRAME_PROGRESS ${frame + 1} ${totalFrames}`);
    }
  }

  await browser.close();
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
