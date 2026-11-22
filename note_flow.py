# -*- coding: utf-8 -*-
"""note.com headless fast-path runner (staged for the dummy-article timing test).

Usage inside browser_exec calls:
    import sys, os
    sys.path.insert(0, os.environ['BH_AGENT_WORKSPACE'])
    import note_flow as nf

State persists ONLY via files in the browser workspace:
    seq_state.json   {mode: new|update, note_id, editor_url, done_steps, ...}
    seq_progress.jsonl  one line per completed step
Prereq: user must have logged in to note.com in this headless session ONCE
(session cookies persist in the browser daemon).
"""
import base64, json, os, re, shutil, socketserver, threading, time, http.server

WS = os.environ.get('BH_AGENT_WORKSPACE', '.')
SEQ_PATH = r'C:/Users/supertask/note_paste_v3/seq.json'
STATE_PATH = os.path.join(WS, 'seq_state.json')
PROG_PATH = os.path.join(WS, 'seq_progress.jsonl')
IMG_ROOT = os.path.join(WS, 'imgs_serve')

# Browser helpers (js, cdp, goto_url, fill_input, wait_for_load, new_tab) are
# injected by the CALLER right after import, e.g.:
#     import note_flow as nf
#     nf.js, nf.cdp, nf.goto_url, nf.fill_input, nf.wait_for_load = js, cdp, goto_url, fill_input, wait_for_load
# Every function below uses these module-level names.

EDITOR_ORIGIN = 'https://editor.note.com'

# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_state(st):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)

def load_seq():
    with open(SEQ_PATH, encoding='utf-8') as f:
        return json.load(f)

def done_steps():
    if not os.path.exists(PROG_PATH):
        return []
    out = []
    with open(PROG_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(line.split('\t')[0])
    return out

def log_step(i, kind, secs, extra=''):
    with open(PROG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'{i}\t{kind}\t{secs:.3f}\t{extra}\n')

# ---------------------------------------------------------------- local image server
class _H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=IMG_ROOT, **kw)
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        super().end_headers()
    def log_message(self, *a):
        pass

class ImgServer:
    def __init__(self, port=8941):
        os.makedirs(IMG_ROOT, exist_ok=True)
        for k, step in enumerate(load_seq()):
            if step['kind'] == 'img':
                dst = os.path.join(IMG_ROOT, f'img{k:03d}.jpg')
                shutil.copy(step['path'], dst)
        self.httpd = socketserver.ThreadingTCPServer(('127.0.0.1', port), _H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)
        self.port = port
    def url(self, idx):
        return f'http://127.0.0.1:{self.port}/img{idx:03d}.jpg'
    def shutdown(self):
        self.httpd.shutdown()

# ---------------------------------------------------------------- browser helpers (assume helpers pre-imported: js, cdp, ...)
def grant_clip():
    cdp('Browser.grantPermissions', permissions=['clipboardReadWrite', 'clipboardSanitizedWrite'])
    cdp('Page.bringToFront')
    time.sleep(0.2)
    hf = js('document.hasFocus()')
    if not hf:
        time.sleep(0.5)
    return hf

def paste_html_file(path):
    """text/html ClipboardItem -> Ctrl+V. Returns (status, secs)."""
    html = open(path, encoding='utf-8').read()
    esc = html.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
    r = js(f'''(() => {{
      const html = `{esc}`;
      return navigator.clipboard.write([new ClipboardItem({{'text/html': new Blob([html], {{type:'text/html'}})}})]).then(()=>'OK').catch(e=>'ERR:'+e.message);
    }})()''')
    if r != 'OK':
        return r
    key_ctrl_v()
    return 'OK'

