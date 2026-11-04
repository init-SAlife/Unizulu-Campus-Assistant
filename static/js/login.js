// Sign in / create account, then the emailed code
let mode = 'login';
let opts = {
  email_domain: 'gmail.com', needs_code: false,
  code_minutes: 10, per_day: 5, resend_seconds: 120, remember_days: {student: 5, admin: 1},
};

const form = document.getElementById('authForm');
const errorBox = document.getElementById('authError');
const submitBtn = document.getElementById('authSubmit');
const passwordInput = document.getElementById('password');
const emailInput = document.getElementById('email');
const typeSelect = document.getElementById('accountType');
const roleHint = document.getElementById('roleHint');

async function postJSON(url, body){
  const res = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin',
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  return {res, data};
}

function showError(message){
  errorBox.textContent = message;
  errorBox.hidden = !message;
  if(message){ errorBox.classList.remove('shake'); void errorBox.offsetWidth; errorBox.classList.add('shake'); }
  if(message) document.getElementById('authNotice').hidden = true;
}

function setMode(next){
  mode = next;
  const registering = mode === 'register';
  document.querySelectorAll('.auth-tab').forEach(tab => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.register-only').forEach(el => { el.hidden = !registering; });
  document.querySelectorAll('.login-only').forEach(el => { el.hidden = registering; });
  passwordInput.autocomplete = registering ? 'new-password' : 'current-password';
  passwordInput.placeholder = registering ? 'Choose a password' : 'Your password';
  document.getElementById('switchText').textContent = registering ? 'Already have an account?' : 'New here?';
  document.getElementById('switchMode').textContent = registering ? 'Sign in' : 'Create an account';
  showError('');
  updateRole();
  (registering ? document.getElementById('fullName') : emailInput).focus();
}

// only Gmail addresses can be used
const isGmail = email => (email.toLowerCase().split('@')[1] || '').trim() === opts.email_domain;
const gmailMessage = () => 'Please use a Gmail address (ending in @' + opts.email_domain + ').';

// account type and the admin request fields
function updateRole(){
  const registering = mode === 'register';
  const email = emailInput.value.trim();
  const known = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
  roleHint.classList.toggle('warn', known && !isGmail(email));
  roleHint.textContent = known && !isGmail(email) ? gmailMessage()
    : 'Choose Administrator to ask for an admin account. An existing admin must approve it.';

  const admin = registering && typeSelect.value === 'admin';
  document.getElementById('adminFields').hidden = !admin;
  document.getElementById('adminCodeField').hidden = !(admin && opts.needs_code);
  submitBtn.textContent = !registering ? 'Sign in' : admin ? 'Continue and request admin' : 'Continue';
}
emailInput.addEventListener('input', updateRole);
typeSelect.addEventListener('change', () => { showError(''); updateRole();
  if(typeSelect.value === 'admin') document.getElementById('adminReason').focus(); });

fetch('/auth/register-options').then(r => r.ok ? r.json() : null).then(data => {
  if(data) opts = Object.assign(opts, data);
  updateRole();
}).catch(() => {});

// "Forgotten password?" carries over the email that was already typed
document.getElementById('forgotLink').addEventListener('click', event => {
  const email = emailInput.value.trim();
  if(email){
    event.preventDefault();
    window.location.href = '/forgot-password?email=' + encodeURIComponent(email);
  }
});

document.querySelectorAll('.auth-tab').forEach(tab => tab.addEventListener('click', () => setMode(tab.dataset.mode)));
document.getElementById('switchMode').addEventListener('click', () => setMode(mode === 'login' ? 'register' : 'login'));

// step 1: the form
form.addEventListener('submit', async event => {
  event.preventDefault();
  const body = {email: emailInput.value.trim(), password: passwordInput.value};
  if(mode === 'register'){
    body.full_name = document.getElementById('fullName').value.trim();
    body.account_type = typeSelect.value;
    if(!body.full_name){ showError('Please enter your full name.'); return; }
    if(!body.email){ showError('Please enter your email.'); emailInput.focus(); return; }
    if(!body.account_type){ showError('Please choose your account type: Student or Administrator.'); typeSelect.focus(); return; }
    if(body.account_type === 'admin'){
      body.admin_reason = document.getElementById('adminReason').value.trim();
      if(body.admin_reason.length < 3){ showError('Please say what your role at UNIZULU is.'); document.getElementById('adminReason').focus(); return; }
      if(opts.needs_code){
        body.admin_code = document.getElementById('adminCode').value.trim();
        if(!body.admin_code){ showError('Please enter the admin access code.'); document.getElementById('adminCode').focus(); return; }
      }
    }
  }
  if(!body.email || !body.password){ showError('Please enter your email and password.'); return; }
  if(!isGmail(body.email)){ showError(gmailMessage()); emailInput.focus(); return; }
  if(mode === 'register' && body.password.length < 8){ showError('Your password must be at least 8 characters.'); return; }

  submitBtn.disabled = true;
  submitBtn.classList.add('is-busy');
  showError('');
  try{
    const {res, data} = await postJSON(mode === 'register' ? '/auth/register' : '/auth/login', body);
    if(!res.ok){ showError(data.error || 'Something went wrong. Please try again.'); return; }
    if(data.step === 'code'){ showCodeStep(data); return; }
    goOn(data);                      // a remembered device: signed in straight away
  } catch(err){
    showError('Could not reach the server. Please check your connection.');
    console.error(err);
  } finally {
    submitBtn.disabled = false;
    submitBtn.classList.remove('is-busy');
  }
});

// after signing in: admins go to their dashboard, "?next=" is honoured too
function goOn(data){
  if(data.admin_request && data.admin_request.status === 'pending'){ showWaitStep('pending'); return; }
  const next = new URLSearchParams(location.search).get('next');
  const safeNext = next && next.startsWith('/') && !next.startsWith('//') ? next : null;
  window.location.href = data.redirect || safeNext || '/';
}

// step 2: the emailed code
const codeStep = document.getElementById('codeStep');
const waitStep = document.getElementById('adminWaiting');
const codeForm = document.getElementById('codeForm');
const codeSubmit = document.getElementById('codeSubmit');
const boxes = Array.from(codeStep.querySelectorAll('.otp-box'));
let resendTimer = null;

function hideAuthForm(){
  form.hidden = true;
  document.querySelector('.auth-tabs').hidden = true;
  document.querySelector('.auth-switch').hidden = true;
}
function codeMessage(id, text){
  const el = document.getElementById(id);
  el.textContent = text || ''; el.hidden = !text;
  if(id === 'codeError' && text){
    document.getElementById('codeNotice').hidden = true;
    el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake');
  }
}

function showCodeStep(data){
  hideAuthForm(); waitStep.hidden = true; codeStep.hidden = false;
  document.getElementById('codeTitle').textContent = data.purpose === 'signup' ? 'Confirm your email' : 'Check your email';
  document.getElementById('codeEmail').textContent = data.email;
  document.getElementById('codeMinutes').textContent = data.minutes || opts.code_minutes;
  codeSubmit.textContent = data.purpose === 'signup' ? 'Verify and create account' : 'Verify and sign in';
  const days = data.remember_days || 0;
  document.getElementById('rememberRow').hidden = days <= 0;
  document.getElementById('rememberDays').textContent = days === 1 ? '1 day' : days + ' days';
  document.getElementById('rememberDevice').checked = false;
  codeMessage('codeError', ''); codeMessage('codeNotice', '');
  clearCode();
  showLimits(data);
  boxes[0].focus();
}

// "Send a new code" waits for the countdown; the server enforces the same rules
function showLimits(data){
  const left = typeof data.left === 'number' ? data.left : null;
  const perDay = data.per_day || opts.per_day;
  const leftBox = document.getElementById('codeLeft');
  if(left !== null){
    leftBox.textContent = left > 0
      ? `${left} of ${perDay} code emails left today`
      : `No code emails left today (${perDay} a day). You can ask again after midnight.`;
    leftBox.classList.toggle('none', left <= 0);
  }
  startCountdown(data.wait || 0, left === 0);
}

function startCountdown(seconds, noneLeft){
  const btn = document.getElementById('resendCode');
  const wait = document.getElementById('resendWait');
  clearInterval(resendTimer);
  let left = Math.max(0, Math.round(seconds));
  const tick = () => {
    if(noneLeft){ btn.disabled = true; wait.textContent = ''; clearInterval(resendTimer); return; }
    if(left <= 0){ clearInterval(resendTimer); btn.disabled = false; wait.textContent = ''; return; }
    btn.disabled = true;
    const m = Math.floor(left / 60), s = String(left % 60).padStart(2, '0');
    wait.textContent = `(in ${m}:${s})`;
    left--;
  };
  tick();
  resendTimer = setInterval(tick, 1000);
}

function codeValue(){ return boxes.map(b => b.value).join(''); }
function clearCode(){ boxes.forEach(b => { b.value = ''; b.classList.remove('filled', 'wrong'); }); codeSubmit.disabled = true; }
function fillFrom(index, digits){
  digits.split('').forEach((d, i) => {
    const box = boxes[index + i];
    if(box){ box.value = d; box.classList.add('filled'); }
  });
  boxes[Math.min(index + digits.length, boxes.length - 1)].focus();
  codeSubmit.disabled = codeValue().length !== 6;
}
boxes.forEach((box, i) => {
  box.addEventListener('input', () => {
    const digits = box.value.replace(/\D/g, '');
    box.value = '';
    box.classList.remove('filled', 'wrong');
    if(digits) fillFrom(i, digits.slice(0, 6 - i));
    codeSubmit.disabled = codeValue().length !== 6;
  });
  box.addEventListener('keydown', e => {
    if(e.key === 'Backspace' && !box.value && i > 0){
      boxes[i - 1].value = ''; boxes[i - 1].classList.remove('filled'); boxes[i - 1].focus(); e.preventDefault();
      codeSubmit.disabled = true;
    }
    if(e.key === 'ArrowLeft' && i > 0) boxes[i - 1].focus();
    if(e.key === 'ArrowRight' && i < boxes.length - 1) boxes[i + 1].focus();
    if(e.key === 'Enter' && codeValue().length === 6) codeForm.requestSubmit();
  });
  box.addEventListener('paste', e => {
    const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
    if(!digits) return;
    e.preventDefault();
    boxes.forEach(b => { b.value = ''; b.classList.remove('filled', 'wrong'); });
    fillFrom(0, digits);
  });
  box.addEventListener('focus', () => box.select());
});

codeForm.addEventListener('submit', async event => {
  event.preventDefault();
  const code = codeValue();
  if(code.length !== 6){ codeMessage('codeError', 'Please enter all 6 digits.'); return; }
  codeSubmit.disabled = true; codeSubmit.classList.add('is-busy');
  try{
    const {res, data} = await postJSON('/auth/code/verify', {code, remember: document.getElementById('rememberDevice').checked});
    if(!res.ok){
      if(data.restart){ backToForm(data.error); return; }
      codeMessage('codeError', data.error || 'That code is not correct.');
      boxes.forEach(b => b.classList.add('wrong'));
      setTimeout(() => { clearCode(); boxes[0].focus(); }, 450);
      return;
    }
    clearInterval(resendTimer);
    goOn(data);
  } catch(err){
    codeMessage('codeError', 'Could not reach the server. Please check your connection.');
  } finally {
    codeSubmit.classList.remove('is-busy');
    codeSubmit.disabled = codeValue().length !== 6;
  }
});

document.getElementById('resendCode').addEventListener('click', async () => {
  const btn = document.getElementById('resendCode');
  btn.disabled = true;
  try{
    const {res, data} = await postJSON('/auth/code/resend');
    if(!res.ok){
      if(data.restart){ backToForm(data.error); return; }
      codeMessage('codeError', data.error || 'Could not send a new code.');
      showLimits(data);
      return;
    }
    codeMessage('codeError', '');
    codeMessage('codeNotice', 'A new code is on its way. Only the newest code works.');
    clearCode(); boxes[0].focus();
    showLimits(data);
  } catch(err){
    codeMessage('codeError', 'Could not reach the server. Please try again.');
    btn.disabled = false;
  }
});

function backToForm(message){
  clearInterval(resendTimer);
  codeStep.hidden = true; waitStep.hidden = true;
  form.hidden = false;
  document.querySelector('.auth-tabs').hidden = false;
  document.querySelector('.auth-switch').hidden = false;
  showError(message || '');
}
document.getElementById('startAgain').addEventListener('click', () => backToForm(''));

// admin request: waiting for an existing admin
function showWaitStep(status){
  hideAuthForm(); codeStep.hidden = true; waitStep.hidden = false;
  const title = document.getElementById('waitTitle'), text = document.getElementById('waitText');
  if(status === 'rejected'){
    title.textContent = 'Admin request not approved';
    text.textContent = 'Your request for an admin account was not approved. You can still use the Campus AI Assistant with your account.';
  } else if(status === 'approved'){
    title.textContent = 'You are an admin';
    text.textContent = 'Your request was approved. Open the admin dashboard from the menu or your account card.';
  } else {
    title.textContent = 'Account created, request sent';
    text.textContent = "An administrator will look at your request. You'll get an email when it is decided. " +
      'Until then you can use the Campus AI Assistant with your account.';
  }
}

// "create an account" links from guest mode open the Create account tab
setMode(new URLSearchParams(location.search).get('mode') === 'register' ? 'register' : 'login');

// back from "Forgotten password?" after choosing a new password
if(new URLSearchParams(location.search).get('reset') === '1'){
  const notice = document.getElementById('authNotice');
  notice.textContent = 'Your password has been changed. Sign in with your new password.';
  notice.hidden = false;
  history.replaceState(null, '', '/login');
}

// /login?verify=admin (from the account card): show where the admin request is
if(new URLSearchParams(location.search).get('verify') === 'admin'){
  fetch('/auth/admin-request').then(r => r.ok ? r.json() : null).then(data => {
    if(data && data.admin_request) showWaitStep(data.admin_request.status);
  }).catch(() => {});
}
