// note投稿ランナー（Playwright Chromium・ヘッドあり＋画面外配置）
// 使い方:
//   node pw_note.js draft  <タイトル> <本文htmlのパス> [画像パス...]
//   node pw_note.js check                     （ログイン状態の確認のみ）
// 認証: ~/.note-state.json（storageState形式）
// 注意: ヘッドレスは note に拒否される（真っ白になる）ため headless:false 固定。
//       ウィンドウは画面外に出して操作の邪魔をしない。
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const STATE_PATH = process.env.NOTE_STATE_PATH || path.join(require('os').homedir(), '.note-state.json');

async function launch(contextOpts = {}) {
  const browser = await chromium.launch({
    headless: false,
    args: ['--lang=ja-JP', '--window-position=--12000,0'],
  });
  const context = await browser.newContext({
    storageState: STATE_PATH,
    locale: 'ja-JP',
    viewport: { width: 1400, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  page.setDefaultTimeout(45000);
  return { browser, context, page };
}

async function checkLogin(page) {
  await page.goto('https://editor.note.com/new', { waitUntil: 'networkidle', timeout: 45000 })
    .catch(() => {});
  await page.waitForTimeout(6000);
  const title = await page.$('textarea[placeholder*="タイトル"]');
  const url = page.url();
  const m = url.match(/notes\/(n[a-z0-9]+)/);
  return { loggedIn: !!title, draftId: m ? m[1] : null, url };
}

async function createDraft(page, title, htmlPath, imagePaths = []) {
  // 新規エディタを開く（自動で下書きが作られる）
  const st = await checkLogin(page);
  if (!st.loggedIn) throw new Error('NOT_LOGGED_IN: ' + st.url);
  await page.$eval('textarea[placeholder*="タイトル"]', el => { el.focus(); });
  await page.fill('textarea[placeholder*="タイトル"]', title);
  // 本文HTMLを1発貼り（単一断片はマージされるため全文1回で）
  const html = fs.readFileSync(htmlPath, 'utf-8');
  const pm = await page.$('.ProseMirror');
  if (!pm) throw new Error('NO_PM');
  await pm.click();
  await page.keyboard.press('Control+End');
  await page.evaluate(async (h) => {
    await navigator.clipboard.write([
      new ClipboardItem({
        'text/html': new Blob([h], { type: 'text/html' }),
        'text/plain': new Blob([h.replace(/<[^>]+>/g, '')], { type: 'text/plain' }),
      }),
    ]);
  }, html);
  await page.keyboard.press('Control+v');
  await page.waitForTimeout(2500);
  // 画像挿入（メニュー→ファイルチューザ）
  for (const imgPath of imagePaths) {
    const abs = path.resolve(imgPath);
    page.on('filechooser', () => {}); // no-op（interceptはCdpでなくPlaywright標準で拾う）
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser', { timeout: 10000 }),
      (async () => {
        const menuBtn = await page.$('button[aria-label*="メニュー"]');
        if (!menuBtn) throw new Error('NO_MENU');
        await menuBtn.click();
        await page.waitForTimeout(900);
        const items = await page.$$('text="画像"');
        for (const it of items.reverse()) {
          if (await it.isVisible()) { await it.click(); return; }
        }
        throw new Error('NO_IMG_ITEM');
      })(),
    ]);
    await chooser.setFiles(abs);
    // アップロード完了待ち（figure の枚数が増えるまで）
    const before = await page.$$eval('.ProseMirror figure', els => els.length);
    await page.waitForFunction(
      (n) => document.querySelectorAll('.ProseMirror figure').length > n,
      before, { timeout: 30000 },
    );
    await page.waitForTimeout(1200);
  }
  // 保存してURL（下書きID）を返す
  await page.waitForTimeout(2000);
  const saveBtn = await page.$('button:has-text("下書き保存")');
  if (saveBtn) { await saveBtn.click(); await page.waitForTimeout(2500); }
  const m = page.url().match(/notes\/(n[a-z0-9]+)/);
  const stats = await page.evaluate(() => {
    const pm2 = document.querySelector('.ProseMirror');
    return pm2 ? {
      chars: (pm2.innerText || '').replace(/\s/g, '').length,
      imgs: document.querySelectorAll('.ProseMirror figure img[src*="assets"]').length,
    } : null;
  });
  return { draftId: m ? m[1] : null, stats };
}

(async () => {
  const [, , cmd, ...rest] = process.argv;
  if (cmd === 'check') {
    const { browser, page } = await launch();
    const st = await checkLogin(page);
    console.log(JSON.stringify({ ok: true, ...st }));
    await browser.close();
    return;
  }
  if (cmd === 'draft') {
    const [title, htmlPath, ...imgs] = rest;
    if (!title || !htmlPath) { console.error('usage: node pw_note.js draft <title> <html> [imgs...]'); process.exit(1); }
    const { browser, page } = await launch();
    try {
      const r = await createDraft(page, title, htmlPath, imgs);
      console.log(JSON.stringify({ ok: true, ...r }));
    } finally {
      await browser.close();
    }
    return;
  }
  console.error('usage: node pw_note.js check | draft <title> <html> [imgs...]');
  process.exit(1);
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
