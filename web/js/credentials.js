export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_LENGTH = 256;

const ALGORITHM = 'PBKDF2-SHA256';
const ITERATIONS = 600_000;
const SALT_BYTES = 16;
const DIGEST_BYTES = 32;
const encoder = new TextEncoder();

const hex = (bytes) => Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
const fromHex = (value) => new Uint8Array(value.match(/../g).map((pair) => Number.parseInt(pair, 16)));

export function validateNewPassword(password, previous) {
  if (typeof password !== 'string' || password.length > MAX_PASSWORD_LENGTH * 2) {
    throw new Error(`Use a password with ${MIN_PASSWORD_LENGTH}–${MAX_PASSWORD_LENGTH} characters.`);
  }
  const length = Array.from(password).length;
  if (length < MIN_PASSWORD_LENGTH || length > MAX_PASSWORD_LENGTH || !password.trim()) {
    throw new Error(`Use ${MIN_PASSWORD_LENGTH}–${MAX_PASSWORD_LENGTH} characters with at least one non-space character.`);
  }
  if (password === previous) throw new Error('Choose a password different from the current password.');
}

async function derive(password, salt) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error('Open the workspace in the desktop app to manage passwords.');
  const key = await subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: ITERATIONS }, key, DIGEST_BYTES * 8);
  return new Uint8Array(bits);
}

export async function createCredential(password) {
  const salt = globalThis.crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const digest = await derive(password, salt);
  return { algorithm: ALGORITHM, iterations: ITERATIONS, salt: hex(salt), digest: hex(digest) };
}

export async function matchesCredential(user, password) {
  if (!user?.active || typeof password !== 'string' || !password) return false;
  const credential = user.credential;
  if (!credential) return Boolean(user.password) && user.password === password;
  if (password.length > MAX_PASSWORD_LENGTH * 2 || credential.algorithm !== ALGORITHM || credential.iterations !== ITERATIONS
    || !/^[a-f0-9]{32}$/.test(credential.salt) || !/^[a-f0-9]{64}$/.test(credential.digest)) return false;
  const expected = fromHex(credential.digest);
  const actual = await derive(password, fromHex(credential.salt));
  let differences = 0;
  for (const [index, byte] of actual.entries()) differences |= byte ^ expected[index];
  return differences === 0;
}
