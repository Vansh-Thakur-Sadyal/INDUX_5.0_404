/* Saarthi 2.0 - sign up, sign in, verify, reset.
 *
 * An OVERRIDE: build_landing.py copies the upstream site first and then lays this on
 * top, so a rebuild cannot quietly restore the template's version. The one it
 * replaces spoke to the earlier agritech auth service and expected a captcha, a
 * Google OAuth redirect and a /api/roles endpoint - none of which exist here.
 *
 * The session is an httpOnly cookie set by the server, so nothing here ever holds a
 * token: javascript cannot read that cookie, which is what stops a cross-site script
 * from stealing a login. `credentials: "same-origin"` is what sends it back.
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var DASHBOARD = "/app/";

  /* The pages carry several forms and show one at a time. */
  var FORMS = ["authForm", "otpForm", "signinOtpForm", "forgotForm",
               "resetOtpForm", "resetForm"];

  var pending = { identifier: "", purpose: "signup", code: "" };

  function show(name) {
    FORMS.forEach(function (id) {
      var el = $(id);
      if (el) el.classList.toggle("d-none", id !== name);
    });
  }

  /* Show a message on the card.
   *
   * The class names here have to match auth-app.css exactly, and for a long time
   * they did not: this wrote "auth-msg bad" while the stylesheet hides .auth-msg
   * unless it also carries .show, and colours it with .err rather than .bad. The
   * result was that EVERY message was invisible - a wrong captcha answer, a wrong
   * password, "signed in, opening your dashboard". Pressing Sign In looked like
   * pressing a dead button, which is exactly how it was reported, and worst on a
   * phone where the captcha is easiest to get wrong.
   *
   * It is also scrolled into view. On a 375-wide screen the button sits near the
   * bottom of the viewport and the message appears above the form, off-screen, so
   * showing it is not by itself enough to be seen.
   */
  function note(message, kind) {
    var box = $("authMsg");
    if (!box) {
      if (message) window.alert(message);
      return;
    }
    box.textContent = message || "";
    if (!message) {
      box.className = "auth-msg";
      return;
    }
    var tone = kind === "bad" ? "err" : (kind === "warn" ? "warn" : "ok");
    box.className = "auth-msg show " + tone;
    if (typeof box.scrollIntoView === "function") {
      box.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function busy(form, on, label) {
    var button = form && form.querySelector('button[type="submit"]');
    if (!button) return;
    if (on) {
      button.dataset.idle = button.dataset.idle || button.innerHTML;
      button.disabled = true;
      button.innerHTML = label || "Please wait...";
    } else {
      button.disabled = false;
      button.innerHTML = button.dataset.idle || button.innerHTML;
    }
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",          // carries the session cookie
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.text().then(function (text) {
        var data;
        try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
        if (!response.ok) {
          var error = new Error(data.detail || response.statusText);
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function value(id) {
    var el = $(id);
    return el ? String(el.value || "").trim() : "";
  }

  /* --------------------------------------------------------------- roles */
  /* Two roles exist and no more: a farmer, and an administrator. The tile is a
     routing hint only - the server decides what an account actually is, and says so
     when the two disagree. Letting the browser assert a role would be no security
     at all. */
  var ROLES = [
    { value: "user", label: "Farmer", blurb: "My farm, my fields, my calls",
      icon: "\u{1F33E}" },
    { value: "admin", label: "Administrator", blurb: "Every account on this server",
      icon: "\u{1F511}" }
  ];
  var chosenRole = "user";

  function paintRoles() {
    var host = $("roleTiles");
    if (!host) return;
    host.innerHTML = ROLES.map(function (r) {
      return '<button type="button" class="rolebtn' + (r.value === chosenRole ? " on" : "") +
        '" data-role="' + r.value + '"><span class="ri">' + r.icon + "</span>" +
        '<span class="rt"><b>' + r.label + "</b><span>" + r.blurb + "</span></span></button>";
    }).join("");
  }

  /* ------------------------------------------------------------- captcha */
  /* The question comes from the server and the ANSWER never does: the browser sends
     back the challenge id and what the person typed, and the server decides. A
     challenge is single-use, so every attempt needs a fresh one - which is why a
     failed sign-in asks a new question rather than leaving the old one on screen. */
  var challengeId = "";

  function newCaptcha() {
    var question = $("capQuestion") || $("forgotCapQuestion");
    if (!question) return Promise.resolve();
    return fetch("/api/auth/captcha", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        challengeId = body.challenge_id;
        ["capQuestion", "forgotCapQuestion"].forEach(function (id) {
          var el = $(id);
          if (el) el.textContent = body.question;
        });
        ["capAnswer", "forgotCapAnswer"].forEach(function (id) {
          var el = $(id);
          if (el) el.value = "";
        });
      })
      .catch(function () {
        var el = $("capQuestion");
        if (el) el.textContent = "Security check unavailable - try again in a moment.";
      });
  }

  function withCaptcha(body, fieldId) {
    var answer = value(fieldId || "capAnswer");
    return Object.assign({}, body, { challenge_id: challengeId, captcha: answer });
  }

  /* ------------------------------------------------------------- sign up */
  function signUp(form) {
    var body = {
      full_name: value("upName"),
      email: value("upEmail"),
      phone: value("upPhone"),
      password: value("upPassword")
    };
    busy(form, true, "Creating your account...");
    post("/api/auth/register", withCaptcha(body)).then(function (res) {
      pending.identifier = body.email;
      pending.purpose = "signup";
      note(res.message, res.verification && res.verification.sent ? "ok" : "bad");
      show("otpForm");
      // The village and district typed here are saved once the account is active.
      try {
        sessionStorage.setItem("saarthi.signup.profile", JSON.stringify({
          village: value("upVillage"), district: value("upDistrict"),
          state: value("upState")
        }));
      } catch (e) { /* private window */ }
    }).catch(function (err) {
      note(err.message, "bad");
    }).then(function () { busy(form, false); newCaptcha(); });
  }

  /* The second half of sign-in: the code, and only then a dashboard. */
  function signInVerify(form) {
    busy(form, true, "Checking...");
    post("/api/auth/login-verify", {
      identifier: pending.identifier,
      code: value("signinOtpCode"),
    }).then(function (res) {
      var role = (res.user && res.user.role) || "user";
      note("Signed in. Opening your dashboard...", "ok");
      location.href = role === "admin" ? DASHBOARD + "#admin" : DASHBOARD;
    }).catch(function (err) {
      note(err.message, "bad");
      busy(form, false);
    });
  }

  /* ------------------------------------------------------------- sign in */
  function signIn(form) {
    var body = { identifier: value("inIdentifier"), password: value("inPassword") };
    busy(form, true, "Signing in...");
    post("/api/auth/login", withCaptcha(body)).then(function (res) {
      /* The password is step one. A session only exists once the code comes back,
         so there is nothing to act on here except showing the box for it. */
      if (res.otp_required) {
        pending.identifier = body.identifier;
        pending.purpose = "signin";
        var to = document.getElementById("signinOtpTo");
        if (to) to.textContent = (res.verification && res.verification.to) || "your account";
        note(res.message || "Enter the 6-digit code we just sent you.", "ok");
        busy(form, false);
        show("signinOtpForm");
        return;
      }
      var role = (res.user && res.user.role) || "user";
      if (chosenRole === "admin" && role !== "admin") {
        note("Signed in, but this account is not an administrator. "
             + "Opening your own dashboard.", "bad");
        setTimeout(function () { location.href = DASHBOARD; }, 1800);
        return;
      }
      note("Signed in. Opening your dashboard...", "ok");
      location.href = role === "admin" ? DASHBOARD + "#admin" : DASHBOARD;
    }).catch(function (err) {
      if (err.status === 403 && /not verified/i.test(err.message)) {
        // The server has already sent a fresh code; just show the box for it.
        pending.identifier = body.identifier;
        pending.purpose = "signup";
        note(err.message, "bad");
        show("otpForm");
        return;
      }
      note(err.message, "bad");
    }).then(function () {
      busy(form, false);
      // A challenge is single-use whether the attempt succeeded or not, so a failed
      // sign-in has to be handed a new question - otherwise the next try is refused
      // with "already answered" and the person is stuck.
      newCaptcha();
    });
  }

  /* ---------------------------------------------------- verify a sign-up */
  function verify(form, codeFieldId) {
    busy(form, true, "Checking...");
    post("/api/auth/verify", {
      identifier: pending.identifier, code: value(codeFieldId)
    }).then(function () {
      // Carry the address typed on the sign-up page into the profile.
      var extra = null;
      try { extra = JSON.parse(sessionStorage.getItem("saarthi.signup.profile") || "null"); }
      catch (e) { extra = null; }
      var done = function () { location.href = DASHBOARD; };
      if (extra && (extra.village || extra.district)) {
        post("/api/profile", extra).then(done, done);
      } else {
        done();
      }
    }).catch(function (err) {
      note(err.message, "bad");
    }).then(function () { busy(form, false); newCaptcha(); });
  }

  function resend(purpose) {
    if (!pending.identifier) { note("Enter your email first.", "bad"); return; }
    post("/api/auth/resend", { identifier: pending.identifier, purpose: purpose })
      .then(function (res) {
        note("A new code is on its way to " + (res.verification.to || "you") + ".", "ok");
      })
      .catch(function (err) { note(err.message, "bad"); });
  }

  /* ------------------------------------------------------ forgot password */
  function forgot(form, fieldId) {
    var identifier = value(fieldId);
    if (!identifier) { note("Enter your email or mobile number.", "bad"); return; }
    busy(form, true, "Sending...");
    post("/api/auth/forgot",
         withCaptcha({ identifier: identifier }, "forgotCapAnswer"))
      .then(function (res) {
      pending.identifier = identifier;
      pending.purpose = "reset";
      note(res.message, "ok");
      show($("resetOtpForm") ? "resetOtpForm" : "resetForm");
    }).catch(function (err) {
      note(err.message, "bad");
    }).then(function () { busy(form, false); newCaptcha(); });
  }

  function resetWithCode() {
    // The code is checked by the same call that sets the password, so this step only
    // carries it forward - there is no separate "is this code right" endpoint.
    pending.code = value("resetOtpCode");
    if (!pending.code) { note("Enter the code we sent you.", "bad"); return; }
    note("");
    show("resetForm");
  }

  function setNewPassword(form) {
    var code = pending.code || value("resetToken") || value("resetOtpCode");
    busy(form, true, "Saving...");
    post("/api/auth/reset", {
      identifier: pending.identifier || value("resetEmail"),
      code: code,
      password: value("resetPassword")
    }).then(function (res) {
      note(res.message, "ok");
      show("authForm");
    }).catch(function (err) {
      note(err.message, "bad");
    }).then(function () { busy(form, false); newCaptcha(); });
  }

  /* ---------------------------------------------------------------- wire */
  document.addEventListener("DOMContentLoaded", function () {
    // Already signed in? Go straight through rather than asking again.
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (body) { if (body.signed_in) location.href = DASHBOARD; })
      .catch(function () { /* offline: let them try to sign in */ });

    paintRoles();

    var auth = $("authForm");
    if (auth) {
      auth.addEventListener("submit", function (e) {
        e.preventDefault();
        note("");
        if (auth.dataset.mode === "signup") signUp(auth); else signIn(auth);
      });
    }

    /* The two code boxes do different jobs: one finishes a signup, the other
       finishes a sign-in. They were both routed to the signup verifier, which is
       why a sign-in code had nowhere to go. */
    var signupOtp = $("otpForm");
    if (signupOtp) {
      signupOtp.addEventListener("submit", function (e) {
        e.preventDefault(); note(""); verify(signupOtp, "otpCode");
      });
    }
    var loginOtp = $("signinOtpForm");
    if (loginOtp) {
      loginOtp.addEventListener("submit", function (e) {
        e.preventDefault(); note(""); signInVerify(loginOtp);
      });
    }

    var forgotForm = $("forgotForm");
    if (forgotForm) {
      forgotForm.addEventListener("submit", function (e) {
        e.preventDefault(); note("");
        forgot(forgotForm, $("forgotIdentifier") ? "forgotIdentifier" : "forgotEmail");
      });
    }

    var resetOtp = $("resetOtpForm");
    if (resetOtp) {
      resetOtp.addEventListener("submit", function (e) {
        e.preventDefault(); resetWithCode();
      });
    }

    var resetForm = $("resetForm");
    if (resetForm) {
      resetForm.addEventListener("submit", function (e) {
        e.preventDefault(); note(""); setNewPassword(resetForm);
      });
    }

    var forgotLink = $("forgotLink");
    if (forgotLink) {
      forgotLink.addEventListener("click", function (e) {
        e.preventDefault(); note(""); show("forgotForm");
      });
    }

    document.addEventListener("click", function (e) {
      var role = e.target.closest("[data-role]");
      if (role) {
        e.preventDefault();
        chosenRole = role.getAttribute("data-role");
        paintRoles();
        return;
      }
      var again = e.target.closest("[data-resend]");
      if (again) { e.preventDefault(); resend(again.getAttribute("data-resend")); return; }
      var back = e.target.closest("[data-back-to-signin]");
      if (back) { e.preventDefault(); note(""); show("authForm"); }
    });

    newCaptcha();                 // the question on the form that is showing
    var refresh = $("capRefresh");
    if (refresh) {
      refresh.addEventListener("click", function (e) { e.preventDefault(); newCaptcha(); });
    }
    var forgotRefresh = $("forgotCapRefresh");
    if (forgotRefresh) {
      forgotRefresh.addEventListener("click", function (e) { e.preventDefault(); newCaptcha(); });
    }
  });
})();
