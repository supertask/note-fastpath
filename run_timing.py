# -*- coding: utf-8 -*-
"""Timing test runner: full 146-step fastpath against the real (logged-in) Chrome.
Run via: BU_NAME=notetest browser-harness <<PY  (helpers pre-imported)
Measures (1) new-draft full paste pass, (2) update pass (Ctrl+A delete + re-paste).
"""
import os, re, sys, time, json, shutil, socketserver, threading, http.server

HARNESS = r'C:/Users/supertask/AppData/Local/hermes/installs/5162f3ddd4c1a9fc/environments/ac593b0c6d474ced88ce40d84e89a18d/venv/Scripts/browser-harness.exe'
sys.path.insert(0, r'C:/Users/supertask/AppData/Local/hermes/cache/browser-use/workspace/sa-0-ddd75557')
import note_flow as nf

WS = r'C:/Users/supertask/note_paste_v3/timing_test'
os.makedirs(WS, exist_ok=True)
nf.WS = WS
nf.STATE_PATH = os.path.join(WS, 'seq_state.json')
nf.PROG_PATH = os.path.join(WS, 'seq_progress.jsonl')
nf.IMG_ROOT = os.path.join(WS, 'imgs_serve')

# inject helpers
nf.js, nf.cdp, nf.goto_url, nf.fill_input, nf.wait_for_load, nf.new_tab = js, cdp, goto_url, fill_input, wait_for_load, new_tab

seq = nf.load_seq()
n_html = sum(1 for s in seq if s['kind'] == 'html')
n_img = sum(1 for s in seq if s['kind'] == 'img')
print(f'seq: {len(seq)} steps (html {n_html} / img {n_img})')

TITLE = '【計測用】ヘッドレス高速化テスト 2026-11-23（この記事は削除されます）'

# ---- image server
class _H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=nf.IMG_ROOT, **kw)
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        super().end_headers()
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
    def log_message(self, *a):
        pass

os.makedirs(nf.IMG_ROOT, exist_ok=True)
img_map = []
for k, step in enumerate(seq):
    if step['kind'] == 'img':
        dst = os.path.join(nf.IMG_ROOT, f'img{k:03d}.jpg')
        shutil.copy(step['path'], dst)
        img_map.append(k)

srv = socketserver.ThreadingTCPServer(('127.0.0.1', 8941), _H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)
print('img server on 8941, files:', len(img_map))

# ---- clipboard/permissions
try:
    cdp('Browser.grantPermissions', permissions=['clipboardReadWrite', 'clipboardSanitizedWrite'])
except Exception as e:
    print('grant err:', str(e)[:80])
cdp('Page.enable')
cdp('Page.setInterceptFileChooserDialog', enabled=True)

# ---- draft: 既にエディタページが開いていればそれを再利用（ナビゲーション不要→beforeunload回避）
js("window.addEventListener('beforeunload', e => { e.stopImmediatePropagation(); }, true)")
cur = js('location.href') or ''
t0 = time.time()
if 'editor.note.com/notes/' in cur:
    m0 = re.search(r'notes/([a-z0-9]+)/edit', cur)
    note_id, editor_url = (m0.group(1) if m0 else None), cur
    print(f'REUSE existing editor: id={note_id}')
else:
    try:
        note_id, editor_url = nf.open_new_draft()
    except Exception as e:
        print(f'NAV BLOCKED: {str(e)[:60]}')
        raise SystemExit(2)
t_create = time.time() - t0
print(f'DRAFT ready: id={note_id} in {t_create:.1f}s')
if not note_id:
    print('FATAL: no draft'); raise SystemExit(1)
nf.save_state({'mode': 'new', 'note_id': note_id, 'editor_url': editor_url, 'title': TITLE})
time.sleep(1)

nf.set_title(TITLE)
time.sleep(0.5)
nf.cdp('Page.bringToFront')
time.sleep(0.5)
nf.click_body()
time.sleep(0.3)

# check clipboard write works without bringToFront
probe = nf.paste_html_file(seq[0]['path'])
print('clipboard probe (no bringToFront):', str(probe)[:80])