def caret_to_doc_end():
    """キャレットを本文の最終テキスト末尾に置く（画像挿入後の復帰用）。
    click_body の中央クリックは長文では画像を拾って失敗するためこちらを使う。
    最後の「テキストノード」に置いてから Ctrl+End で文末に移動する（画像figureに
    直接置くとvoid扱いで貼り付けが落ちる）。"""
    return js("""(() => {
      const pm = document.querySelector('.ProseMirror');
      if (!pm) return 'NO_PM';
      const walker = document.createTreeWalker(pm, NodeFilter.SHOW_TEXT);
      let last = null, n;
      while ((n = walker.nextNode())) { if (n.textContent.trim()) last = n; }
      if (!last) return 'NO_TEXT';
      const sel = window.getSelection();
      const rng = document.createRange();
      rng.setStart(last, last.textContent.length);
      rng.collapse(true);
      sel.removeAllRanges();
      sel.addRange(rng);
      return 'CARET_SET';
    })""")

def key_ctrl_v():
    cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, key='Control', code='ControlLeft', windowsVirtualKeyCode=17)
    cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, key='v', code='KeyV', windowsVirtualKeyCode=86, text='v')
    cdp('Input.dispatchKeyEvent', type='keyUp', modifiers=2, key='v', code='KeyV', windowsVirtualKeyCode=86)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Control', code='ControlLeft', windowsVirtualKeyCode=17)

def key_ctrl_a():
    cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, key='Control', code='ControlLeft', windowsVirtualKeyCode=17)
    cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, key='a', code='KeyA', windowsVirtualKeyCode=65, text='a')
    cdp('Input.dispatchKeyEvent', type='keyUp', modifiers=2, key='a', code='KeyA', windowsVirtualKeyCode=65)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Control', code='ControlLeft', windowsVirtualKeyCode=17)

def key_ctrl_end():
    cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, key='Control', code='ControlLeft', windowsVirtualKeyCode=17)
    cdp('Input.dispatchKeyEvent', type='keyDown', key='End', code='End', windowsVirtualKeyCode=35)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='End', code='End', windowsVirtualKeyCode=35)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Control', code='ControlLeft', windowsVirtualKeyCode=17)

def key_escape():
    cdp('Input.dispatchKeyEvent', type='keyDown', key='Escape', code='Escape', windowsVirtualKeyCode=27)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Escape', code='Escape')
    time.sleep(0.15)

def key_arrow_down(n=1):
    for _ in range(n):
        cdp('Input.dispatchKeyEvent', type='keyDown', key='ArrowDown', code='ArrowDown', windowsVirtualKeyCode=40)
        cdp('Input.dispatchKeyEvent', type='keyUp', key='ArrowDown', code='ArrowDown')
        time.sleep(0.08)
    time.sleep(0.05)

def refocus_end_text():
    """画像ブロック選択状態からテキストフローの文末へ戻す（キーボードのみ・実証済み）"""
    key_escape()
    key_arrow_down(2)
    key_ctrl_end()
    time.sleep(0.15)

def fresh_reload():
    """CDPだけでページをリロードして健全状態に戻す（beforeunload確認ダイアログもCDPで受諾）。"""
    try:
        js("window.addEventListener('beforeunload', e => { e.stopImmediatePropagation(); }, true)")
        time.sleep(0.3)
        js("location.reload()")
    except Exception:
        pass
    time.sleep(2.5)
    try:
        cdp('Page.handleJavaScriptDialog', accept=True)
    except Exception:
        pass
    time.sleep(5)
    for _ in range(8):
        try:
            if pm_stats():
                return 'OK'
        except Exception:
            pass
        time.sleep(2)
    return 'TIMEOUT'

