# Accounts: who may sign in, as what, and who can see whose data

Added 15 Oct 2026, with the database emptied for a fresh start.

```
team.visioncoders.in         the VisionCoders agency site
saarthi2.0.visioncoders.in   Saarthi: its page at /, sign-in, dashboard at /app
indux5.0.visioncoders.in     kept: n8n and Twilio call back to it
```

(An earlier pass had these two the wrong way round - the Saarthi page on `team` and the
bare dashboard on `saarthi2.0`. The agency site is the front door; Saarthi and its
sign-in belong together on the product's own name.)

## How secrets are stored

Nothing reversible is kept.

| secret | stored as |
|---|---|
| password | scrypt, n=2^14 r=8 p=1, 16-byte per-user salt |
| session token | SHA-256 of the token; the raw token only ever lives in the cookie |
| one-time code | SHA-256 of the code |

"Encrypt the saved credentials" is the right instinct and **hashing is the stronger
form of it**. Encryption implies a key that turns the stored value back into the
secret, and for a password nobody - including this server - ever needs to do that.
A stolen database yields no passwords, no usable sessions and no live codes.

The salt is stored beside the hash, which is correct: a salt is not a secret, it is
what stops one rainbow table covering every account. Two people choosing the same
password get different hashes.

`verify_password` fails closed on a damaged or unknown hash format rather than
raising, so a corrupt row is a failed sign-in and not a 500.

## Signing up

1. `POST /api/auth/register` - email, 10-digit mobile, password (8+ characters, not
   all digits, not a known-common one). The account is created **pending**.
2. A six-digit code goes out through n8n. The response masks the address it went to
   (`th**********@gmail.com`) and never contains the code.
3. `POST /api/auth/verify` turns pending into active and signs them in.

A pending account cannot sign in; trying sends a fresh code and says so. There is no
"verify later", because then the code would not be doing anything.

**Codes** live 10 minutes, allow 5 attempts, and one per purpose at a time - issuing a
new one consumes the old. Asking again within 60 seconds is refused with the seconds
remaining, so a resend button cannot flood someone's phone. A sign-up code is not
accepted as a reset code: they are scoped to their purpose.

## Signing in

By email **or** phone, however the number is typed. Five failures lock the account for
15 minutes. A wrong password and an unknown address return the **same** message - which
half was wrong is not the caller's business.

The session is an **httpOnly** cookie, so javascript cannot read it and a cross-site
script cannot steal a login. `Secure` is set when the public URL is https, and left off
locally or nothing would stay signed in over http. An `Authorization: Bearer` header is
accepted too and **outranks** the cookie: sending one is a deliberate act, while the
cookie rides along on every request.

### The two roles

`user` and `admin`, and nothing else. The sign-in page offers both as tiles, but that is
a **routing hint only** - the server decides what an account is and says so when the two
disagree. A browser asserting its own role would be no security at all.

## Who can see what

Enforced in `core/security.py`, in one place rather than as a habit spread across
twenty handlers:

* `owned_phone(user, requested)` - a farmer always gets their own number, whatever the
  request asked for. An admin gets the one they named.
* `assert_may_read(user, phone)` - raises 403 unless it is their own data, or they are
  an admin.

Before accounts, `GET /api/profile?phone=<anyone>` returned that farmer's whole record
to any caller. `tests/test_isolation.py` now checks this from the attacker's side: every
test names a number that is not the caller's and asserts what comes back.

Also closed by the same change:

* `/api/farmers` listed every farmer on the box - now admin only.
* `/api/calls` showed every call - now scoped, with the whole log for an admin.
* `/api/calls/{id}` handed over any transcript - now an ownership check.
* **The call-back button dialled whatever number was typed.** It now always rings the
  number the account signed up with, so it cannot be used to make Saarthi phone a
  stranger. `CallRequest.phone` became optional because the dashboard no longer sends
  one.
* `/api/chat` took the farmer from the request body, so naming someone else's number
  read their memory. A signed-in account now wins over anything in the body.

## Forgot password

`POST /api/auth/forgot` answers **identically** whether or not the account exists, so
the endpoint cannot be used to find out who has one. On success a code goes to the
address on the account; `POST /api/auth/reset` checks it and sets the new password.

Changing a password **closes every other session**, because any session opened with the
old one is no longer trustworthy.

## The administrator

Created at start-up from `SAARTHI_ADMIN_EMAIL`, `SAARTHI_ADMIN_PHONE` and
`SAARTHI_ADMIN_PASSWORD`, and only if no admin exists yet. Leave the password blank and
**nobody is created** - an account with a guessable default password is worse than no
account.

The live admin was bootstrapped with a password generated on the server and never
recorded anywhere: the owner sets their own through "forgot password", which sends a
code to the same address. No password was ever chosen by, or transmitted through, a
chat session.

The admin dashboard (`/api/admin/...`, and the Admin page in the sidebar) lists every
account with its role, status, field count, acreage, remembered facts and call count,
opens any one in full, and can change a role or lock an account. An admin cannot strip
their own admin rights or lock themselves out - that would leave the page unreachable.
Locking an account ends its sessions immediately.

## One-time code delivery

Cloned from the estate's existing `AFI_OTP_Delivery` chain into
`Saarthi_OTP_Delivery` (`SaarthiOTP000001`, webhook `/webhook/saarthi-otp`), so the two
apps cannot rotate each other's secret. Saarthi holds neither the SMTP password nor the
Twilio token; it posts the code behind a shared secret and n8n sends it.

Two things were changed rather than copied: the webhook path and secret, and the Twilio
`from` number - **the original has the literal string `__TWILIO_FROM__` in that field,
so its SMS branch cannot ever have worked.** Rebuild with
`deploy/build_otp_workflow.py`, which refuses to emit a chain with an empty secret.

Verified live: a wrong secret is refused with 401, and both the email and SMS branches
return `{"ok": true}`.

## The fresh start

`deploy/wipe_database.py` empties every table and restarts the autoincrement counters,
keeping the schema. Deliberately not "delete the file": the database carries the schema
too, and recreating it by hand is how a column quietly goes missing.

The live database was copied to `/opt/stacks/_backups/` before it was emptied. What
remains is one administrator and no farm data.


---

## The security question (added 15 Oct 2026)

Sign-up, sign-in and forgot-password each require a short challenge first. Not an
anti-bot measure in the Turnstile sense - a determined script will read the question.
What it stops is the cheap case: a loop hammering `/api/auth/login` with a password
list without ever fetching a challenge or keeping state.

* **The answer never reaches the browser.** `GET /api/auth/captcha` returns a question
  and an id; the answer is hashed server-side. A captcha whose answer travels with the
  question is decoration.
* **Single use, whether right or wrong**, or one challenge could be brute-forced twenty
  times over. A failed attempt therefore gets a fresh question automatically - the
  sign-in path needed its own fix for this, because its catch block differs.
* Arithmetic in words rather than a distorted image: it has to work for a farmer on a
  cheap phone, and an unreadable image is a worse barrier to the legitimate user than to
  the script.

The markup already existed on the sign-in and sign-up forms and was being hidden; the
forgot-password form had none and now gets one built in at assembly time.

## Deleting an account

`DELETE /api/admin/users/{id}` removes the account **and everything it owns**: profile,
mapped fields, remembered facts, call log and transcripts, advisory history, sensor
readings, sessions and codes.

This matters because the farm tables are keyed on the **phone number**, not the user id.
Deleting the `users` row alone would leave all of it in place - and hand it to the next
person who registers that number. `purge=false` keeps the farm records if that is ever
wanted.

The admin panel asks twice and spells out what goes. An admin cannot delete the account
they are signed in as.

## Signing up over a phone call

`/api/enrol/*` lets the call itself open an account, for a farmer with no browser.
n8n asks one question per turn and sends the whole `collected` dictionary back each
time, so it holds no state and a dropped call writes nothing.

The script collects name, village, district, acreage and crop, then offers to register
a field (name, size, soil). Two deliberate differences from the web sign-up:

* **The code goes by SMS, never email.** This caller has given a phone number and may
  have no email at all.
* **No password and no captcha.** Answering the call on that number is the proof; a
  password typed on a numeric keypad helps nobody. A long random one is set so the row
  is never password-less, and the farmer uses "forgot password" if they ever sign in on
  the website.

Nothing is written to the profile until the caller reads the code back. An acreage that
cannot be heard as a number is asked again rather than guessed at.

The 15-agent chain is **not** run from this flow - collecting the details and reasoning
over them are separate jobs, and this one has to fit inside a phone turn.
