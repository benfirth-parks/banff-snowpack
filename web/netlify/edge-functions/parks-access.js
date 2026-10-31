/**
 * Parks access check. Every Parks app carries an identical copy of this file in
 * its netlify/edge-functions folder; the master copy is in the app-portal repo.
 *
 * 1. Canada only. Visitors Netlify places outside Canada get a bare "Not
 *    found" (no reason given), unless they hold a Parks pass. Travellers sign
 *    in at the portal's /pass page, which stays open everywhere. Visitors whose
 *    country is unknown are let in.
 *    Setting CANADA_ONLY=off on a site turns this part off there.
 * 2. Parks password, switched centrally on the portal. Each app asks the portal
 *    (/api/parks-config, cached for five minutes) for its mode and the portal's
 *    public key. With mode "on", visitors without a pass are sent to the
 *    portal's sign-in page, which sends them back with a pass signed by the
 *    portal for this site only; it's kept in a 30-day cookie. "report" only logs
 *    who would have been sent to sign in. If the portal can't be reached, the
 *    password part stays open, so a portal outage never locks out an app.
 *
 * Paths that other apps, servers and services call (APIs, data files, Netlify
 * functions, the portal tracker) are left out in `config` below. No secrets
 * live here: apps only hold the portal's public key.
 */

export const PORTAL = "https://banff-app-portal.netlify.app"
export const COOKIE = "parks_pass"
export const TICKET_PATH = "/__parks-pass"
export const PASS_SECONDS = 30 * 24 * 60 * 60
const TICKET_SECONDS = 120
const CONFIG_SECONDS = 300

const env = (k) => (globalThis.Netlify && globalThis.Netlify.env && globalThis.Netlify.env.get(k)) || ""
const now = () => Math.floor(Date.now() / 1000)
const enc = new TextEncoder()

export function b64url(bytes) {
  let s = ""
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}
export function fromB64url(s) {
  return Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0))
}

/**
 * A pass is "<body>.<signature>": body is {aud: the one site it's for, r: path
 * to return to, exp: when it can no longer be handed over (two minutes),
 * pexp: when it stops working as a pass}. Only the portal can sign one.
 */
export async function signPass(privateKey, aud, r = "/") {
  const body = b64url(enc.encode(JSON.stringify({ aud, r, exp: now() + TICKET_SECONDS, pexp: now() + PASS_SECONDS })))
  const sig = await crypto.subtle.sign("Ed25519", privateKey, enc.encode(body))
  return `${body}.${b64url(new Uint8Array(sig))}`
}
/** Returns the pass's contents if the portal signed it for `origin`, else null. */
export async function readPass(publicKey, token, origin) {
  const [body, sig] = String(token || "").split(".")
  if (!publicKey || !body || !sig) return null
  try {
    if (!(await crypto.subtle.verify("Ed25519", publicKey, fromB64url(sig), enc.encode(body)))) return null
    const p = JSON.parse(new TextDecoder().decode(fromB64url(body)))
    return p.aud === origin && p.pexp > now() ? p : null
  } catch {
    return null
  }
}
export function passCookie(token) {
  return `${COOKIE}=${token}; Path=/; Max-Age=${PASS_SECONDS}; HttpOnly; Secure; SameSite=Lax`
}
export function readCookie(req, name = COOKIE) {
  const m = (req.headers.get("cookie") || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`))
  return m ? m[1] : ""
}
/** Only same-site paths, never "//host" or a full URL. */
export function safePath(p) {
  return typeof p === "string" && p.startsWith("/") && !p.startsWith("//") && !p.startsWith("/\\") ? p : "/"
}
export const importPublicKey = (raw) => crypto.subtle.importKey("raw", fromB64url(raw), { name: "Ed25519" }, false, ["verify"])

// ---------- the portal's settings for this site, cached per edge instance ----------
let cached = null // { at, mode, key }
export function _resetCache() {
  cached = null
}
async function siteConfig(origin) {
  if (cached && now() - cached.at < CONFIG_SECONDS) return cached
  try {
    const res = await fetch(`${PORTAL}/api/parks-config?site=${encodeURIComponent(origin)}`, { signal: AbortSignal.timeout(3000) })
    if (!res.ok) throw new Error(`status ${res.status}`)
    const c = await res.json()
    cached = { at: now(), mode: String(c.mode || "off"), key: c.key ? await importPublicKey(c.key) : null }
  } catch (e) {
    console.log(`parks-access: couldn't read the portal settings (${e && e.message}); password check open`)
    // Keep the last good settings if there are any; try again in a minute.
    cached = cached ? { ...cached, at: now() - CONFIG_SECONDS + 60 } : { at: now() - CONFIG_SECONDS + 60, mode: "off", key: null }
  }
  return cached
}

const isPage = (req) =>
  req.method === "GET" && (req.headers.get("sec-fetch-mode") === "navigate" || (req.headers.get("accept") || "").includes("text/html"))
const noStore = { "cache-control": "no-store", "referrer-policy": "no-referrer" }
const signInUrl = (url) => `${PORTAL}/pass?to=${encodeURIComponent(url.href)}`

export default async function parksAccess(req, context) {
  const url = new URL(req.url)
  const site = await siteConfig(url.origin)

  // Coming back from the portal sign-in with a pass for this site.
  if (url.pathname === TICKET_PATH) {
    const token = url.searchParams.get("t") || ""
    const pass = await readPass(site.key, token, url.origin)
    if (!pass || !(pass.exp > now())) {
      return new Response("This sign-in link has expired. Open the app again to get a new one.", { status: 400, headers: noStore })
    }
    return new Response(null, { status: 302, headers: { ...noStore, location: safePath(pass.r), "set-cookie": passCookie(token) } })
  }

  const hasPass = !!(await readPass(site.key, readCookie(req), url.origin))

  // Outside Canada the site simply looks missing: a bare "Not found", no reason given.
  const country = context && context.geo && context.geo.country && context.geo.country.code
  if (country && country !== "CA" && !hasPass && env("CANADA_ONLY").toLowerCase() !== "off") {
    return new Response("Not found", { status: 404, headers: { ...noStore, "content-type": "text/plain; charset=utf-8" } })
  }

  if (hasPass || !site.key || (site.mode !== "on" && site.mode !== "report")) return
  if (site.mode === "report") {
    console.log(`parks-access report: would send to sign-in ${req.method} ${url.pathname}`)
    return
  }
  if (isPage(req)) return new Response(null, { status: 302, headers: { ...noStore, location: signInUrl(url) } })
  return new Response("Sign in with the Parks password first", { status: 401, headers: noStore })
}

export const config = {
  path: "/*",
  // /pass is the portal sign-in page, open so travellers can reach it.
  excludedPath: ["/api/*", "/.netlify/*", "/data/*", "/t.js", "/pass"],
}