def run_pass(label):
    nf.cdp('Page.bringToFront')
    time.sleep(0.4)
    t_start = time.time()
    html_secs = img_secs = 0.0
    prev_imgs = (nf.pm_stats() or {}).get('imgs', 0)
    for k, step in enumerate(seq):
        ts = time.time()
        # 毎ステップ: 最終テキストへ実クリック（スクロール込み）→文末。画像後の脱落対策の決定版
        nf.click_end_text()
        if step['kind'] == 'html':
            st = nf.paste_html_file(step['path'])
            if st != 'OK':
                print(f'  step {k} html ERR: {str(st)[:60]}')
                nf.cdp('Page.bringToFront'); time.sleep(0.5)
                st = nf.paste_html_file(step['path'])
            # DOM wait: 文字カウンタが増えるのを待つ（最大1.2s）
            t1 = time.time()
            def _chars():
                m = re.search(r'(\d[\d,]*)\s*文字', js('document.body.innerText') or '')
                return int(m.group(1).replace(',', '')) if m else 0
            c0 = _chars()
            while time.time() - t1 < 1.2:
                c1 = _chars()
                if c1 > c0:
                    break
                time.sleep(0.06)
            dt = time.time() - ts
            html_secs += dt
            nf.log_step(k, 'html', dt)
        else:
            # メニューを開く → 画像 → ファイルチューザ Intercept → setFileInputFiles
            js("""(() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.getAttribute('aria-label') === 'メニューを開く'); if (b) b.click(); })()""")
            time.sleep(0.35)
            js("""(() => { const items = Array.from(document.querySelectorAll('button, [role="menuitem"], li')).filter(e => (e.textContent||'').trim() === '画像' && e.offsetParent !== null); if (items.length) items[0].click(); })()""")
            time.sleep(0.45)
            try:
                doc = cdp('DOM.getDocument'); root = doc['root']['nodeId']
                q = cdp('DOM.querySelector', nodeId=root, selector='input[type="file"]')
                nid = q.get('nodeId')
                if not nid:
                    print(f'  step {k} img NO_INPUT'); img_secs += time.time() - ts; continue
                cdp('DOM.setFileInputFiles', files=[step['path']], nodeId=nid)
            except Exception as e:
                print(f'  step {k} img SETFILE ERR: {str(e)[:60]}'); img_secs += time.time() - ts; continue
            st2 = nf.wait_img_uploaded(prev_imgs, timeout=40)
            dt = time.time() - ts
            img_secs += dt
            if st2:
                prev_imgs = st2['imgs']
            nf.log_step(k, 'img', dt, str(prev_imgs))
            if not st2:
                print(f'  step {k} img UPLOAD TIMEOUT')
        if (k + 1) % 20 == 0:
            el = time.time() - t_start
            print(f'  [{label}] step {k+1}/{len(seq)} elapsed {el:.0f}s (html {html_secs:.0f} img {img_secs:.0f})')
    total = time.time() - t_start
    st = nf.pm_stats()
    print(f'[{label}] TOTAL {total:.1f}s = html {html_secs:.1f} + img {img_secs:.1f}; stats {st}')
    return total, html_secs, img_secs, st

t_new, h1, i1, st1 = run_pass('NEW')
ok1, s_save = nf.save_draft()
print('save_draft:', ok1, f'{s_save:.2f}s')

# ---- update pass: 同一タブで全消去→再貼り付け（再読込は別計測済み: 4.7s）
t0 = time.time()
nf.click_end_text()
nf.cdp('Page.bringToFront'); time.sleep(0.3)
nf.key_ctrl_a(); time.sleep(0.3); nf.key_delete(); time.sleep(0.6)
st = nf.pm_stats()
if st and (st['imgs'] > 0 or st['chars'] > 5):
    print('wipe retry (PM focus)')
    js("document.querySelector('.ProseMirror').focus()")
    time.sleep(0.3)
    nf.key_ctrl_a(); time.sleep(0.3); nf.key_delete(); time.sleep(0.6)
    st = nf.pm_stats()
print('after wipe stats:', st)
t_reopen = time.time() - t0
t_upd, h2, i2, st2 = run_pass('UPDATE')
ok2, s_save2 = nf.save_draft()
print('update save:', ok2, f'{s_save2:.2f}s')

out = {
    'note_id': note_id,
    'create_draft_s': round(t_create, 1),
    'new_pass_total_s': round(t_new, 1), 'new_html_s': round(h1, 1), 'new_img_s': round(i1, 1),
    'save_s': round(s_save, 2), 'new_stats': st1,
    'reopen_wipe_s': round(t_reopen, 1),
    'update_pass_total_s': round(t_upd, 1), 'update_html_s': round(h2, 1), 'update_img_s': round(i2, 1),
    'update_save_s': round(s_save2, 2), 'update_stats': st2,
    'clipboard_no_bringToFront': str(probe)[:40],
}
with open(os.path.join(WS, 'timing_result.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('TIMING_RESULT:', json.dumps(out, ensure_ascii=False))
srv.shutdown()
print('done')
