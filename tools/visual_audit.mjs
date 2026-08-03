import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const outDir = '_source_snapshot/visual-audit';
await fs.mkdir(outDir, { recursive: true });

const EXPECTED_YOUTUBE_IFRAMES = 16;
const EXPECTED_CONVERTED_YOUTUBE_BLOCKS = 7;
const EXPECTED_NATIVE_VIDEOS = 24;
const EXPECTED_SOURCE_SEGMENTS = 11;
const EXPECTED_IMAGES = 66;

const browser = await chromium.launch({ headless: true });
const report = { generatedAt: new Date().toISOString(), views: [], consoleErrors: [], pageErrors: [] };

async function scrollToAnchor(page, anchor) {
  await page.evaluate((anchorId) => {
    const target = document.getElementById(anchorId);
    if (!target) throw new Error(`Missing anchor: ${anchorId}`);
    const header = document.querySelector('header');
    const headerHeight = header ? header.getBoundingClientRect().height : 0;
    const top = target.getBoundingClientRect().top + window.scrollY - headerHeight - 12;
    window.scrollTo({ top: Math.max(0, top), behavior: 'instant' });
  }, anchor);
  await page.waitForTimeout(1200);
}

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
  await page.waitForTimeout(10000);

  const metrics = await page.evaluate(() => {
    const images = [...document.images];
    const videos = [...document.querySelectorAll('video[data-native-id]')];
    const youtubeFrames = [...document.querySelectorAll('iframe')].filter(frame => /youtube\.com\/embed\//.test(frame.src));
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
      youtubeIframes: youtubeFrames.length,
      youtubeIds: youtubeFrames.map(frame => {
        const match = frame.src.match(/youtube\.com\/embed\/([A-Za-z0-9_-]{11})/);
        return match ? match[1] : '';
      }).filter(Boolean),
      convertedYoutubeBlocks: Number(document.documentElement.dataset.exactYoutubeBlocksConverted || 0),
      nativeVideos: videos.length,
      nativeVideoStates: videos.map(video => ({
        id: video.dataset.nativeId || '',
        source: video.dataset.hlsSrc || '',
        currentSrc: video.currentSrc || '',
        readyState: video.readyState,
        networkState: video.networkState,
      })),
      exactAnchors: [...document.querySelectorAll('.onepage-anchor')].map(anchor => anchor.id),
      bodyBackground: getComputedStyle(document.body).backgroundColor,
      bodyFont: getComputedStyle(document.body).fontFamily,
    };
  });

  report.views.push({ name, ...metrics });

  const shots = [];
  for (const anchor of anchors) {
    if (anchor === 'home') {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.waitForTimeout(800);
    } else {
      await scrollToAnchor(page, anchor);
    }
    const file = path.join(outDir, `${name}-${anchor}.jpg`);
    await page.screenshot({ path: file, type: 'jpeg', quality: 52, fullPage: false });
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
  'faq',
]);

await browser.close();
await fs.writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
await fs.writeFile(path.join(outDir, 'shots.json'), JSON.stringify({ desktopShots, mobileShots }, null, 2));

const failed = report.views.some(view =>
  view.horizontalOverflow ||
  view.images.total !== EXPECTED_IMAGES ||
  view.images.loaded !== EXPECTED_IMAGES ||
  view.images.broken.length > 0 ||
  view.youtubeIframes !== EXPECTED_YOUTUBE_IFRAMES ||
  view.convertedYoutubeBlocks !== EXPECTED_CONVERTED_YOUTUBE_BLOCKS ||
  view.nativeVideos !== EXPECTED_NATIVE_VIDEOS ||
  view.sourceSegments !== EXPECTED_SOURCE_SEGMENTS
);

if (failed || report.pageErrors.length > 0) process.exitCode = 1;
