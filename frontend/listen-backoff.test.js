/* Tests for the listening backoff. Run with: node frontend/listen-backoff.test.js
   No framework - node is already on the machine and this file has no dependencies.

   The bug these hold shut: on a phone the recogniser ends every second or two,
   and the old code restarted it from both the `end` handler and the `error`
   handler. One silent window, two restarts, a start() throwing, and the
   microphone opening and closing twice a second - "it listens and cancels and
   again listens and cancels, automatically". */
"use strict";

const assert = require("assert");
const L = require("./listen-backoff.js");

let passed = 0;
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

/* --------------------------------------------------------- the delays */
test("the first restart is quick, so a pause for breath is not punished", () => {
  assert.ok(L.nextDelay(0) <= 400, "a farmer thinking for a second is normal");
  assert.ok(L.nextDelay(1) <= 400);
});

test("it backs off while nobody is speaking", () => {
  const waits = [0, 1, 2, 3, 4, 5, 9].map(L.nextDelay);
  for (let i = 1; i < waits.length; i += 1) {
    assert.ok(waits[i] >= waits[i - 1],
      `wait went down from ${waits[i - 1]} to ${waits[i]}`);
  }
  assert.ok(waits[waits.length - 1] >= 1500, "it never settles into a spin");
});

test("the wait is capped rather than growing forever", () => {
  assert.strictEqual(L.nextDelay(50), L.nextDelay(L.RESTART_MS.length - 1));
});

test("a negative count cannot index past the front of the table", () => {
  assert.strictEqual(L.nextDelay(-3), L.RESTART_MS[0]);
});

/* -------------------------------------------- one window, one decision */
test("a silent window asks for exactly one restart", () => {
  const back = new L.Backoff();
  const out = back.ended(false);
  assert.strictEqual(out.stop, false);
  assert.ok(out.wait > 0, "it restarted immediately, which is the spin");
});

test("speech resets the backoff", () => {
  const back = new L.Backoff();
  for (let i = 0; i < 5; i += 1) back.ended(false);
  const slow = L.nextDelay(back.quiet);
  back.heard();
  assert.strictEqual(back.quiet, 0);
  assert.ok(L.nextDelay(back.quiet) < slow,
    "after somebody spoke it should be quick again");
});

test("a window that heard something resets it too", () => {
  const back = new L.Backoff();
  back.ended(false);
  back.ended(false);
  back.ended(true);
  assert.strictEqual(back.quiet, 0);
});

/* ----------------------------------------------------- it does give up */
test("it stops rather than holding the microphone open for ever", () => {
  const back = new L.Backoff();
  let stopped = false;
  for (let i = 0; i < L.ENOUGH_QUIET + 2 && !stopped; i += 1) {
    stopped = back.ended(false).stop;
  }
  assert.ok(stopped, "a phone in a pocket would listen all day");
});

test("giving up takes long enough to be a real silence", () => {
  const back = new L.Backoff();
  let total = 0;
  for (let i = 0; i < L.ENOUGH_QUIET; i += 1) {
    const out = back.ended(false);
    if (out.stop) break;
    total += out.wait;
  }
  // The waits alone, never mind the listening windows between them.
  assert.ok(total >= 10000,
    `gave up after ${total} ms of waiting, which is too quick`);
});

test("a conversation that ended in silence starts fresh", () => {
  const back = new L.Backoff();
  for (let i = 0; i < L.ENOUGH_QUIET; i += 1) back.ended(false);
  back.reset();
  assert.strictEqual(back.ended(false).stop, false,
    "a new conversation opened already half-way to giving up");
});

/* ------------------------------------------------- the whole sequence */
test("a real minute: talk, pause, talk, walk away", () => {
  const back = new L.Backoff();
  const opened = [];
  const window = (spoke) => {
    const out = back.ended(spoke);
    if (out.stop) return false;
    opened.push(out.wait);
    return true;
  };

  assert.ok(window(true));          // said something
  assert.ok(window(false));         // thinking
  assert.ok(window(true));          // said something else
  assert.strictEqual(back.quiet, 0, "the pause in the middle stuck around");

  let alive = true;
  let windows = 0;
  while (alive && windows < 40) { alive = window(false); windows += 1; }
  assert.ok(!alive, "it never stopped after they walked away");
  assert.strictEqual(windows, L.ENOUGH_QUIET,
    "it gave up at a different point than ENOUGH_QUIET says");
});

/* ------------------------------------------------------------------ run */
for (const [name, fn] of tests) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    console.error("FAIL  " + name + "\n      " + err.message);
    process.exitCode = 1;
  }
}
console.log(passed + " of " + tests.length + " listening-backoff tests passed");
if (passed !== tests.length) process.exitCode = 1;
