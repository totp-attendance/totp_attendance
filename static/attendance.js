// 브라우저 내 TOTP (pyotp 호환: SHA-1, 6자리, 30초) + 기기 신원 저장.
// 학생 폰 브라우저가 인증앱 역할 — secret 은 이 기기 localStorage 에만 저장.

const IDENTITY_KEY = "attendance_identity";

function saveIdentity(sid, name, secret) {
  localStorage.setItem(IDENTITY_KEY, JSON.stringify({ sid, name, secret }));
}
function loadIdentity() {
  try { return JSON.parse(localStorage.getItem(IDENTITY_KEY)); }
  catch (e) { return null; }
}
function clearIdentity() { localStorage.removeItem(IDENTITY_KEY); }

// base32 (RFC4648) 디코드 → Uint8Array
function base32decode(s) {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  s = (s || "").replace(/=+$/, "").toUpperCase().replace(/\s/g, "");
  let bits = 0, val = 0; const out = [];
  for (const c of s) {
    const i = A.indexOf(c);
    if (i < 0) continue;
    val = (val << 5) | i; bits += 5;
    if (bits >= 8) { out.push((val >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  return new Uint8Array(out);
}

// --- 순수 JS SHA-1 / HMAC-SHA1 폴백 ----------------------------------------
// crypto.subtle 은 secure context(HTTPS/localhost)에서만 동작. 학생폰이
// http://<교사PC-IP>:5000 (평문 LAN) 으로 접속하면 undefined → TOTP 못 만듦.
// 그래서 WebCrypto 없을 때 쓸 순수 JS 구현을 둠 (HTTP LAN 에서도 동작).
function _sha1(bytes) {
  const ml = bytes.length * 8;
  const total = ((bytes.length + 1 + 8 + 63) & ~63);
  const msg = new Uint8Array(total);
  msg.set(bytes);
  msg[bytes.length] = 0x80;
  const dv = new DataView(msg.buffer);
  dv.setUint32(total - 4, ml >>> 0);
  dv.setUint32(total - 8, Math.floor(ml / 2 ** 32));
  let h0 = 0x67452301, h1 = 0xEFCDAB89, h2 = 0x98BADCFE,
      h3 = 0x10325476, h4 = 0xC3D2E1F0;
  const w = new Int32Array(80);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getInt32(off + i * 4);
    for (let i = 16; i < 80; i++) {
      const v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = (v << 1) | (v >>> 31);
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      let f, k;
      if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
      else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
      else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
      else { f = b ^ c ^ d; k = 0xCA62C1D6; }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) | 0;
      e = d; d = c; c = (b << 30) | (b >>> 2); b = a; a = t;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0;
    h3 = (h3 + d) | 0; h4 = (h4 + e) | 0;
  }
  const out = new Uint8Array(20);
  const odv = new DataView(out.buffer);
  odv.setInt32(0, h0); odv.setInt32(4, h1); odv.setInt32(8, h2);
  odv.setInt32(12, h3); odv.setInt32(16, h4);
  return out;
}

function _hmacSha1(key, msg) {
  if (key.length > 64) key = _sha1(key);
  const k = new Uint8Array(64); k.set(key);
  const ip = new Uint8Array(64), op = new Uint8Array(64);
  for (let i = 0; i < 64; i++) { ip[i] = k[i] ^ 0x36; op[i] = k[i] ^ 0x5c; }
  const inner = new Uint8Array(64 + msg.length);
  inner.set(ip); inner.set(msg, 64);
  const ih = _sha1(inner);
  const outer = new Uint8Array(84);
  outer.set(op); outer.set(ih, 64);
  return _sha1(outer);
}

// TOTP 코드 계산 (현재 시각 기준). WebCrypto 있으면 사용, 없으면 순수 JS.
async function totp(secretB32, interval = 30, digits = 6) {
  const keyData = base32decode(secretB32);
  const counter = Math.floor(Date.now() / 1000 / interval);
  const buf = new Uint8Array(8);
  let c = counter;
  for (let i = 7; i >= 0; i--) { buf[i] = c & 0xff; c = Math.floor(c / 256); }
  let sig;
  if (typeof crypto !== "undefined" && crypto.subtle) {
    const key = await crypto.subtle.importKey(
      "raw", keyData, { name: "HMAC", hash: "SHA-1" }, false, ["sign"]
    );
    sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, buf.buffer));
  } else {
    sig = _hmacSha1(keyData, buf);  // 평문 HTTP LAN 폴백
  }
  const off = sig[sig.length - 1] & 0x0f;
  const bin = ((sig[off] & 0x7f) << 24) | (sig[off + 1] << 16)
            | (sig[off + 2] << 8) | sig[off + 3];
  return String(bin % 10 ** digits).padStart(digits, "0");
}
