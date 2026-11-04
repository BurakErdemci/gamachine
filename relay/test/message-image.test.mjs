import { setLang } from '../public/i18n.js';
setLang('tr');

// The phone page shows message text as plain text: a markdown image becomes a chip.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { messageText } from '../public/net.js';

const FILE = '![ilk ekran](gamachine-file:0a1b2c3d-1111-2222-3333-444455556666)';

test('an image the agent showed is a chip with its caption', () => {
  assert.equal(messageText({ text: FILE }), '[🖼 ilk ekran]');
});

test('an image without a caption is a chip called görüntü', () => {
  assert.equal(messageText({ text: '![](gamachine-file:0a1b2c3d-1111-2222-3333-444455556666)' }), '[🖼 görüntü]');
  assert.equal(messageText({ text: '![   ](gamachine-file:0a1b2c3d-1111-2222-3333-444455556666)' }), '[🖼 görüntü]');
});

test('a path written as an image is a chip too, and the target never shows', () => {
  const out = messageText({ text: 'bak: ![x](C:\Users\a\b c\shot.png) tamam ![](<D:\y.png>)' });
  assert.equal(out, 'bak: [🖼 x] tamam [🖼 görüntü]');
  assert.ok(!out.includes('C:'));
});

test('text around the image and plain messages are unchanged', () => {
  assert.equal(messageText({ text: 'önce\n' + FILE + '\nsonra' }), 'önce\n[🖼 ilk ekran]\nsonra');
  assert.equal(messageText({ text: 'a [link](http://x.y) ve ! işareti (parantez)' }), 'a [link](http://x.y) ve ! işareti (parantez)');
  assert.equal(messageText({ text: '' }), '');
});

test('a shortened message keeps its chip, and a message without text still reads as before', () => {
  assert.ok(messageText({ text: FILE, truncated: true }).includes('[🖼 ilk ekran]'));
  assert.equal(messageText({ truncated: true }), messageText({ truncated: true, text: undefined }));
  assert.equal(messageText({}), '');
});
