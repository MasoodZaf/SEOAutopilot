import {createCipheriv, createDecipheriv, randomBytes, timingSafeEqual} from "node:crypto";

/**
 * AES-256-GCM around a JSON value, for cookies the browser must not read.
 *
 * Kept free of environment access and of `server-only` so it can be exercised
 * directly: this is the piece where a mistake means a session cookie that can be
 * read or edited by whoever holds it, and that deserves tests rather than
 * confidence. The key is a parameter for the same reason.
 */

const IV_BYTES = 12;

/**
 * @param {Buffer} key 32 bytes.
 * @param {unknown} value
 * @returns {string}
 */
export function seal(key, value) {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const body = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return [iv, cipher.getAuthTag(), body].map((part) => part.toString("base64url")).join(".");
}

/**
 * @param {Buffer} key 32 bytes.
 * @param {string | undefined} envelope
 * @returns {unknown}
 */
export function open(key, envelope) {
  if (!envelope) return null;
  const parts = envelope.split(".");
  if (parts.length !== 3) return null;
  try {
    const [iv, tag, body] = parts.map((part) => Buffer.from(part, "base64url"));
    if (iv.length !== IV_BYTES) return null;
    const decipher = createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAuthTag(tag);
    const plain = Buffer.concat([decipher.update(body), decipher.final()]);
    return JSON.parse(plain.toString("utf8"));
  } catch {
    // A cookie that fails its authentication tag is not a session with a
    // problem, it is not a session. Treat it exactly like none at all.
    return null;
  }
}

/**
 * Constant-time comparison, for the OAuth state value.
 * @param {string} a
 * @param {string} b
 */
export function sameState(a, b) {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}
