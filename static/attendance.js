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

// TOTP 코드 계산 (현재 시각 기준)
async function totp(secretB32, interval = 30, digits = 6) {
  const keyData = base32decode(secretB32);
  const key = await crypto.subtle.importKey(
    "raw", keyData, { name: "HMAC", hash: "SHA-1" }, false, ["sign"]
  );
  const counter = Math.floor(Date.now() / 1000 / interval);
  const buf = new ArrayBuffer(8);
  const dv = new DataView(buf);
  dv.setUint32(0, Math.floor(counter / 2 ** 32));
  dv.setUint32(4, counter >>> 0);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, buf));
  const off = sig[sig.length - 1] & 0x0f;
  const bin = ((sig[off] & 0x7f) << 24) | (sig[off + 1] << 16)
            | (sig[off + 2] << 8) | sig[off + 3];
  return String(bin % 10 ** digits).padStart(digits, "0");
}
