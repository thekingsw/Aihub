// Kiểm thử bộ dựng Markdown của hub.html (tuỳ chọn): cần Node và `npm i jsdom`.
//   node test_hub_md.js
const {JSDOM} = require('jsdom');
const path = require('path');
let failed = 0;
const check = (name, ok, extra) => { if (!ok) failed++; console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : ' :: ' + extra)); };
(async () => {
  const dom = await JSDOM.fromFile(path.join(__dirname, 'hub.html'), {
    runScripts: 'dangerously', url: 'http://localhost/hub', pretendToBeVisual: true,
    beforeParse(w) { w.fetch = () => Promise.reject(new Error('no net')); },
  });
  const w = dom.window;
  await new Promise((r) => setTimeout(r, 100));
  const md = (src) => w.renderMarkdown(src);
  const q = (el, sel) => [...el.querySelectorAll(sel)];
  let e;

  e = md('**đậm** và *nghiêng* và ~~gạch~~ và `code`');
  check('inline styles', q(e, 'strong').length === 1 && q(e, 'em').length === 1 && q(e, 'del').length === 1 && q(e, 'code')[0].textContent === 'code', e.innerHTML);
  e = md('dùng my_var_name và file_name.py, còn _nghiêng_ thì đổi');
  check('snake_case stays literal, _x_ italic', q(e, 'em').length === 1 && q(e, 'em')[0].textContent === 'nghiêng' && e.textContent.includes('my_var_name'), e.innerHTML);
  e = md('# Một\n### Ba');
  check('headings', q(e, 'h1').length === 1 && q(e, 'h3')[0].textContent === 'Ba');
  e = md('- a\n- b\n  - c\n  - d\n- e\n\n1. x\n2. y');
  check('nested lists', q(e, 'ul').length === 2 && q(e, 'ul ul li').length === 2 && q(e, 'ol li').length === 2 && q(e, 'ul > li').length === 5, e.innerHTML);
  e = md('- [x] xong\n- [ ] chưa');
  check('task list', q(e, 'li')[0].textContent.startsWith('☑') && q(e, 'li')[1].textContent.startsWith('☐'));
  e = md("```js\nconst a = '<b>x</b>';\n```\nsau");
  check('fenced code is literal', q(e, 'pre code')[0].textContent === "const a = '<b>x</b>';" && q(e, 'b').length === 0 && q(e, 'p').length === 1, e.innerHTML);
  e = md('```\nchưa đóng\nvẫn là code');
  check('unterminated fence', q(e, 'pre code')[0].textContent === 'chưa đóng\nvẫn là code');
  e = md('| Cột | Giá trị |\n|:---|---:|\n| A | 2 |\n| B \\| x | 3 |');
  check('table with alignment and escaped pipe', q(e, 'th').length === 2 && q(e, 'tbody tr').length === 2 && q(e, 'td')[2].textContent === 'B | x' && q(e, 'td')[1].getAttribute('style') === 'text-align:right', e.innerHTML);
  e = md('> trích dẫn\n> > lồng nhau\n\nhết');
  check('blockquote nested', q(e, 'blockquote blockquote').length === 1 && q(e, 'p').length >= 2, e.innerHTML);
  e = md('a\nb\n\nc');
  check('line breaks and paragraphs', q(e, 'p').length === 2 && q(e, 'p br').length === 1);
  e = md('trên\n\n---\n\ndưới');
  check('hr', q(e, 'hr').length === 1);

  // ---- an toàn
  e = md('<img src=x onerror=alert(1)> <script>alert(2)</script> <b>x</b>');
  check('raw html stays text', q(e, 'img, script, b').length === 0 && e.textContent.includes('<img src=x onerror=alert(1)>'), e.innerHTML);
  e = md('[bấm](javascript:alert(1)) và [ok](https://a.test/x) và [d](data:text/html,x) và [v](vbscript:x)');
  const hrefs = q(e, 'a').map((a) => a.getAttribute('href'));
  check('only http(s) links', JSON.stringify(hrefs) === '["https://a.test/x"]' && e.textContent.includes('[bấm](javascript:alert(1))'), JSON.stringify(hrefs));
  check('links open safely', q(e, 'a')[0].rel === 'noopener noreferrer' && q(e, 'a')[0].target === '_blank');
  e = md('xem https://example.com/x?a=1. Hết');
  check('autolink excludes trailing dot', q(e, 'a')[0].getAttribute('href') === 'https://example.com/x?a=1' && e.textContent.endsWith('. Hết'));
  e = md('![ảnh](http://evil.test/t.png) và [f](/api/v1/files/' + '0123456789abcdef'.repeat(2) + ')');
  check('remote images become links, never <img>; hub file links allowed', q(e, 'img').length === 0 && q(e, 'a').length === 2 && q(e, 'a')[0].textContent === '🖼 ảnh' && !e.textContent.startsWith('!') && q(e, 'a')[1].getAttribute('href') === '/api/v1/files/' + '0123456789abcdef'.repeat(2), e.innerHTML);
  e = md('[**đậm** https://x.test](https://y.test)');
  check('no nested links', q(e, 'a').length === 1 && q(e, 'a strong').length === 1, e.innerHTML);
  e = md('E:\\MyProj\\.aihub-outbox\\12 và *.py cùng *.js');
  check('windows paths and globs survive', e.textContent === 'E:\\MyProj\\.aihub-outbox\\12 và *.py cùng *.js' && q(e, 'em').length === 0, e.textContent);
  e = md(null); check('null is empty', e.childNodes.length === 0);
  e = md('x'.repeat(70000)); check('huge input falls back to plain pre', q(e, 'pre').length === 1);
  for (const [name, src] of [['stars', '*'.repeat(8000) + 'a'], ['brackets', '['.repeat(5000)], ['mixed', '**a *b '.repeat(2000)], ['underscores', '_a'.repeat(8000)], ['backticks', '`a'.repeat(6000)]]) {
    const t0 = Date.now(); md(src); const dt = Date.now() - t0;
    check('no pathological slowdown: ' + name + ' (' + dt + ' ms)', dt < 1500, dt);
  }
  console.log(failed ? failed + ' FAILED' : 'ALL MARKDOWN CHECKS PASSED');
  process.exit(failed ? 1 : 0);
})();
