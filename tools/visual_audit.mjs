import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const outDir = '_source_snapshot/visual-audit';
await fs.mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const report = { generatedAt: new Date().toISOString(), views: [], consoleErrors: [], pageErrors: [] };

async function auditViewport(name, viewport, anchors) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.on('console', message => {
    if (message.type() === 'error') report.consoleErrors.push({ view: name, text: message.text() });
  });
  page.on('pageerror', error => report.pageErrors.push({ view: name, text: String(error) }));

  await page.goto('http://127.0.0.1:8000/exact-preview.html', {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(8000);

  const metrics = await page.evaluate(() => {
    const images = [...document.images];
    const rect = element => {
      const r = element?.getBoundingClientRect();
      return r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null;
    };
    return {
      title: document.title,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      document: {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight,
      },
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
      headerRect: rect(document.querySelector('header')),
      mainRect: rect(document.querySelector('main')),
      sourceSegments: document.querySelectorAll('[data-onepage-source]').length,
      pageSections: document.querySelectorAll('section.page-section').length,
      images: {
        total: images.length,
        loaded: images.filter(image => image.complete && image.naturalWidth > 0).length,
        broken: images.filter(image => image.complete && image.naturalWidth === 0).map(image => image.currentSrc || image.src),
      },
      youtubeIframes: [...document.querySelectorAll('iframe')].filter(frame => /youtube/.test(frame.src)).length,
      nativeVideos: document.querySelectorAll('video[data-native-id]').length,
      exactAnchors: [...document.querySelectorAll('.onepage-anchor')].map(anchor => anchor.id),
      bodyBackground: getComputedStyle(document.body).backgroundColor,
      bodyFont: getComputedStyle(document.body).fontFamily,
    };
  });

  report.views.push({ name, ...metrics });

  const shots = [];
  for (const anchor of anchors) {
    if (anchor !== 'home') {
      await page.locator(`#${anchor}`).scrollIntoViewIfNeeded();
      await page.waitForTimeout(1200);
    } else {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(600);
    }
    const file = path.join(outDir, `${name}-${anchor}.jpg`);
    await page.screenshot({ path: file, type: 'jpeg', quality: 46, fullPage: false });
    shots.push(file);
  }

  await context.close();
  return shots;
}

const desktopShots = await auditViewport('desktop', { width: 1440, height: 1000 }, [
  'home',
  'services',
  'about',
  'gallery',
  'contact',
  'faq',
]);
const mobileShots = await auditViewport('mobile', { width: 390, height: 844 }, [
  'home',
  'gallery',
  'contact',
]);

await browser.close();
await fs.writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
await fs.writeFile(path.join(outDir, 'shots.json'), JSON.stringify({ desktopShots, mobileShots }, null, 2));

if (report.views.some(view => view.horizontalOverflow)) process.exitCode = 2;
if (report.views.some(view => view.images.broken.length > 0)) process.exitCode = 3;