def click_end_text():
    """最終テキスト要素を画面内にスクロールして実マウスクリック→文末へ。
    画像挿入後に最終テキストが画面外に出てクリック/貼り付けが無効になる問題の対策。
    クリック後はフォーカスがPMに入ったことを検証し、外れていれば中央クリックで再試行する
    （要素上端のクリックでキャレットが立たない→以降の全貼り付けが死ぬ実例の対策）。"""
    def _pos_js(mode):
        return js("""(() => {
      const pm = document.querySelector('.ProseMirror');
      if (!pm) return 'NO_PM';
      const walker = document.createTreeWalker(pm, NodeFilter.SHOW_TEXT);
      let last = null, n;
      while ((n = walker.nextNode())) { if (n.textContent.trim()) last = n; }
      if (!last) return 'NO_TEXT';
      last.parentElement.scrollIntoView({block: 'center', behavior: 'instant'});
      let rc = null;
      try { const rng = document.createRange(); rng.selectNodeContents(last); rc = rng.getBoundingClientRect(); } catch (e) {}
      if (!rc || rc.width === 0 || rc.height === 0) rc = last.parentElement.getBoundingClientRect();
      if (!rc || rc.width === 0) return 'NO_RC';
      let x, y;
      x = rc.right - 4; y = rc.top + rc.height/2;
      x = Math.max(10, Math.round(x)); y = Math.round(y);
      if (y < 5 || y > 900) return 'OFFSCREEN:' + x + ',' + y;
      return 'POS=' + x + ',' + y;
    })()""")
    def _do_click(x, y):
        cdp('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y, button='left', clickCount=1)
        cdp('Input.dispatchMouseEvent', type='mouseReleased', x=x, y=y, button='left', clickCount=1)
        time.sleep(0.35)
    def _focus_ok():
        v = js("(() => { const pm = document.querySelector('.ProseMirror'); const ae = document.activeElement; return (ae && pm && (ae === pm || pm.contains(ae))) ? 'IN_PM' : 'OUT'; })()")
        return v == 'IN_PM'
    r = _pos_js('end')
    if not (isinstance(r, str) and r.startswith('POS=')):
        return r
    x_s, y_s = r[4:].split(',')
    x, y = int(x_s), int(y_s)
    _do_click(x, y)
    if not _focus_ok():
        r2 = _pos_js('center')
        if isinstance(r2, str) and r2.startswith('POS='):
            x2_s, y2_s = r2[4:].split(',')
            _do_click(int(x2_s), int(y2_s))
            if not _focus_ok():
                return 'FOCUS_FAIL@%s,%s' % (x2_s, y2_s)
    key_ctrl_end()
    time.sleep(0.15)
    return 'OK@%d,%d' % (x, y)

def key_enter():
    cdp('Input.dispatchKeyEvent', type='keyDown', key='Enter', code='Enter', windowsVirtualKeyCode=13, text='\r')
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Enter', code='Enter', windowsVirtualKeyCode=13)

def key_delete():
    cdp('Input.dispatchKeyEvent', type='keyDown', key='Delete', code='Delete', windowsVirtualKeyCode=46)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Delete', code='Delete', windowsVirtualKeyCode=46)

def pm_stats():
    """Editor stats computed in-page, numbers only (avoids huge CDP payloads)."""
    return js('''(() => {
      const pm = document.querySelector('div[contenteditable="true"][role="textbox"]');
      if (!pm) return null;
      const imgs = Array.from(pm.querySelectorAll('img'));
      return {
        chars: pm.innerText.length,
        imgs: imgs.length,
        assetsImgs: imgs.filter(i => /assets\\.st-note\\.com/.test(i.src)).length,
        lastIsAsset: imgs.length ? /assets\\.st-note\\.com/.test(imgs[imgs.length-1].src) : false,
        paragraphs: pm.querySelectorAll('p, h2, h3, li').length
      };
    })()''')

def paste_img_url(url, name):
    """Synthetic paste event carrying original image File. Returns 'DISPATCHED size=N'."""
    return js(f'''(async () => {{
      const blob = await (await fetch('{url}')).blob();
      const f = new File([blob], '{name}', {{type: blob.type || 'image/jpeg'}});
      const dt = new DataTransfer(); dt.items.add(f);
      const ev = new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true, cancelable: true}});
      const pm = document.querySelector('div[contenteditable="true"][role="textbox"]');
      pm.dispatchEvent(ev);
      return 'DISPATCHED size=' + f.size;
    }})()''')

def wait_img_uploaded(prev_imgs, timeout=25):
    """DOM wait: a new img appears in ProseMirror and finishes upload (assets URL or stable)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = pm_stats()
        if st and st['imgs'] > prev_imgs:
            return st
        time.sleep(0.1)
    return None

# ---------------------------------------------------------------- flow steps
def open_new_draft():
    goto_url('https://editor.note.com/new')
    wait_for_load()
    t0 = time.time()
    while time.time() - t0 < 20:
        u = js('location.href')
        m = re.search(r'editor\.note\.com/notes/([a-z0-9]+)/edit', u or '')
        if m:
            return m.group(1), u
        time.sleep(0.3)
    u = js('location.href')
    m2 = re.search(r'editor\.note\.com/notes/([a-z0-9]+)/edit', u or '')
    if m2:
        return m2.group(1), u
    return None, u

def set_title(title):
    fill_input('textarea[placeholder*="タイトル"]', title)

def click_body():
    js('''(() => {
      const pm = document.querySelector('div[contenteditable="true"][role="textbox"]');
      if (!pm) return 'NO_PM';
      pm.focus();
      const sel = window.getSelection();
      const r = document.createRange();
      r.selectNodeContents(pm);
      r.collapse(false);
      sel.removeAllRanges(); sel.addRange(r);
      return 'FOCUSED';
    })()''')

def save_draft():
    """Click 下書き保存, wait for 保存しました toast. Returns (ok, secs)."""
    t0 = time.time()
    r = js('''(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const b = btns.find(x => x.innerText && x.innerText.includes('下書き保存'));
      if (!b) return 'NO_BTN';
      b.click();
      return 'CLICKED';
    })()''')
    if r != 'CLICKED':
        return False, 0
    # poll for toast fast (it lives ~1s)
    while time.time() - t0 < 10:
        txt = js('document.body.innerText')
        if txt and '保存しました' in txt:
            return True, time.time() - t0
        time.sleep(0.05)
    return False, time.time() - t0

def fill_tags(tags):
    """On /publish page: fill hashtag input one word at a time + Enter."""
    for t in tags:
        sel = 'input[placeholder*="ハッシュタグ"]'
        if not js(f"!!document.querySelector('{sel}')"):
            sel = 'input[role="combobox"]'
        fill_input(sel, t)
        key_enter()
        time.sleep(0.2)

def find_text(text):
    """Boolean substring search inside ProseMirror text (no huge payloads)."""
    t = json.dumps(text)
    return js(f'''(() => {{
      const pm = document.querySelector('div[contenteditable="true"][role="textbox"]');
      return pm ? pm.innerText.includes({t}) : false;
    }})()''')

# ---------------------------------------------------------------- verification
def expected_from_seq():
    """Sum of visible text length of html frags + title, and img count."""
    import html as htmlmod
    seq = load_seq()
    st = load_state()
    title = st.get('title', '')
    n_chars = len(title)
    first_txt = last_txt = None
    for step in seq:
        if step['kind'] == 'html':
            raw = open(step['path'], encoding='utf-8').read()
            txt = re.sub(r'<br\s*/?>', '\n', raw)
            txt = re.sub(r'<[^>]+>', '', txt)
            txt = htmlmod.unescape(txt)
            n_chars += len(txt.strip())
            if first_txt is None:
                first_txt = txt.strip()[:30]
            last_txt = txt.strip()[-30:]
        else:
            n_chars += 1  # placeholder where image sits (note counts ~1)
    return {'chars': n_chars, 'imgs': sum(1 for s in seq if s['kind'] == 'img'),
            'first_txt': first_txt, 'last_txt': last_txt}

def verify_editor():
    """After reload: char count, img count, distinct assets, first/last text present."""
    exp = expected_from_seq()
    st = pm_stats()
    first = find_text(exp['first_txt'] or '')
    last = find_text(exp['last_txt'] or '')
    # distinct assets via in-page Set (numbers only)
    distinct = js('''(() => {
      const pm = document.querySelector('div[contenteditable="true"][role="textbox"]');
      if (!pm) return 0;
      const s = new Set();
      pm.querySelectorAll('img').forEach(i => {
        const m = i.src.match(/assets\\.st-note\\.com\\/img\\/([0-9]+)-/);
        if (m) s.add(m[1]);
      });
      return s.size;
    })()''')
    return {'expected': exp, 'actual': st, 'first_ok': first, 'last_ok': last, 'distinct_assets': distinct}

def report_state(st):
    save_state(st)
    return st
